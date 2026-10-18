"""Pydantic v2 models.

Two families live here:

* **Gemini-facing output schemas** (ReadResult, VerifyLLMOutput, ExplainLLMOutput,
  CreditMemo). These are passed to Gemini as `response_schema`, so they stay
  flat and simple: no dicts, no unions, only lists of objects, strings, numbers
  and enums (Literal).
* **Internal models** (ScoreResult, Assessment, StepTrace, ...) that the API
  returns to the UI and that are persisted as JSON.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

CheckStatus = Literal["verified", "partially_verified", "unverified", "inconsistent"]
Severity = Literal["info", "warning", "critical"]
DocumentType = Literal["sales_ledger", "bank_statement", "payment_export", "other"]

# Ordering used when merging statuses: the more conservative status wins.
STATUS_RANK: dict[str, int] = {
    "verified": 0,
    "partially_verified": 1,
    "unverified": 2,
    "inconsistent": 3,
}


def more_conservative(a: str, b: str) -> str:
    return a if STATUS_RANK.get(a, 2) >= STATUS_RANK.get(b, 2) else b


# --------------------------------------------------------------------------- #
# Step 1 — READ
# --------------------------------------------------------------------------- #
class MonthlyFigure(BaseModel):
    month: str = Field(description="Calendar month in YYYY-MM format")
    sales_or_inflows_mnt: float = Field(description="Total sales (ledger) or credits/inflows (bank, payments) in MNT")
    expenses_or_outflows_mnt: Optional[float] = Field(default=None, description="Total expenses or debits in MNT, null if not shown")
    source_document: str = Field(description="File name of the document this figure came from")
    page_or_row: str = Field(description="Page number, row number or row range where the figure is shown")


class ExtractedField(BaseModel):
    field: str = Field(description="Snake_case name, e.g. business_name, account_opened, total_credits, period_total_sales")
    value: str = Field(description="The value exactly as read; numbers as digits without currency symbol")
    source_document: str
    page_or_row: str
    confidence: float = Field(description="0.0 to 1.0 confidence that the value was read correctly")

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


class ReadResult(BaseModel):
    document_type: DocumentType
    language: str = Field(description="ISO 639-1 code of the document language, e.g. mn, en, ru")
    period_start: str = Field(description="YYYY-MM-DD or YYYY-MM of the first period covered")
    period_end: str = Field(description="YYYY-MM-DD or YYYY-MM of the last period covered")
    monthly_figures: list[MonthlyFigure]
    extracted_fields: list[ExtractedField]
    quality_notes: list[str] = Field(description="Observations such as blurry, cropped, handwritten, missing months, totals not shown")


class DocumentRead(BaseModel):
    """ReadResult plus the document it belongs to."""

    doc_id: str
    filename: str
    mime_type: str
    result: ReadResult


# --------------------------------------------------------------------------- #
# Step 2 — VERIFY
# --------------------------------------------------------------------------- #
class VerifyCheck(BaseModel):
    check_name: str
    status: CheckStatus
    detail: str
    evidence_refs: list[str] = Field(default_factory=list, description="References like 'bank_statement.csv row 14' or 'ledger.jpg page 1'")
    source: Literal["deterministic", "gemini"] = "gemini"


class VerifyFlag(BaseModel):
    severity: Severity
    message: str
    evidence_refs: list[str] = Field(default_factory=list)
    source: Literal["deterministic", "gemini"] = "gemini"
    code: str = ""


class VerifiedInput(BaseModel):
    name: str = Field(description="One of: months_documented, avg_monthly_inflow_mnt, inflow_cv, years_in_business, declared_monthly_revenue_mnt, corroborating_sources, ledger_to_bank_ratio")
    value: float
    status: CheckStatus
    basis: str = Field(description="One sentence on how the value was derived and why it has this status")


class VerifyLLMOutput(BaseModel):
    """What Gemini returns for the VERIFY step."""

    checks: list[VerifyCheck]
    flags: list[VerifyFlag]
    verified_inputs: list[VerifiedInput]


class VerifyResult(BaseModel):
    """Merged deterministic + Gemini verification."""

    checks: list[VerifyCheck]
    flags: list[VerifyFlag]
    verified_inputs: list[VerifiedInput]
    metrics: dict[str, Any] = Field(default_factory=dict)
    monthly_series: list[dict] = Field(default_factory=list)
    llm_notes: list[str] = Field(default_factory=list)

    def input_map(self) -> dict[str, VerifiedInput]:
        return {v.name: v for v in self.verified_inputs}


# --------------------------------------------------------------------------- #
# Step 3 — SCORE (deterministic)
# --------------------------------------------------------------------------- #
class FlagRef(BaseModel):
    code: str
    severity: Severity


class ScorerInputs(BaseModel):
    """Exactly what the scorer is allowed to see. No names, no protected attributes."""

    months_documented: float = 0
    months_documented_status: CheckStatus = "unverified"
    avg_monthly_inflow_mnt: float = 0
    avg_monthly_inflow_status: CheckStatus = "unverified"
    inflow_cv: float = 1.0
    inflow_cv_status: CheckStatus = "unverified"
    years_in_business: float = 0
    years_in_business_status: CheckStatus = "unverified"
    declared_monthly_revenue_mnt: float = 0
    declared_monthly_revenue_status: CheckStatus = "unverified"
    corroborating_sources: float = 0
    corroborating_sources_status: CheckStatus = "verified"
    ledger_to_bank_ratio: Optional[float] = None
    loan_amount_mnt: float = 0
    tenor_months: int = 12
    flags: list[FlagRef] = Field(default_factory=list)


class ScoreFactor(BaseModel):
    key: str
    label: str
    raw_value: Optional[float]
    raw_display: str
    base_points: float
    points: float
    max_points: float
    verification_status: CheckStatus
    weight_applied: float
    reason_code: str
    reason: str


class ScorePenalty(BaseModel):
    code: str
    label: str
    points: float
    detail: str


class ScoreResult(BaseModel):
    scorecard_version: str
    total_points: float
    max_points: float
    grade: str
    grade_label: str
    pd_illustrative: float
    rate_band: str
    rate_min: Optional[float]
    rate_max: Optional[float]
    decision_hint: str
    suggested_limit_mnt: float
    suggested_tenor_months: int
    reference_monthly_installment_mnt: float
    factors: list[ScoreFactor]
    penalties: list[ScorePenalty]
    inputs: ScorerInputs
    computed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Simulation(BaseModel):
    action_key: str
    label: str
    change: str
    base_points: float
    new_points: float
    points_gain: float
    base_grade: str
    new_grade: str
    expected_grade_impact: str


# --------------------------------------------------------------------------- #
# Step 4 — EXPLAIN
# --------------------------------------------------------------------------- #
class ImprovementActionLLM(BaseModel):
    action_key: str = Field(description="Must be one of the simulated action keys given in the prompt")
    action: str = Field(description="Imperative, one sentence, plain language")
    why: str = Field(description="One or two sentences on why this raises the grade; do not state numbers that were not given")


class ExplainLLMOutput(BaseModel):
    owner_summary: str = Field(description="3-5 short sentences in friendly plain English explaining the grade")
    owner_summary_mn: str = Field(description="The same summary in Mongolian (Cyrillic)")
    improvement_plan: list[ImprovementActionLLM]


class ImprovementItem(BaseModel):
    action_key: str
    action: str
    why: str
    expected_grade_impact: str
    points_gain: float
    simulated_grade: str
    simulated_points: float


class GuardrailReport(BaseModel):
    numbers_checked: int = 0
    violations: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    note: str = ""


class ExplainResult(BaseModel):
    owner_summary: str
    owner_summary_mn: str
    improvement_plan: list[ImprovementItem]
    guardrail: GuardrailReport


# --------------------------------------------------------------------------- #
# Step 5 — WRITE
# --------------------------------------------------------------------------- #
class EvidenceItem(BaseModel):
    figure: str = Field(description="What the number is, e.g. 'Average monthly bank inflows'")
    value: str = Field(description="The value as given in the inputs, with MNT")
    source: str = Field(description="Document and page/row reference")


class CreditMemo(BaseModel):
    title: str
    business_summary: str
    cash_flow_analysis: str
    verification_status: str
    key_risks: list[str]
    mitigants: list[str]
    scorecard_result: str
    suggested_limit_and_tenor: str
    evidence_appendix: list[EvidenceItem]


class WriteResult(BaseModel):
    memo: CreditMemo
    markdown: str
    html: str
    guardrail: GuardrailReport


# --------------------------------------------------------------------------- #
# Request / persistence models
# --------------------------------------------------------------------------- #
class DeclaredInfo(BaseModel):
    business_name: str
    owner_name: str
    sector: str
    location: str
    years_in_business: float
    monthly_revenue_mnt: float
    loan_amount_mnt: float
    tenor_months: int
    purpose: str


class UploadedDoc(BaseModel):
    doc_id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    path: str


class StepTrace(BaseModel):
    step: str
    prompt_name: str
    prompt_version: str
    model: str
    latency_ms: int
    schema_valid: bool
    attempts: int
    cached: bool
    input_refs: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    note: str = ""


class Offer(BaseModel):
    id: str
    bank_name: str
    amount_mnt: float
    tenor_months: int
    annual_rate: float
    status: Literal["offered", "accepted", "declined"] = "offered"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Assessment(BaseModel):
    id: str
    created_at: str
    status: Literal["created", "running", "done", "error"] = "created"
    declared: DeclaredInfo
    documents: list[UploadedDoc] = Field(default_factory=list)
    reads: list[DocumentRead] = Field(default_factory=list)
    verify: Optional[VerifyResult] = None
    score: Optional[ScoreResult] = None
    simulations: list[Simulation] = Field(default_factory=list)
    explain: Optional[ExplainResult] = None
    write: Optional[WriteResult] = None
    trace: list[StepTrace] = Field(default_factory=list)
    error: Optional[str] = None
    offers: list[Offer] = Field(default_factory=list)
    identity_revealed: bool = False
    mode: str = ""
