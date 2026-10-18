"""Step 3 — SCORE. Deterministic, auditable, no LLM.

Everything here is pure Python over `ScorerInputs` and the tables in
`app/scorecard_config.py`. The same function is re-run by the improvement
simulator so that "expected grade impact" is computed, never guessed.
"""

from __future__ import annotations

import math
from typing import Optional

from app import scorecard_config as cfg
from app.schemas import (
    DeclaredInfo,
    FlagRef,
    ScoreFactor,
    ScorePenalty,
    ScoreResult,
    ScorerInputs,
    Simulation,
    VerifyResult,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def monthly_installment(principal: float, annual_rate: float, months: int) -> float:
    """Standard annuity installment. Falls back to straight-line if rate is 0."""
    if months <= 0:
        return float(principal)
    r = annual_rate / 12.0
    if r <= 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** (-months))


def _pick_band(bands, value: float, lower_is_better: bool):
    for threshold, points, code, text in bands:
        if lower_is_better:
            if value <= threshold:
                return points, code, text
        else:
            if value >= threshold:
                return points, code, text
    threshold, points, code, text = bands[-1]
    return points, code, text


def _grade_row(total: float) -> dict:
    for row in cfg.GRADES:
        if total >= row["min_points"]:
            return row
    return cfg.GRADES[-1]


def fmt_mnt(value: float) -> str:
    return f"₮{value:,.0f}"


# --------------------------------------------------------------------------- #
# Scorer
# --------------------------------------------------------------------------- #
def compute_score(inputs: ScorerInputs) -> ScoreResult:
    installment = monthly_installment(inputs.loan_amount_mnt, cfg.REFERENCE_ANNUAL_RATE, inputs.tenor_months)
    coverage = (inputs.avg_monthly_inflow_mnt / installment) if installment > 0 else 0.0

    # Share of declared figures that are verified (weighted by status).
    declared_statuses = [inputs.declared_monthly_revenue_status, inputs.years_in_business_status]
    verified_share = sum(cfg.STATUS_WEIGHTS[s] for s in declared_statuses) / len(declared_statuses)

    factor_values: dict[str, tuple[Optional[float], str, str]] = {
        # key: (raw_value, raw_display, status)
        "months_documented": (inputs.months_documented, f"{inputs.months_documented:.0f} months", inputs.months_documented_status),
        "installment_coverage": (coverage, f"{coverage:.1f}x (inflow {fmt_mnt(inputs.avg_monthly_inflow_mnt)} / installment {fmt_mnt(installment)})", inputs.avg_monthly_inflow_status),
        "inflow_stability": (inputs.inflow_cv, f"CV {inputs.inflow_cv:.2f}", inputs.inflow_cv_status),
        "years_in_business": (inputs.years_in_business, f"{inputs.years_in_business:g} years", inputs.years_in_business_status),
        "verified_share": (verified_share, f"{verified_share:.0%} of declared figures verified", "verified"),
        "corroborating_sources": (inputs.corroborating_sources, f"{inputs.corroborating_sources:.0f} consistent sources", inputs.corroborating_sources_status),
    }

    factors: list[ScoreFactor] = []
    total = 0.0
    for spec in cfg.FACTORS:
        raw, display, status = factor_values[spec["key"]]
        if spec["key"] == "inflow_stability" and inputs.months_documented < 3:
            base_points, code, text = 2.0, "ST_NA", "Too few months to assess stability"
        elif spec.get("proportional"):
            _, code, text = _pick_band(spec["bands"], raw, spec["lower_is_better"])
            base_points = round(raw * spec["max_points"], 1)
        else:
            base_points, code, text = _pick_band(spec["bands"], raw, spec["lower_is_better"])
        weight = cfg.STATUS_WEIGHTS[status]
        points = round(base_points * weight, 1)
        total += points
        factors.append(
            ScoreFactor(
                key=spec["key"],
                label=spec["label"],
                raw_value=None if raw is None else round(float(raw), 4),
                raw_display=display,
                base_points=float(base_points),
                points=points,
                max_points=float(spec["max_points"]),
                verification_status=status,
                weight_applied=weight,
                reason_code=code,
                reason=text if weight == 1.0 else f"{text}; counted at {weight:.0%} because input is {status.replace('_', ' ')}",
            )
        )

    penalties: list[ScorePenalty] = []
    penalty_total = 0.0
    for flag in inputs.flags:
        pts = cfg.PENALTIES.get(flag.severity, 0.0)
        if pts == 0.0:
            continue
        penalty_total += pts
        penalties.append(ScorePenalty(code=flag.code or f"FLAG_{flag.severity.upper()}", label=f"{flag.severity.capitalize()} flag", points=pts, detail=f"{flag.severity} inconsistency flag"))
    if penalty_total < cfg.PENALTIES["cap"]:
        penalties.append(ScorePenalty(code="PEN_CAP", label="Penalty cap applied", points=cfg.PENALTIES["cap"] - penalty_total, detail="Total penalties are capped"))
        penalty_total = cfg.PENALTIES["cap"]

    total = round(max(0.0, min(float(cfg.MAX_POINTS), total + penalty_total)), 1)
    row = _grade_row(total)

    if row["rate"]:
        rate_min, rate_max = row["rate"]
        rate_band = f"{rate_min:.0%}–{rate_max:.0%} p.a."
    else:
        rate_min = rate_max = None
        rate_band = "No standard offer (refer or decline)"

    verified_inflow = inputs.avg_monthly_inflow_mnt * cfg.STATUS_WEIGHTS[inputs.avg_monthly_inflow_status]
    suggested_limit = min(inputs.loan_amount_mnt, row["limit_multiple"] * verified_inflow)
    suggested_limit = math.floor(suggested_limit / 100_000) * 100_000 if suggested_limit > 0 else 0.0
    suggested_tenor = min(inputs.tenor_months, row["max_tenor"])

    return ScoreResult(
        scorecard_version=cfg.SCORECARD_VERSION,
        total_points=total,
        max_points=float(cfg.MAX_POINTS),
        grade=row["grade"],
        grade_label=row["label"],
        pd_illustrative=row["pd"],
        rate_band=rate_band,
        rate_min=rate_min,
        rate_max=rate_max,
        decision_hint=row["decision"],
        suggested_limit_mnt=float(suggested_limit),
        suggested_tenor_months=int(suggested_tenor),
        reference_monthly_installment_mnt=round(installment, 0),
        factors=factors,
        penalties=penalties,
        inputs=inputs,
    )


