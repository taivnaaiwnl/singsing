"""Step 2 — VERIFY.

Two halves, merged:

* `deterministic_checks` — pure Python arithmetic: CSV re-summation, month
  continuity, averages, coefficient of variation, outliers, declared-vs-bank,
  ledger-vs-bank, payments-vs-bank, totals, account age, account-holder match.
* Gemini reasoning (prompts/verify.txt) — qualitative checks and flags, plus a
  proposed status per scorer input.

Merge rule: values always come from Python. For statuses, the more
conservative of the two wins. Gemini flags are shown but carry no score
penalty unless a deterministic check raised the same issue.
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import date
from pathlib import Path

from app.config import settings
from app.gemini_client import GeminiStepFailure, gemini, load_prompt, render
from app.schemas import (
    DeclaredInfo,
    DocumentRead,
    StepTrace,
    UploadedDoc,
    VerifiedInput,
    VerifyCheck,
    VerifyFlag,
    VerifyLLMOutput,
    VerifyResult,
    more_conservative,
)
from app.tabular import monthly_totals_from_csv, pick_inflow_column, pick_outflow_column

TODAY = date.today()

INPUT_NAMES = [
    "months_documented",
    "avg_monthly_inflow_mnt",
    "inflow_cv",
    "years_in_business",
    "declared_monthly_revenue_mnt",
    "corroborating_sources",
    "ledger_to_bank_ratio",
]


def fmt_m(v: float) -> str:
    return f"₮{v / 1e6:.1f}M"


# --------------------------------------------------------------------------- #
# Pure math helpers (unit-tested in tests/test_verify_math.py)
# --------------------------------------------------------------------------- #
def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM between start and end; [] when either label is not a real month."""
    try:
        y, m = int(start[:4]), int(start[5:7])
        ey, em = int(end[:4]), int(end[5:7])
    except (TypeError, ValueError):
        return []
    if not (1 <= m <= 12 and 1 <= em <= 12):
        return []
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def missing_months(months: list[str]) -> list[str]:
    if len(months) < 2:
        return []
    ms = sorted(months)
    return [m for m in month_range(ms[0], ms[-1]) if m not in set(ms)]


def coefficient_of_variation(values: list[float]) -> float:
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return 1.0
    mean = statistics.fmean(vals)
    if mean <= 0:
        return 1.0
    return statistics.pstdev(vals) / mean


def outlier_months(series: dict[str, float], high: float = 2.5, low: float = 0.4) -> list[str]:
    if len(series) < 4:
        return []
    med = statistics.median(series.values())
    if med <= 0:
        return []
    return [m for m, v in sorted(series.items()) if v > high * med or v < low * med]


def declared_vs_bank_status(ratio: float | None) -> tuple[str, str | None, str | None]:
    """Return (status, flag_severity, flag_code) for declared revenue / average bank inflow."""
    if ratio is None:
        return "unverified", None, None
    if ratio <= 1.10:
        return "verified", None, None
    if ratio <= 1.35:
        return "partially_verified", "info", "DECLARED_VS_BANK_MINOR"
    if ratio <= 2.0:
        return "unverified", "warning", "DECLARED_VS_BANK"
    return "inconsistent", "critical", "DECLARED_VS_BANK_CRITICAL"


def ratio_status(ratio: float | None, tight=(0.85, 1.15), loose=(0.7, 1.5)) -> str:
    if ratio is None:
        return "unverified"
    if tight[0] <= ratio <= tight[1]:
        return "verified"
    if loose[0] <= ratio <= loose[1]:
        return "partially_verified"
    return "inconsistent"


def account_age_status(declared_years: float, age_years: float | None) -> tuple[str, str | None]:
    if age_years is None:
        return "unverified", None
    if age_years >= 0.8 * declared_years:
        return "partially_verified", None
    if age_years < 0.5 * declared_years:
        return "inconsistent", "warning"
    return "unverified", "info"


