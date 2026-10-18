"""Step 4 — EXPLAIN. Gemini writes the owner-facing explanation.

The grade and the improvement impacts are inputs (computed in step 3); the
model only writes words. A number guardrail checks every figure in the output
against the inputs; on violation we retry once with the error, then fall back
to a deterministic template.
"""

from __future__ import annotations

from app.config import settings
from app.gemini_client import GeminiStepFailure, gemini, load_prompt, render
from app.guardrail import allowed_numbers, check_payload
from app.schemas import (
    Assessment,
    ExplainLLMOutput,
    ExplainResult,
    GuardrailReport,
    ImprovementActionLLM,
    ImprovementItem,
    ScoreResult,
    Simulation,
    StepTrace,
    VerifyResult,
)


def score_payload(score: ScoreResult) -> dict:
    return {
        "grade": score.grade,
        "grade_label": score.grade_label,
        "total_points": score.total_points,
        "max_points": score.max_points,
        "pd_illustrative": score.pd_illustrative,
        "rate_band": score.rate_band,
        "decision_hint": score.decision_hint,
        "suggested_limit_mnt": score.suggested_limit_mnt,
        "suggested_tenor_months": score.suggested_tenor_months,
        "reference_monthly_installment_mnt": score.reference_monthly_installment_mnt,
        "factors": [
            {"label": f.label, "value": f.raw_display, "points": f.points, "max_points": f.max_points, "verification_status": f.verification_status, "reason_code": f.reason_code, "reason": f.reason}
            for f in score.factors
        ],
        "penalties": [{"code": p.code, "points": p.points, "detail": p.detail} for p in score.penalties],
        "scorecard_version": score.scorecard_version,
    }


def verification_payload(verify: VerifyResult, limit: int = 14) -> dict:
    return {
        "flags": [{"severity": f.severity, "message": f.message, "source": f.source} for f in verify.flags],
        "checks": [{"check_name": c.check_name, "status": c.status, "detail": c.detail} for c in verify.checks[:limit]],
        "verified_inputs": [{"name": v.name, "value": v.value, "status": v.status} for v in verify.verified_inputs],
    }


def sims_payload(sims: list[Simulation]) -> list[dict]:
    return [{"action_key": s.action_key, "label": s.label, "change": s.change, "expected_grade_impact": s.expected_grade_impact} for s in sims]


PLAIN_LABELS = {
    "months_documented": "the number of months of bank records you provided",
    "installment_coverage": "how comfortably your monthly inflows cover the loan installment",
    "inflow_stability": "how steady your monthly inflows are",
    "years_in_business": "how long you have been in business",
    "verified_share": "how much of what you declared could be verified against documents",
    "corroborating_sources": "how many independent records agree with each other",
}


def deterministic_explain(score: ScoreResult, sims: list[Simulation], verify: VerifyResult) -> ExplainLLMOutput:
    top = max(score.factors, key=lambda f: f.points)
    low = min(score.factors, key=lambda f: f.points / f.max_points)
    n_flags = len([f for f in verify.flags if f.severity != "info"])
    summary = (
        f"Your Kervan passport grade is {score.grade} ({score.grade_label}). "
        f"What helped most was {PLAIN_LABELS.get(top.key, top.label.lower())}. What held the grade back was {PLAIN_LABELS.get(low.key, low.label.lower())}. "
        + (f"{n_flags} verification flag(s) were raised and are listed on your passport. " if n_flags else "No verification flags were raised. ")
        + "The steps below were tested by re-running the scorecard, so the expected gains are computed, not guessed."
    )
    summary_mn = (
        f"Таны Kervan зээлийн паспортын үнэлгээ {score.grade} ({score.grade_label}). "
        "Доорх алхмуудыг оноо тооцох системээр дахин тооцоолсон тул ахиц бодит тоо юм."
    )
    plan = [ImprovementActionLLM(action_key=s.action_key, action=s.label, why=f"This changes {s.change}.") for s in sims]
    return ExplainLLMOutput(owner_summary=summary, owner_summary_mn=summary_mn, improvement_plan=plan)


def assemble(out: ExplainLLMOutput, sims: list[Simulation], guardrail: GuardrailReport) -> ExplainResult:
    by_key = {p.action_key: p for p in out.improvement_plan}
    items: list[ImprovementItem] = []
    for s in sims:  # order and impact always come from the simulator
        p = by_key.get(s.action_key)
        items.append(
            ImprovementItem(
                action_key=s.action_key,
                action=p.action if p else s.label,
                why=p.why if p else f"This changes {s.change}.",
                expected_grade_impact=s.expected_grade_impact,
                points_gain=s.points_gain,
                simulated_grade=s.new_grade,
                simulated_points=s.new_points,
            )
        )
    return ExplainResult(owner_summary=out.owner_summary, owner_summary_mn=out.owner_summary_mn, improvement_plan=items, guardrail=guardrail)


async def explain(a: Assessment) -> tuple[ExplainResult, StepTrace]:
    assert a.score and a.verify
    s_payload = score_payload(a.score)
    v_payload = verification_payload(a.verify)
    sim_payload = sims_payload(a.simulations)
    allowed = allowed_numbers({"score": s_payload, "verification": v_payload, "sims": sim_payload, "metrics": a.verify.metrics})
    template, version = load_prompt("explain")
    prompt = render(template, score=s_payload, verification=v_payload, simulations=sim_payload)

    if settings.offline:
        from app import offline

        out, trace = offline.explain(a.score, a.simulations, version)
        return assemble(out, a.simulations, GuardrailReport(numbers_checked=0, note="offline fixture")), trace

    guardrail = GuardrailReport()
    try:
        out, trace = await gemini.generate_json(
            step="explain", prompt_name="explain", prompt_version=version, prompt_text=prompt,
            schema=ExplainLLMOutput, input_refs=["score", "verification", "simulations"], temperature=0.4,
        )
        checked, violations = check_payload(out.model_dump(), allowed)
        guardrail.numbers_checked = checked
        if violations:
            retry_prompt = prompt + (
                "\n\nGUARDRAIL VIOLATION in your previous answer: these numbers do not exist in the inputs: "
                + ", ".join(violations[:10])
                + ". Rewrite the texts without inventing numbers; only use figures given above."
            )
            out2, trace2 = await gemini.generate_json(
                step="explain", prompt_name="explain", prompt_version=version, prompt_text=retry_prompt,
                schema=ExplainLLMOutput, input_refs=["score", "verification", "simulations"], temperature=0.2, use_cache=False,
            )
            trace.attempts += trace2.attempts
            trace.latency_ms += trace2.latency_ms
            checked2, violations2 = check_payload(out2.model_dump(), allowed)
            guardrail.numbers_checked = checked2
            if violations2:
                guardrail.violations = violations2
                guardrail.fallback_used = True
                guardrail.note = "Model text replaced by deterministic template after two guardrail failures"
                trace.note = "guardrail fallback: " + ", ".join(violations2[:5])
                out = deterministic_explain(a.score, a.simulations, a.verify)
            else:
                out = out2
                guardrail.note = f"Retry fixed invented numbers: {', '.join(violations[:5])}"
                trace.note = guardrail.note
        else:
            guardrail.note = "All numbers in the explanation exist in the inputs"
    except GeminiStepFailure as exc:
        trace = exc.trace
        trace.note = "Gemini unavailable; deterministic explanation used"
        guardrail = GuardrailReport(fallback_used=True, note="Gemini call failed; deterministic template used")
        out = deterministic_explain(a.score, a.simulations, a.verify)
    return assemble(out, a.simulations, guardrail), trace