# --------------------------------------------------------------------------- #
# Verify -> scorer inputs
# --------------------------------------------------------------------------- #
def inputs_from_verification(verify: VerifyResult, declared: DeclaredInfo) -> ScorerInputs:
    """Translate the verified inputs produced by step 2 into scorer inputs.

    Values come from the deterministic checks; statuses are the merged
    (most conservative) statuses. Nothing identifying is carried over.
    """
    m = verify.input_map()

    def val(name: str, default: float) -> float:
        return float(m[name].value) if name in m else default

    def status(name: str, default: str = "unverified") -> str:
        return m[name].status if name in m else default  # type: ignore[return-value]

    ledger_ratio = val("ledger_to_bank_ratio", 0.0) or None
    return ScorerInputs(
        months_documented=val("months_documented", 0),
        months_documented_status=status("months_documented"),
        avg_monthly_inflow_mnt=val("avg_monthly_inflow_mnt", 0),
        avg_monthly_inflow_status=status("avg_monthly_inflow_mnt"),
        inflow_cv=val("inflow_cv", 1.0),
        inflow_cv_status=status("inflow_cv"),
        years_in_business=val("years_in_business", declared.years_in_business),
        years_in_business_status=status("years_in_business"),
        declared_monthly_revenue_mnt=val("declared_monthly_revenue_mnt", declared.monthly_revenue_mnt),
        declared_monthly_revenue_status=status("declared_monthly_revenue_mnt"),
        corroborating_sources=val("corroborating_sources", 0),
        corroborating_sources_status=status("corroborating_sources", "verified"),
        ledger_to_bank_ratio=ledger_ratio,
        loan_amount_mnt=declared.loan_amount_mnt,
        tenor_months=declared.tenor_months,
        flags=[FlagRef(code=f.code or f.message[:40], severity=f.severity) for f in verify.flags],
    )


# --------------------------------------------------------------------------- #
# Improvement simulator (re-runs the scorer; never guesses)
# --------------------------------------------------------------------------- #
def _impact_text(base: ScoreResult, new: ScoreResult) -> str:
    gain = new.total_points - base.total_points
    sign = "+" if gain >= 0 else ""
    if new.grade != base.grade:
        return f"{base.grade} → {new.grade} ({sign}{gain:.1f} pts)"
    return f"stays {base.grade} ({sign}{gain:.1f} pts)"


