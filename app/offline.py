"""Developer-only fixture mode (KERVAN_OFFLINE=1).

Lets the pipeline, SSE stream and UI be exercised without a Gemini key, e.g.
in CI or on a plane. It is NOT the product: READ uses the demo ground-truth
files / deterministic CSV sums, VERIFY/EXPLAIN/WRITE return templated text.
The Agent-trust view labels every trace from this mode as "offline-fixture".
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from app.config import settings
from app.schemas import (
    CreditMemo,
    EvidenceItem,
    ExplainLLMOutput,
    ExtractedField,
    ImprovementActionLLM,
    MonthlyFigure,
    ReadResult,
    StepTrace,
    UploadedDoc,
    VerifiedInput,
    VerifyCheck,
    VerifyLLMOutput,
)
from app.tabular import monthly_totals_from_csv, pick_inflow_column, pick_outflow_column

MODEL_NAME = "offline-fixture"


def _trace(step: str, version: str, refs: list[str]) -> StepTrace:
    return StepTrace(step=step, prompt_name=step, prompt_version=version, model=MODEL_NAME, latency_ms=1, schema_valid=True,
                     attempts=0, cached=False, input_refs=refs, note="offline fixture mode — no Gemini call (developer use only)")


def _find_demo_match(sha256: str) -> tuple[Path, str] | None:
    if not settings.fixtures_dir.exists():
        return None
    for pdir in settings.fixtures_dir.iterdir():
        if not pdir.is_dir():
            continue
        for f in pdir.iterdir():
            if f.suffix.lower() in {".jpg", ".png", ".pdf", ".csv"}:
                if hashlib.sha256(f.read_bytes()).hexdigest() == sha256:
                    return pdir, f.name
    return None


def read(doc: UploadedDoc, data: bytes, prompt_version: str) -> tuple[ReadResult, StepTrace]:
    name = doc.filename.lower()
    match = _find_demo_match(doc.sha256)
    trace = _trace("read", prompt_version, [doc.filename])
    if match:
        pdir, _ = match
        truth = json.loads((pdir / "truth.json").read_text(encoding="utf-8"))
        declared = json.loads((pdir / "declared.json").read_text(encoding="utf-8"))
        if "ledger" in name and "ledger" in truth:
            L = truth["ledger"]
            return ReadResult(
                document_type="sales_ledger", language="mn", period_start=L["months"][0]["month"], period_end=L["months"][-1]["month"],
                monthly_figures=[MonthlyFigure(month=m["month"], sales_or_inflows_mnt=m["sales_mnt"], expenses_or_outflows_mnt=m["expenses_mnt"], source_document=doc.filename, page_or_row=f"page 1 row {m['row']}") for m in L["months"]],
                extracted_fields=[
                    ExtractedField(field="document_title", value="Борлуулалтын дэвтэр", source_document=doc.filename, page_or_row="page 1", confidence=0.9),
                    ExtractedField(field="business_name", value=declared["business_name"], source_document=doc.filename, page_or_row="page 1", confidence=0.85),
                    ExtractedField(field="period_total_sales", value=str(L["total_sales_mnt"]), source_document=doc.filename, page_or_row="page 1 totals", confidence=0.8),
                    ExtractedField(field="period_total_expenses", value=str(L["total_expenses_mnt"]), source_document=doc.filename, page_or_row="page 1 totals", confidence=0.8),
                ],
                quality_notes=["handwritten", "offline fixture: values from demo ground truth"],
            ), trace
        if name.endswith(".pdf") and "bank_monthly" in truth:
            B = truth["bank_monthly"]
            return ReadResult(
                document_type="bank_statement", language="en", period_start=B[0]["month"], period_end=B[-1]["month"],
                monthly_figures=[MonthlyFigure(month=m["month"], sales_or_inflows_mnt=m["credits"], expenses_or_outflows_mnt=m["debits"], source_document=doc.filename, page_or_row="last page, monthly summary") for m in B],
                extracted_fields=[
                    ExtractedField(field="account_holder", value=declared["business_name"], source_document=doc.filename, page_or_row="page 1", confidence=0.98),
                    ExtractedField(field="bank_name", value="Khangai Bank", source_document=doc.filename, page_or_row="page 1", confidence=0.98),
                    ExtractedField(field="total_credits", value=str(sum(m["credits"] for m in B)), source_document=doc.filename, page_or_row="last page", confidence=0.95),
                ],
                quality_notes=["offline fixture: values from demo ground truth"],
            ), trace
    if doc.mime_type.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
        totals = monthly_totals_from_csv(text)
        fields: list[ExtractedField] = []
        try:
            for i, row in enumerate(csv.reader(io.StringIO(text)), start=1):
                if len(row) == 2 and row[0].strip() and row[1].strip():
                    fields.append(ExtractedField(field=row[0].strip().lower().replace(" ", "_"), value=row[1].strip(), source_document=doc.filename, page_or_row=f"row {i}", confidence=0.99))
                if i > 12:
                    break
        except csv.Error:
            pass
        doc_type = "payment_export" if ("payment" in name or "settlement" in name) else "bank_statement"
        figs: list[MonthlyFigure] = []
        if totals:
            in_col = pick_inflow_column(totals["columns"])
            out_col = pick_outflow_column(totals["columns"])
            for m, cols in totals["months"].items():
                figs.append(MonthlyFigure(month=m, sales_or_inflows_mnt=cols.get(in_col, 0.0), expenses_or_outflows_mnt=cols.get(out_col) if out_col else None,
                                          source_document=doc.filename, page_or_row=totals["rows"][m]))
            if doc_type == "bank_statement":
                fields.append(ExtractedField(field="total_credits", value=str(int(sum(f.sales_or_inflows_mnt for f in figs))), source_document=doc.filename, page_or_row="computed", confidence=0.99))
        return ReadResult(document_type=doc_type, language="en", period_start=figs[0].month if figs else "", period_end=figs[-1].month if figs else "",
                          monthly_figures=figs, extracted_fields=fields, quality_notes=["offline fixture: deterministic CSV sums"]), trace
    return ReadResult(document_type="other", language="und", period_start="", period_end="", monthly_figures=[], extracted_fields=[],
                      quality_notes=["offline mode cannot read images or PDFs that are not demo files"]), trace


def verify(det: dict, prompt_version: str) -> tuple[VerifyLLMOutput, StepTrace]:
    out = VerifyLLMOutput(
        checks=[VerifyCheck(check_name="model_qualitative_review", status="unverified", detail="Gemini qualitative review skipped (offline fixture mode).", evidence_refs=[], source="gemini")],
        flags=[],
        verified_inputs=[VerifiedInput(name=v.name, value=v.value, status=v.status, basis="offline: deterministic status kept") for v in det["inputs"]],
    )
    return out, _trace("verify", prompt_version, [])


def explain(score, sims, prompt_version: str) -> tuple[ExplainLLMOutput, StepTrace]:
    from app.steps.explain import PLAIN_LABELS

    top = max(score.factors, key=lambda f: f.points)
    low = min(score.factors, key=lambda f: f.points / f.max_points)
    summary = (f"Your Kervan passport grade is {score.grade} ({score.grade_label}). "
               f"What helped most was {PLAIN_LABELS.get(top.key, top.label.lower())}. What held the grade back was {PLAIN_LABELS.get(low.key, low.label.lower())}. "
               "The steps below were tested by re-running the scorecard, so the gains are real numbers, not guesses. (Offline fixture text.)")
    summary_mn = (f"Таны Kervan зээлийн паспортын үнэлгээ {score.grade} ({score.grade_label}). "
                  "Доорх алхмуудыг оноо тооцох системээр дахин тооцоолсон тул ахиц бодит тоо юм.")
    plan = [ImprovementActionLLM(action_key=s.action_key, action=s.label, why=f"This changes: {s.change}.") for s in sims]
    return ExplainLLMOutput(owner_summary=summary, owner_summary_mn=summary_mn, improvement_plan=plan), _trace("explain", prompt_version, [])


def write(declared, score, verify_result, evidence: list[dict], prompt_version: str) -> tuple[CreditMemo, StepTrace]:
    flags = [f for f in verify_result.flags if f.severity != "info"]
    memo = CreditMemo(
        title=f"Credit memo — {declared.business_name}",
        business_summary=f"{declared.business_name} is a {declared.sector.lower()} in {declared.location}, operating for {declared.years_in_business:g} years. "
                         f"It requests ₮{declared.loan_amount_mnt:,.0f} over {declared.tenor_months} months for: {declared.purpose}. (Offline fixture text.)",
        cash_flow_analysis=f"Documented months: {verify_result.metrics.get('months_documented')}. Average monthly inflow ₮{verify_result.metrics.get('avg_monthly_inflow_mnt', 0):,.0f}; "
                           f"coefficient of variation {verify_result.metrics.get('inflow_cv')}. Reference installment ₮{score.reference_monthly_installment_mnt:,.0f}.",
        verification_status="; ".join(f"{c.check_name}: {c.status}" for c in verify_result.checks[:8]),
        key_risks=[f.message for f in flags] or ["No warning or critical flags raised by deterministic checks."],
        mitigants=["Bank-documented cash flow history", "Deterministic verification of declared figures"],
        scorecard_result=f"Grade {score.grade} ({score.grade_label}), {score.total_points:.0f}/{score.max_points:.0f} points, illustrative PD {score.pd_illustrative:.1%}, rate band {score.rate_band}.",
        suggested_limit_and_tenor=f"Suggested limit ₮{score.suggested_limit_mnt:,.0f} over {score.suggested_tenor_months} months; decision hint: {score.decision_hint}. Draft for human review.",
        evidence_appendix=[EvidenceItem(figure=e["figure"], value=e["value"], source=e["source"]) for e in evidence[:10]],
    )
    return memo, _trace("write", prompt_version, [])