def name_similarity(a: str, b: str) -> float:
    tok = lambda s: {t for t in re.findall(r"[a-zа-яөүё0-9]+", s.lower()) if len(t) > 2}
    ta, tb = tok(a), tok(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def name_script(s: str) -> str:
    """'lat', 'cyr', 'latcyr' or '' depending on which alphabets the name uses."""
    low = (s or "").lower()
    return ("lat" if re.search(r"[a-z]", low) else "") + ("cyr" if re.search(r"[а-яөүё]", low) else "")


def match_fraction(a: dict[str, float], b: dict[str, float], tol: float) -> float:
    common = [m for m in a if m in b]
    if not common:
        return 0.0
    ok = sum(1 for m in common if abs(a[m] - b[m]) <= max(1.0, abs(b[m]) * tol))
    return ok / len(common)


# --------------------------------------------------------------------------- #
# Deterministic checks
# --------------------------------------------------------------------------- #
def _field(read: DocumentRead, *names: str) -> str | None:
    for ef in read.result.extracted_fields:
        f = ef.field.lower()
        if any(n in f for n in names):
            return ef.value
    return None


def _ref(read: DocumentRead, page_or_row: str = "") -> str:
    return f"{read.filename} {page_or_row}".strip()


def _parse_number(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"-?\d[\d,\s]*(\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _account_key(read: DocumentRead) -> str:
    """Digits of the extracted account number, or '' when the statement does not show one."""
    raw = _field(read, "account_number", "account_no", "iban")
    digits = re.sub(r"\D", "", raw or "")
    return digits if len(digits) >= 4 else ""


def _series_agree(series: dict[str, float], read: DocumentRead) -> bool:
    """True when every month the read shares with `series` matches within 2% (same statement, other copy)."""
    overlap = [f for f in read.result.monthly_figures if f.month in series]
    if not overlap:
        return False
    return all(abs(series[f.month] - f.sales_or_inflows_mnt) <= max(1.0, abs(series[f.month]) * 0.02) for f in overlap)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    m = re.search(r"(\d{4})[-./](\d{1,2})(?:[-./](\d{1,2}))?", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        return None


def deterministic_checks(declared: DeclaredInfo, reads: list[DocumentRead], documents: list[UploadedDoc]) -> dict:
    checks: list[VerifyCheck] = []
    flags: list[VerifyFlag] = []
    metrics: dict = {}
    notes: list[str] = []
    doc_by_id = {d.doc_id: d for d in documents}

    # 1. CSV arithmetic check: re-sum every CSV in Python and compare with Gemini.
    for read in reads:
        doc = doc_by_id.get(read.doc_id)
        if not doc or not read.mime_type.startswith("text/"):
            continue
        try:
            text = Path(doc.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        totals = monthly_totals_from_csv(text)
        if not totals:
            continue
        in_col = pick_inflow_column(totals["columns"])
        out_col = pick_outflow_column(totals["columns"])
        if not in_col:
            continue
        py_in = {m: v.get(in_col, 0.0) for m, v in totals["months"].items()}
        py_out = {m: v.get(out_col, 0.0) for m, v in totals["months"].items()} if out_col else {}
        llm_in = {f.month: f.sales_or_inflows_mnt for f in read.result.monthly_figures}
        frac = match_fraction(llm_in, py_in, 0.02)
        name = f"csv_arithmetic:{read.filename}"
        if frac >= 0.8 and len(llm_in) >= len(py_in) * 0.8:
            checks.append(VerifyCheck(check_name=name, status="verified", source="deterministic",
                                      detail=f"Gemini's monthly figures reproduce Python column sums of '{in_col}' for {frac:.0%} of months (±2%).",
                                      evidence_refs=[_ref(read, totals["rows"].get(m, "")) for m in list(py_in)[:3]]))
        else:
            checks.append(VerifyCheck(check_name=name, status="partially_verified", source="deterministic",
                                      detail=f"Gemini's monthly figures matched Python sums for only {frac:.0%} of months; figures replaced by deterministic sums of '{in_col}'.",
                                      evidence_refs=[_ref(read, "all transaction rows")]))
            flags.append(VerifyFlag(severity="info", code="LLM_ARITHMETIC_CORRECTED", source="deterministic",
                                    message=f"{read.filename}: model-read monthly totals were corrected using deterministic column sums.",
                                    evidence_refs=[_ref(read)]))
            notes.append(f"{read.filename}: replaced {len(llm_in)} model figures with {len(py_in)} deterministic sums")
            from app.schemas import MonthlyFigure
            read.result.monthly_figures = [
                MonthlyFigure(month=m, sales_or_inflows_mnt=py_in[m], expenses_or_outflows_mnt=py_out.get(m),
                              source_document=read.filename, page_or_row=totals["rows"].get(m, "rows"))
                for m in sorted(py_in)
            ]

    bank_reads = [r for r in reads if r.result.document_type == "bank_statement"]
    ledger_reads = [r for r in reads if r.result.document_type == "sales_ledger"]
    pay_reads = [r for r in reads if r.result.document_type == "payment_export"]

    # 2. Bank monthly series. Statements are grouped by account: copies of the same statement
    #    (CSV + PDF of one account) are merged with the CSV winning on conflict, while statements
    #    from different accounts (a business banking with two banks) are summed per month.
    ordered = sorted(bank_reads, key=lambda r: 0 if r.mime_type.startswith("text/") else 1)
    groups: list[dict] = []  # {"key", "reads", "in", "out", "src"}
    disagreements: list[str] = []
    for read in ordered:
        key = _account_key(read)
        if key:
            group = next((g for g in groups if g["key"] == key), None)
        else:
            # No account number on this copy: attach it to the statement it reproduces, if any.
            group = next((g for g in groups if _series_agree(g["in"], read)), None)
        if group is None:
            group = {"key": key, "reads": [], "in": {}, "out": {}, "src": {}}
            groups.append(group)
        group["reads"].append(read)
        for f in read.result.monthly_figures:
            if f.month in group["in"]:
                if abs(group["in"][f.month] - f.sales_or_inflows_mnt) > max(1.0, abs(group["in"][f.month]) * 0.02):
                    disagreements.append(f"{f.month}: {group['src'][f.month]} {group['in'][f.month]:,.0f} vs {read.filename} {f.sales_or_inflows_mnt:,.0f}")
                continue
            group["in"][f.month] = f.sales_or_inflows_mnt
            group["out"][f.month] = f.expenses_or_outflows_mnt
            group["src"][f.month] = f"{read.filename} {f.page_or_row}"
    bank_in: dict[str, float] = {}
    bank_out: dict[str, float | None] = {}
    bank_src: dict[str, str] = {}
    for g in groups:
        for m, v in g["in"].items():
            bank_in[m] = bank_in.get(m, 0.0) + v
            o = g["out"].get(m)
            if m not in bank_out:
                bank_out[m] = o
            elif bank_out[m] is None or o is None:
                bank_out[m] = None
            else:
                bank_out[m] = bank_out[m] + o
            bank_src[m] = f"{bank_src[m]}; {g['src'][m]}" if m in bank_src else g["src"][m]
    copy_groups = [g for g in groups if len(g["reads"]) >= 2]
    if copy_groups:
        copy_files = [r.filename for g in copy_groups for r in g["reads"]]
        if disagreements:
            checks.append(VerifyCheck(check_name="bank_statement_copies_agree", status="inconsistent", source="deterministic",
                                      detail="Bank statement documents for the same account disagree on monthly inflows: " + "; ".join(disagreements[:4]),
                                      evidence_refs=copy_files))
            flags.append(VerifyFlag(severity="warning", code="BANK_COPIES_DISAGREE", source="deterministic",
                                    message=f"Bank statement copies disagree for {len(disagreements)} month(s); the CSV figures were used.",
                                    evidence_refs=copy_files))
        else:
            checks.append(VerifyCheck(check_name="bank_statement_copies_agree", status="verified", source="deterministic",
                                      detail=f"{len(copy_files)} bank statement documents report the same monthly inflows (±2%).",
                                      evidence_refs=copy_files))
    if len(groups) > 1:
        labels = [(f"account ending {g['key'][-4:]}" if g["key"] else "account without a number") + f" ({', '.join(r.filename for r in g['reads'])})" for g in groups]
        checks.append(VerifyCheck(check_name="multiple_accounts_combined", status="verified", source="deterministic",
                                  detail=f"Monthly bank inflows are the sum of {len(groups)} accounts: " + "; ".join(labels) + ".",
                                  evidence_refs=[r.filename for r in bank_reads]))
    metrics["bank_accounts"] = len(groups)

    # 3. Months documented, continuity
    source_of_history = "bank"
    if bank_in:
        history = dict(sorted(bank_in.items()))
    elif ledger_reads:
        source_of_history = "ledger"
        history = {f.month: f.sales_or_inflows_mnt for r in ledger_reads for f in r.result.monthly_figures}
        history = dict(sorted(history.items()))
    else:
        history = {}
    months = list(history)
    gaps = missing_months(months)
    metrics["months_documented"] = len(months)
    metrics["history_source"] = source_of_history
    metrics["first_month"] = months[0] if months else None
    metrics["last_month"] = months[-1] if months else None
    metrics["missing_months"] = gaps
    if months:
        if gaps:
            checks.append(VerifyCheck(check_name="date_continuity", status="partially_verified", source="deterministic",
                                      detail=f"{len(months)} months documented between {months[0]} and {months[-1]} with gaps: {', '.join(gaps)}.",
                                      evidence_refs=[bank_src.get(months[0], "")]))
            flags.append(VerifyFlag(severity="warning", code="MISSING_MONTHS", source="deterministic",
                                    message=f"Documented history has gaps: {', '.join(gaps)}.", evidence_refs=[]))
        else:
            checks.append(VerifyCheck(check_name="date_continuity", status="verified", source="deterministic",
                                      detail=f"{len(months)} consecutive months documented ({months[0]} to {months[-1]}) from {source_of_history} records.",
                                      evidence_refs=[bank_src.get(months[0], ""), bank_src.get(months[-1], "")]))
    else:
        checks.append(VerifyCheck(check_name="date_continuity", status="unverified", source="deterministic",
                                  detail="No monthly cash-flow history could be extracted.", evidence_refs=[]))
    months_status = "verified" if source_of_history == "bank" and months and not gaps else ("partially_verified" if months else "unverified")
    if source_of_history == "ledger":
        months_status = "unverified"

    # 4. Averages, CV, outliers
    values = list(history.values())
    avg = statistics.fmean(values) if values else 0.0
    cv = coefficient_of_variation(values) if values else 1.0
    metrics["avg_monthly_inflow_mnt"] = round(avg, 0)
    metrics["inflow_cv"] = round(cv, 3)
    metrics["min_month_inflow_mnt"] = min(values) if values else 0
    metrics["max_month_inflow_mnt"] = max(values) if values else 0
    metrics["median_month_inflow_mnt"] = statistics.median(values) if values else 0
    outs = outlier_months(history)
    metrics["outlier_months"] = outs
    if values:
        checks.append(VerifyCheck(check_name="outlier_scan", status="verified" if not outs else "partially_verified", source="deterministic",
                                  detail=("No month deviates more than 2.5x above or 0.4x below the median inflow." if not outs
                                          else f"Unusual months relative to the median: {', '.join(outs)}."),
                                  evidence_refs=[bank_src.get(m, m) for m in outs[:3]]))
        if outs:
            flags.append(VerifyFlag(severity="info", code="OUTLIER_MONTHS", source="deterministic",
                                    message=f"Inflows in {', '.join(outs)} are unusual relative to the median month.", evidence_refs=[]))
    if source_of_history == "bank":
        inflow_status = "verified" if len(months) >= 6 else ("partially_verified" if len(months) >= 3 else "unverified")
    elif source_of_history == "ledger":
        inflow_status = "unverified"
    else:
        inflow_status = "unverified"
    if 0 < len(months) < 6:
        flags.append(VerifyFlag(severity="info", code="THIN_FILE", source="deterministic",
                                message=f"Only {len(months)} months of documented cash flow (thin file).", evidence_refs=[]))

    # 5. Declared revenue vs bank
    ratio = (declared.monthly_revenue_mnt / avg) if (avg > 0 and source_of_history == "bank") else None
    metrics["declared_to_bank_ratio"] = round(ratio, 3) if ratio else None
    d_status, d_sev, d_code = declared_vs_bank_status(ratio)
    if ratio is not None:
        pct = (ratio - 1) * 100
        direction = "above" if pct >= 0 else "below"
        checks.append(VerifyCheck(check_name="declared_revenue_vs_bank_inflows", status=d_status, source="deterministic",
                                  detail=f"Declared monthly revenue {fmt_m(declared.monthly_revenue_mnt)} is {abs(pct):.0f}% {direction} average bank inflows {fmt_m(avg)} over {len(months)} months.",
                                  evidence_refs=[bank_src.get(m, "") for m in months[:2]]))
        if d_sev:
            flags.append(VerifyFlag(severity=d_sev, code=d_code or "", source="deterministic",
                                    message=f"Declared monthly sales {fmt_m(declared.monthly_revenue_mnt)} vs average bank inflows {fmt_m(avg)} ({abs(pct):.0f}% {direction}): the declared figure is not supported by the bank statement at this level.",
                                    evidence_refs=[bank_src.get(m, "") for m in months[:2]]))
    else:
        checks.append(VerifyCheck(check_name="declared_revenue_vs_bank_inflows", status="unverified", source="deterministic",
                                  detail="No bank statement available to verify the declared monthly revenue.", evidence_refs=[]))

    # 6. Ledger vs bank
    ledger_series: dict[str, float] = {f.month: f.sales_or_inflows_mnt for r in ledger_reads for f in r.result.monthly_figures}
    ledger_ratio = None
    if ledger_series and bank_in:
        overlap = [m for m in ledger_series if m in bank_in]
        if overlap:
            ledger_ratio = sum(ledger_series[m] for m in overlap) / max(1.0, sum(bank_in[m] for m in overlap))
    metrics["ledger_to_bank_ratio"] = round(ledger_ratio, 3) if ledger_ratio else None
    metrics["ledger_months"] = len(ledger_series)
    l_status = ratio_status(ledger_ratio)
    if ledger_ratio is not None:
        lref = [f"{r.filename} {r.result.monthly_figures[0].page_or_row}" for r in ledger_reads if r.result.monthly_figures]
        checks.append(VerifyCheck(check_name="ledger_vs_bank_inflows", status=l_status, source="deterministic",
                                  detail=f"Ledger sales are {ledger_ratio:.2f}x bank inflows over {len([m for m in ledger_series if m in bank_in])} overlapping months"
                                         + (" (consistent; small cash share)." if l_status == "verified" else " (ledger likely includes cash sales not deposited)." if l_status == "partially_verified" else " (materially different records)."),
                                  evidence_refs=lref + [bank_src.get(m, "") for m in months[:1]]))
        if l_status == "inconsistent":
            flags.append(VerifyFlag(severity="warning", code="LEDGER_VS_BANK", source="deterministic",
                                    message=f"Sales ledger totals are {ledger_ratio:.2f}x the bank inflows; the two records do not describe the same cash flow.",
                                    evidence_refs=lref))
    elif ledger_reads:
        checks.append(VerifyCheck(check_name="ledger_vs_bank_inflows", status="unverified", source="deterministic",
                                  detail="Ledger provided but no overlapping bank months to compare.", evidence_refs=[r.filename for r in ledger_reads]))
    else:
        checks.append(VerifyCheck(check_name="ledger_vs_bank_inflows", status="unverified", source="deterministic",
                                  detail="No sales ledger provided.", evidence_refs=[]))

    # 7. Payment export vs bank
    pay_series: dict[str, float] = {f.month: f.sales_or_inflows_mnt for r in pay_reads for f in r.result.monthly_figures}
    pay_share = None
    if pay_series and bank_in:
        overlap = [m for m in pay_series if m in bank_in]
        if overlap:
            pay_share = sum(pay_series[m] for m in overlap) / max(1.0, sum(bank_in[m] for m in overlap))
    metrics["payment_share_of_bank_inflows"] = round(pay_share, 3) if pay_share is not None else None
    p_status = "unverified"
    if pay_share is not None:
        pref = [r.filename for r in pay_reads]
        if 0.15 <= pay_share <= 1.05:
            p_status = "verified"
            checks.append(VerifyCheck(check_name="payment_export_vs_bank", status="verified", source="deterministic",
                                      detail=f"Payment-provider settlements equal {pay_share:.0%} of bank inflows over the overlapping months, consistent with settlement into this account.",
                                      evidence_refs=pref))
        elif pay_share > 1.05:
            p_status = "inconsistent"
            checks.append(VerifyCheck(check_name="payment_export_vs_bank", status="inconsistent", source="deterministic",
                                      detail=f"Payment-provider settlements are {pay_share:.0%} of bank inflows: more was settled than arrived in this account.",
                                      evidence_refs=pref))
            flags.append(VerifyFlag(severity="warning", code="PAYMENTS_EXCEED_BANK", source="deterministic",
                                    message="Payment-provider settlements exceed bank inflows; settlements may go to another account.", evidence_refs=pref))
        else:
            p_status = "partially_verified"
            checks.append(VerifyCheck(check_name="payment_export_vs_bank", status="partially_verified", source="deterministic",
                                      detail=f"Payment-provider settlements are only {pay_share:.0%} of bank inflows.", evidence_refs=pref))
    elif pay_reads:
        checks.append(VerifyCheck(check_name="payment_export_vs_bank", status="unverified", source="deterministic",
                                  detail="Payment export provided but no overlapping bank months.", evidence_refs=[r.filename for r in pay_reads]))

    # 8. Corroborating sources
    sources = (1 if bank_in else 0) + (1 if l_status in {"verified", "partially_verified"} else 0) + (1 if p_status in {"verified", "partially_verified"} else 0)
    if not bank_in and ledger_series:
        sources = 1
    metrics["corroborating_sources"] = sources

    # 9. Stated totals vs sum of months (per document)
    for read in reads:
        total_field = _field(read, "total_credits", "period_total_sales", "total_sales", "total_inflow")
        total = _parse_number(total_field)
        figs = read.result.monthly_figures
        if total and figs:
            s = sum(f.sales_or_inflows_mnt for f in figs)
            ok = abs(s - total) <= max(1.0, total * 0.02)
            checks.append(VerifyCheck(check_name=f"stated_total_vs_sum:{read.filename}", status="verified" if ok else "inconsistent", source="deterministic",
                                      detail=f"Stated total {total:,.0f} vs sum of {len(figs)} monthly figures {s:,.0f}" + (" (match ±2%)." if ok else " (mismatch)."),
                                      evidence_refs=[_ref(read, figs[0].page_or_row)]))
            if not ok:
                flags.append(VerifyFlag(severity="warning", code="TOTAL_MISMATCH", source="deterministic",
                                        message=f"{read.filename}: the stated period total ({total:,.0f}) does not equal the sum of the monthly figures ({s:,.0f}).",
                                        evidence_refs=[_ref(read)]))

    # 10. Years in business vs account age
    opened = None
    opened_ref = ""
    for read in bank_reads or reads:
        val = _field(read, "account_opened", "opened", "open_date")
        d = _parse_date(val)
        if d:
            opened = d
            opened_ref = _ref(read, next((ef.page_or_row for ef in read.result.extracted_fields if "open" in ef.field.lower()), ""))
            break
    age = round((TODAY - opened).days / 365.25, 2) if opened else None
    metrics["account_age_years"] = age
    y_status, y_sev = account_age_status(declared.years_in_business, age)
    if age is not None:
        checks.append(VerifyCheck(check_name="tenure_vs_account_age", status=y_status, source="deterministic",
                                  detail=f"Declared {declared.years_in_business:g} years in business; the bank account was opened {opened} ({age:.1f} years ago). Account age is a proxy, so at most partially verified.",
                                  evidence_refs=[opened_ref]))
        if y_sev:
            flags.append(VerifyFlag(severity=y_sev, code="TENURE_VS_ACCOUNT_AGE", source="deterministic",
                                    message=f"Declared tenure ({declared.years_in_business:g} years) is not supported by the account age ({age:.1f} years).", evidence_refs=[opened_ref]))
    else:
        checks.append(VerifyCheck(check_name="tenure_vs_account_age", status="unverified", source="deterministic",
                                  detail="No account-opening date or registration document to verify years in business.", evidence_refs=[]))

    # 11. Account holder vs declared business (or owner: sole traders bank in their own name).
    #     The owner's name is compared here in Python only; it is never written into the check
    #     text, which is later handed to the model.
    for read in bank_reads:
        holder = _field(read, "account_holder", "business_name")
        if holder:
            sim_business = name_similarity(holder, declared.business_name)
            sim_owner = name_similarity(holder, declared.owner_name or "")
            sim = max(sim_business, sim_owner)
            hs, bs = name_script(holder), name_script(declared.business_name)
            cross_script = sim == 0 and bool(hs) and bool(bs) and hs != bs
            ref = [_ref(read, "header")]
            if sim >= 0.5:
                checks.append(VerifyCheck(check_name="account_holder_matches_business", status="verified", source="deterministic",
                                          detail=f"Account holder '{holder}' vs declared business '{declared.business_name}' (token overlap {sim_business:.0%})."
                                                 + (" The account is held in the declared owner's name." if sim_owner > sim_business else ""),
                                          evidence_refs=ref))
            elif cross_script:
                checks.append(VerifyCheck(check_name="account_holder_matches_business", status="partially_verified", source="deterministic",
                                          detail=f"Account holder '{holder}' and declared business '{declared.business_name}' are written in different alphabets; the names could not be compared automatically.",
                                          evidence_refs=ref))
                flags.append(VerifyFlag(severity="warning", code="ACCOUNT_HOLDER_SCRIPT_MISMATCH", source="deterministic",
                                        message=f"The bank account holder '{holder}' and the declared name use different alphabets (Latin vs Cyrillic); a reviewer should confirm they are the same party.",
                                        evidence_refs=ref))
            else:
                checks.append(VerifyCheck(check_name="account_holder_matches_business", status="inconsistent", source="deterministic",
                                          detail=f"Account holder '{holder}' vs declared business '{declared.business_name}' (token overlap {sim_business:.0%}); it does not match the declared owner either.",
                                          evidence_refs=ref))
                flags.append(VerifyFlag(severity="critical", code="ACCOUNT_HOLDER_MISMATCH", source="deterministic",
                                        message=f"The bank account holder '{holder}' does not match the declared business or owner name.", evidence_refs=ref))
            break

    # 12. Loan size plausibility
    if avg > 0:
        multiple = declared.loan_amount_mnt / avg
        metrics["loan_to_monthly_inflow"] = round(multiple, 2)
        if multiple > 12:
            flags.append(VerifyFlag(severity="warning", code="LOAN_SIZE_VS_INFLOWS", source="deterministic",
                                    message=f"Requested amount {fmt_m(declared.loan_amount_mnt)} is {multiple:.1f}x the average monthly inflow.", evidence_refs=[]))
            st = "inconsistent"
        elif multiple > 6:
            flags.append(VerifyFlag(severity="info", code="LOAN_SIZE_LARGE", source="deterministic",
                                    message=f"Requested amount is {multiple:.1f}x the average monthly inflow.", evidence_refs=[]))
            st = "partially_verified"
        else:
            st = "verified"
        checks.append(VerifyCheck(check_name="loan_size_plausibility", status=st, source="deterministic",
                                  detail=f"Requested {fmt_m(declared.loan_amount_mnt)} over {declared.tenor_months} months equals {multiple:.1f} months of average inflows.", evidence_refs=[]))

    # Verified inputs (values from Python)
    inputs = [
        VerifiedInput(name="months_documented", value=float(len(months)), status=months_status, basis=f"{len(months)} months from {source_of_history} records" + (f"; gaps {gaps}" if gaps else "")),
        VerifiedInput(name="avg_monthly_inflow_mnt", value=round(avg, 0), status=inflow_status, basis=f"Mean of {len(months)} monthly {source_of_history} inflows"),
        VerifiedInput(name="inflow_cv", value=round(cv, 3), status=inflow_status, basis="Population std-dev / mean of monthly inflows"),
        VerifiedInput(name="years_in_business", value=float(declared.years_in_business), status=y_status, basis="Declared; compared with bank account age" if age is not None else "Declared; no document to verify"),
        VerifiedInput(name="declared_monthly_revenue_mnt", value=float(declared.monthly_revenue_mnt), status=d_status, basis=f"Declared; ratio to bank inflows {ratio:.2f}" if ratio else "Declared; no bank statement to compare"),
        VerifiedInput(name="corroborating_sources", value=float(sources), status="verified", basis="Count of independent records consistent with the bank statement"),
        VerifiedInput(name="ledger_to_bank_ratio", value=round(ledger_ratio, 3) if ledger_ratio else 0.0, status=l_status, basis="Ledger sales / bank inflows over overlapping months" if ledger_ratio else "No ledger overlap"),
    ]

    series = []
    all_months = sorted(set(history) | set(ledger_series) | set(pay_series))
    for m in all_months:
        series.append({
            "month": m,
            "bank_inflow": bank_in.get(m),
            "bank_outflow": bank_out.get(m),
            "ledger_sales": ledger_series.get(m),
            "payment_net": pay_series.get(m),
        })
    return {"checks": checks, "flags": flags, "inputs": inputs, "metrics": metrics, "series": series, "notes": notes}


# --------------------------------------------------------------------------- #
# Gemini half + merge
# --------------------------------------------------------------------------- #
def _doc_summary(read: DocumentRead) -> dict:
    r = read.result
    return {
        "filename": read.filename,
        "document_type": r.document_type,
        "language": r.language,
        "period": f"{r.period_start} to {r.period_end}",
        "monthly_figures": [
            {"month": f.month, "inflows_or_sales": f.sales_or_inflows_mnt, "outflows_or_expenses": f.expenses_or_outflows_mnt, "ref": f"{read.filename} {f.page_or_row}"}
            for f in r.monthly_figures
        ],
        "extracted_fields": [{"field": e.field, "value": e.value, "ref": f"{read.filename} {e.page_or_row}", "confidence": e.confidence} for e in r.extracted_fields],
        "quality_notes": r.quality_notes,
    }


async def verify(declared: DeclaredInfo, reads: list[DocumentRead], documents: list[UploadedDoc]) -> tuple[VerifyResult, StepTrace]:
    det = deterministic_checks(declared, reads, documents)
    template, version = load_prompt("verify")
    declared_for_llm = declared.model_dump()
    declared_for_llm.pop("owner_name", None)  # no personal attributes reach the model's reasoning
    prompt = render(
        template,
        declared=declared_for_llm,
        documents=[_doc_summary(r) for r in reads],
        metrics=det["metrics"],
        det_checks=[{"check_name": c.check_name, "status": c.status, "detail": c.detail} for c in det["checks"]]
        + [{"flag": f.code, "severity": f.severity, "message": f.message} for f in det["flags"]],
    )

    llm: VerifyLLMOutput | None = None
    if settings.offline:
        from app import offline

        llm, trace = offline.verify(det, prompt_version=version)
    else:
        try:
            llm, trace = await gemini.generate_json(
                step="verify",
                prompt_name="verify",
                prompt_version=version,
                prompt_text=prompt,
                schema=VerifyLLMOutput,
                parts=[],
                parts_hashes=[],
                input_refs=[r.filename for r in reads],
                temperature=0.2,
            )
        except GeminiStepFailure as exc:
            trace = exc.trace
            trace.note = "Gemini verification unavailable; deterministic checks only"
            llm = None

    checks = list(det["checks"])
    flags = list(det["flags"])
    inputs: list[VerifiedInput] = list(det["inputs"])
    notes = list(det["notes"])
    if llm:
        existing = {c.check_name for c in checks}
        for c in llm.checks:
            c.source = "gemini"
            if c.check_name in existing:
                c.check_name = f"{c.check_name} (model view)"
            checks.append(c)
        for i, f in enumerate(llm.flags[:5]):
            f.source = "gemini"
            f.code = f.code or f"MODEL_OBS_{i + 1}"
            flags.append(f)
        llm_map = {v.name: v for v in llm.verified_inputs}
        merged = []
        for v in inputs:
            lv = llm_map.get(v.name)
            if lv:
                new_status = more_conservative(v.status, lv.status)
                if new_status != v.status:
                    notes.append(f"{v.name}: status tightened from {v.status} to {lv.status} by Gemini ({lv.basis})")
                    v = v.model_copy(update={"status": new_status, "basis": f"{v.basis}. Gemini: {lv.basis}"})
                if abs(lv.value - v.value) > max(0.01, abs(v.value) * 0.02):
                    notes.append(f"{v.name}: Gemini proposed {lv.value}, deterministic value {v.value} kept")
            merged.append(v)
        inputs = merged

    return VerifyResult(checks=checks, flags=flags, verified_inputs=inputs, metrics=det["metrics"], monthly_series=det["series"], llm_notes=notes), trace