def simulate_improvements(inputs: ScorerInputs, base: Optional[ScoreResult] = None) -> list[Simulation]:
    base = base or compute_score(inputs)
    candidates: list[tuple[str, str, str, ScorerInputs]] = []
    sim = cfg.SIMULATION

    if inputs.months_documented < sim["full_history_months"]:
        alt = inputs.model_copy(deep=True)
        alt.months_documented = sim["full_history_months"]
        alt.months_documented_status = "verified"
        if alt.avg_monthly_inflow_status == "partially_verified":
            alt.avg_monthly_inflow_status = "verified"
        if alt.inflow_cv_status == "partially_verified":
            alt.inflow_cv_status = "verified"
        candidates.append(("extend_history", f"Provide {sim['full_history_months']} months of bank statements",
                           f"months_documented {inputs.months_documented:.0f} → {sim['full_history_months']}", alt))

    if inputs.declared_monthly_revenue_status != "verified":
        alt = inputs.model_copy(deep=True)
        alt.declared_monthly_revenue_status = "verified"
        alt.flags = [f for f in alt.flags if f.code not in {"DECLARED_VS_BANK", "DECLARED_VS_BANK_CRITICAL"}]
        candidates.append(("resolve_declared_gap", "Align the declared monthly revenue with bank-documented inflows",
                           "declared revenue status → verified; declared-vs-bank flag cleared", alt))

    if inputs.ledger_to_bank_ratio and inputs.ledger_to_bank_ratio > 1.15 and inputs.avg_monthly_inflow_mnt > 0:
        alt = inputs.model_copy(deep=True)
        uplift = min(inputs.ledger_to_bank_ratio, sim["route_cash_cap_ratio"])
        alt.avg_monthly_inflow_mnt = round(inputs.avg_monthly_inflow_mnt * uplift, 0)
        candidates.append(("route_sales_through_bank", "Deposit cash sales into the business bank account",
                           f"avg monthly inflow {fmt_mnt(inputs.avg_monthly_inflow_mnt)} → {fmt_mnt(alt.avg_monthly_inflow_mnt)}", alt))

    coverage_factor = next(f for f in base.factors if f.key == "installment_coverage")
    if coverage_factor.base_points < coverage_factor.max_points and inputs.loan_amount_mnt > 0:
        alt = inputs.model_copy(deep=True)
        alt.loan_amount_mnt = round(inputs.loan_amount_mnt * sim["reduce_loan_factor"], -5)
        candidates.append(("reduce_loan_amount", f"Request {fmt_mnt(alt.loan_amount_mnt)} instead of {fmt_mnt(inputs.loan_amount_mnt)}",
                           f"loan amount {fmt_mnt(inputs.loan_amount_mnt)} → {fmt_mnt(alt.loan_amount_mnt)}", alt))
        next_tenor = next((t for t in sim["tenor_steps"] if t > inputs.tenor_months), None)
        if next_tenor:
            alt2 = inputs.model_copy(deep=True)
            alt2.tenor_months = next_tenor
            candidates.append(("extend_tenor", f"Extend the tenor from {inputs.tenor_months} to {next_tenor} months",
                               f"tenor {inputs.tenor_months} → {next_tenor} months", alt2))

    if inputs.corroborating_sources < 3:
        alt = inputs.model_copy(deep=True)
        alt.corroborating_sources = inputs.corroborating_sources + 1
        candidates.append(("add_corroborating_source", "Add another independent record (payment-provider export or sales ledger)",
                           f"corroborating sources {inputs.corroborating_sources:.0f} → {alt.corroborating_sources:.0f}", alt))

    results: list[Simulation] = []
    for key, label, change, alt in candidates:
        new = compute_score(alt)
        gain = round(new.total_points - base.total_points, 1)
        if gain <= 0:
            continue
        results.append(
            Simulation(
                action_key=key,
                label=label,
                change=change,
                base_points=base.total_points,
                new_points=new.total_points,
                points_gain=gain,
                base_grade=base.grade,
                new_grade=new.grade,
                expected_grade_impact=_impact_text(base, new),
            )
        )
    results.sort(key=lambda s: s.points_gain, reverse=True)
    return results[:5]
