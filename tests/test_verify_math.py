"""Tests for the deterministic half of VERIFY and the CSV re-summation."""

from __future__ import annotations

import pytest

from app.steps.verify import (
    account_age_status,
    coefficient_of_variation,
    declared_vs_bank_status,
    match_fraction,
    missing_months,
    month_range,
    name_similarity,
    outlier_months,
    ratio_status,
)
from app.tabular import monthly_totals_from_csv, pick_inflow_column, pick_outflow_column


def test_month_range_and_gaps():
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert missing_months(["2025-10", "2025-11", "2026-01"]) == ["2025-12"]
    assert missing_months(["2026-01"]) == []


def test_coefficient_of_variation():
    assert coefficient_of_variation([10, 10, 10]) == 0
    assert coefficient_of_variation([]) == 1.0
    cv = coefficient_of_variation([8.9, 9.6, 8.4, 7.1, 5.2, 4.4, 3.9, 4.1, 4.6, 5.0, 5.9, 7.3])
    assert 0.28 < cv < 0.34


def test_outliers():
    series = {f"2026-0{i}": 5_000_000 for i in range(1, 8)}
    series["2026-08"] = 20_000_000
    series["2026-09"] = 1_000_000
    assert outlier_months(series) == ["2026-08", "2026-09"]
    assert outlier_months({"a": 1, "b": 100}) == []  # too short


@pytest.mark.parametrize(
    "ratio,status,sev",
    [
        (None, "unverified", None),
        (0.95, "verified", None),
        (1.08, "verified", None),
        (1.3, "partially_verified", "info"),
        (1.45, "unverified", "warning"),
        (2.4, "inconsistent", "critical"),
    ],
)
def test_declared_vs_bank_thresholds(ratio, status, sev):
    s, severity, _ = declared_vs_bank_status(ratio)
    assert s == status
    assert severity == sev


def test_ratio_status():
    assert ratio_status(1.0) == "verified"
    assert ratio_status(1.12) == "verified"
    assert ratio_status(1.3) == "partially_verified"
    assert ratio_status(2.0) == "inconsistent"
    assert ratio_status(None) == "unverified"


def test_account_age_status():
    assert account_age_status(4, 3.9) == ("partially_verified", None)
    assert account_age_status(4, 1.0) == ("inconsistent", "warning")
    assert account_age_status(4, 2.5) == ("unverified", "info")
    assert account_age_status(4, None) == ("unverified", None)


def test_name_similarity():
    assert name_similarity("Erdenet Cashmere Workshop", "Erdenet Cashmere Workshop LLC") >= 0.5
    assert name_similarity("Darkhan Fresh Bakery", "Khan-Uul Auto Parts") < 0.5


def test_match_fraction():
    a = {"2026-01": 100, "2026-02": 200, "2026-03": 300}
    b = {"2026-01": 101, "2026-02": 250, "2026-03": 300}
    assert match_fraction(a, b, 0.02) == pytest.approx(2 / 3)
    assert match_fraction({}, b, 0.02) == 0


CSV = """Khangai Demo Bank — statement
Account holder,Test Co
Account number,5001-0000-1111
Account opened,2022-11-03
Currency,MNT

Date,Description,Debit (MNT),Credit (MNT),Balance (MNT)
2026-01-03,Sale,,100000,1100000
2026-01-10,Rent,40000,,1060000
2026-02-02,Sale,,250000,1310000
2026-02-05,Sale,,50000,1360000

Monthly summary
Month,Total credits (MNT),Total debits (MNT)
2026-01,100000,40000
2026-02,300000,0
"""


def test_csv_monthly_totals_ignore_summary_tables():
    totals = monthly_totals_from_csv(CSV)
    assert totals["months"]["2026-01"]["Credit (MNT)"] == 100000
    assert totals["months"]["2026-01"]["Debit (MNT)"] == 40000
    assert totals["months"]["2026-02"]["Credit (MNT)"] == 300000
    assert "2026-02" in totals["rows"]
    assert pick_inflow_column(totals["columns"]) == "Credit (MNT)"
    assert pick_outflow_column(totals["columns"]) == "Debit (MNT)"


def test_csv_without_dates_returns_empty():
    assert monthly_totals_from_csv("a,b\n1,2\n") == {}


# --------------------------------------------------------------------------- #
# Regression tests for real-upload edge cases
# --------------------------------------------------------------------------- #
from app.schemas import DeclaredInfo, DocumentRead, ExtractedField, MonthlyFigure, ReadResult, UploadedDoc  # noqa: E402
from app.steps.read import normalise, parse_month  # noqa: E402
from app.steps.verify import deterministic_checks, name_script  # noqa: E402
from app.tabular import _to_number  # noqa: E402


def test_month_range_rejects_impossible_months_instead_of_looping():
    assert month_range("2025-13", "2026-01") == []
    assert month_range("2025-00", "2025-03") == []
    assert month_range("2025-01", "2025-13") == []
    assert month_range("garbage", "2025-03") == []
    assert missing_months(["2025-13", "2026-01"]) == []
    assert month_range("2025-12", "2026-01") == ["2025-12", "2026-01"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2025-10", "2025-10"),
        ("2025-10-15", "2025-10"),
        ("2025.11", "2025-11"),
        ("2025/12", "2025-12"),
        ("10/2025", "2025-10"),
        ("Oct 2025", "2025-10"),
        ("October 2025", "2025-10"),
        ("Sept 2025", "2025-09"),
        ("2025 оны 10 сар", "2025-10"),
        ("2025-13", None),
        ("2025-00", None),
        ("13/2025", None),
        ("Foo 2025", None),
        ("", None),
    ],
)
def test_parse_month_formats(raw, expected):
    assert parse_month(raw) == expected


def _fig(month, value, out=None):
    return MonthlyFigure(month=month, sales_or_inflows_mnt=value, expenses_or_outflows_mnt=out, source_document="x", page_or_row="p1")


def test_normalise_drops_impossible_month_and_keeps_other_formats():
    result = ReadResult(document_type="bank_statement", language="en", period_start="2025-10", period_end="2026-01",
                        monthly_figures=[_fig("2025-13", 1), _fig("Nov 2025", 2), _fig("2025.12", 3), _fig("2026-01", 4)],
                        extracted_fields=[], quality_notes=[])
    out = normalise(result, "s.csv")
    assert [f.month for f in out.monthly_figures] == ["2025-11", "2025-12", "2026-01"]
    assert all(f.source_document == "s.csv" for f in out.monthly_figures)
    assert any("2025-13" in n for n in out.quality_notes)


def test_to_number_accepts_plus_sign_and_currency_suffix():
    assert _to_number("+500,000") == 500000
    assert _to_number("500,000 MNT") == 500000
    assert _to_number("500 000₮") == 500000
    assert _to_number("-1,250.50") == -1250.5
    assert _to_number("abc") is None
    assert _to_number("") is None


MONGOLIAN_CSV = """Огноо,Гүйлгээний утга,Дебит гүйлгээ,Кредит гүйлгээ,Үлдэгдэл
2026-01-03,Борлуулалт,,5000000,6000000
2026-01-10,Түрээс,800000,,5200000
2026-02-02,Борлуулалт,,5200000,10400000
2026-02-07,Цалин,900000,,9500000
2026-03-02,Борлуулалт,,4800000,14300000
2026-03-09,Түрээс,800000,,13500000
"""


def test_pick_columns_mongolian_headers():
    totals = monthly_totals_from_csv(MONGOLIAN_CSV)
    assert pick_inflow_column(totals["columns"]) == "Кредит гүйлгээ"
    assert pick_outflow_column(totals["columns"]) == "Дебит гүйлгээ"
    assert totals["months"]["2026-01"]["Кредит гүйлгээ"] == 5000000


def test_pick_columns_income_expense_and_no_guessing():
    assert pick_inflow_column(["Balance", "Expense", "Income"]) == "Income"
    assert pick_outflow_column(["Balance", "Expense", "Income"]) == "Expense"
    assert pick_inflow_column(["Зарлага", "Орлого", "Үлдэгдэл"]) == "Орлого"
    assert pick_outflow_column(["Зарлага", "Орлого", "Үлдэгдэл"]) == "Зарлага"
    # Nothing recognisable: never fall back to the first numeric column.
    assert pick_inflow_column(["Balance", "Foo", "Bar"]) is None
    assert pick_inflow_column(["Debit", "Balance"]) is None


SIGNED_CSV = """Date,Description,Amount,Balance
2026-01-03,Sale,+500000,1500000
2026-01-05,Rent,-400000,1100000
2026-01-12,Sale,220000,1320000
2026-01-20,Supplier,-433000,887000
"""


def test_signed_single_amount_column_is_split_into_credits_and_debits():
    totals = monthly_totals_from_csv(SIGNED_CSV)
    m = totals["months"]["2026-01"]
    assert m["Amount (credits)"] == 720000
    assert m["Amount (debits)"] == 433000 + 400000
    assert m["Amount"] == -113000  # raw net column is kept
    assert pick_inflow_column(totals["columns"]) == "Amount (credits)"
    assert pick_outflow_column(totals["columns"]) == "Amount (debits)"


def test_csv_rows_with_impossible_month_are_skipped():
    text = "Date,Desc,Credit,Debit\n2025-13-01,bad,100,0\n2025-12-01,ok,200,0\n"
    totals = monthly_totals_from_csv(text)
    assert list(totals["months"]) == ["2025-12"]


def test_unclosed_quote_in_huge_csv_does_not_raise():
    rows = ["Date,Description,Credit,Debit"]
    rows.append('2026-01-03,"unterminated description,100,0')
    rows += [f"2026-01-{(i % 28) + 1:02d},row {i} with a reasonably long description text,100,0" for i in range(4000)]
    text = "\n".join(rows) + "\n"
    assert len(text) > 131072
    assert monthly_totals_from_csv(text) == {}


def _declared(**kw):
    base = dict(business_name="Nar LLC", owner_name="Bold Bat", sector="Retail", location="UB", years_in_business=4,
                monthly_revenue_mnt=5_000_000, loan_amount_mnt=10_000_000, tenor_months=12, purpose="stock")
    base.update(kw)
    return DeclaredInfo(**base)


def _bank_read(doc_id, filename, series, fields=None, mime="application/pdf"):
    months = sorted(series)
    return DocumentRead(doc_id=doc_id, filename=filename, mime_type=mime, result=ReadResult(
        document_type="bank_statement", language="en", period_start=months[0], period_end=months[-1],
        monthly_figures=[_fig(m, series[m], 100.0) for m in months],
        extracted_fields=[ExtractedField(field=k, value=v, source_document=filename, page_or_row="page 1", confidence=0.9) for k, v in (fields or {}).items()],
        quality_notes=[]))


def _doc(doc_id, filename, mime="application/pdf", path="nonexistent"):
    return UploadedDoc(doc_id=doc_id, filename=filename, mime_type=mime, size_bytes=1, sha256="0" * 64, path=path)


SIX = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]


def test_two_bank_accounts_are_summed_not_treated_as_copies():
    khan = _bank_read("a", "khan.pdf", {m: 3_000_000 for m in SIX}, {"account_holder": "Nar LLC", "account_number": "5001-0000-1111"})
    golomt = _bank_read("b", "golomt.pdf", {m: 2_000_000 for m in SIX}, {"account_holder": "Nar LLC", "account_number": "1105-2222-3333"})
    det = deterministic_checks(_declared(), [khan, golomt], [_doc("a", "khan.pdf"), _doc("b", "golomt.pdf")])
    assert det["metrics"]["avg_monthly_inflow_mnt"] == 5_000_000
    assert det["metrics"]["declared_to_bank_ratio"] == 1.0
    assert det["metrics"]["bank_accounts"] == 2
    codes = {f.code for f in det["flags"]}
    assert "BANK_COPIES_DISAGREE" not in codes
    assert not any(c.startswith("DECLARED_VS_BANK") for c in codes)
    names = {c.check_name for c in det["checks"]}
    assert "multiple_accounts_combined" in names
    assert "bank_statement_copies_agree" not in names
    assert det["series"][0]["bank_outflow"] == 200.0


def test_csv_and_pdf_copies_of_one_statement_still_merge():
    csv_read = _bank_read("a", "khan.csv", {m: 3_000_000 for m in SIX}, {"account_number": "5001-0000-1111"}, mime="text/csv")
    pdf_read = _bank_read("b", "khan.pdf", {m: 3_000_000 for m in SIX})  # scan without a readable account number
    det = deterministic_checks(_declared(monthly_revenue_mnt=3_000_000), [csv_read, pdf_read], [_doc("a", "khan.csv", "text/csv"), _doc("b", "khan.pdf")])
    assert det["metrics"]["avg_monthly_inflow_mnt"] == 3_000_000
    assert det["metrics"]["bank_accounts"] == 1
    copies = next(c for c in det["checks"] if c.check_name == "bank_statement_copies_agree")
    assert copies.status == "verified"
    assert "BANK_COPIES_DISAGREE" not in {f.code for f in det["flags"]}


def test_same_account_copies_that_disagree_are_flagged():
    csv_read = _bank_read("a", "khan.csv", {m: 3_000_000 for m in SIX}, {"account_number": "5001-0000-1111"}, mime="text/csv")
    pdf_read = _bank_read("b", "khan.pdf", {m: 2_000_000 for m in SIX}, {"account_number": "5001 0000 1111"})
    det = deterministic_checks(_declared(monthly_revenue_mnt=3_000_000), [csv_read, pdf_read], [_doc("a", "khan.csv", "text/csv"), _doc("b", "khan.pdf")])
    assert det["metrics"]["avg_monthly_inflow_mnt"] == 3_000_000  # CSV wins
    assert "BANK_COPIES_DISAGREE" in {f.code for f in det["flags"]}


def test_name_script():
    assert name_script("Bold Bat") == "lat"
    assert name_script("Болд Бат") == "cyr"
    assert name_script("Нар LLC") == "latcyr"
    assert name_script("123") == ""


def _holder_outcome(holder, **declared):
    read = _bank_read("a", "bank.pdf", {m: 5_000_000 for m in SIX}, {"account_holder": holder})
    det = deterministic_checks(_declared(**declared), [read], [_doc("a", "bank.pdf")])
    check = next(c for c in det["checks"] if c.check_name == "account_holder_matches_business")
    flags = {f.code: f.severity for f in det["flags"] if f.code.startswith("ACCOUNT_HOLDER")}
    return check, flags


def test_account_in_owner_name_is_verified_without_leaking_owner_into_detail():
    check, flags = _holder_outcome("Bold Bat", business_name="Nar LLC", owner_name="Bold Bat")
    assert check.status == "verified"
    assert flags == {}
    assert "owner's name" in check.detail


def test_cross_script_holder_is_a_warning_not_critical():
    check, flags = _holder_outcome("Erdenet Cashmere Workshop", business_name="Эрдэнэт Кашмир", owner_name="")
    assert check.status == "partially_verified"
    assert flags == {"ACCOUNT_HOLDER_SCRIPT_MISMATCH": "warning"}


def test_unrelated_holder_in_same_script_stays_critical():
    check, flags = _holder_outcome("Khan-Uul Auto Parts", business_name="Darkhan Fresh Bakery", owner_name="Bold Bat")
    assert check.status == "inconsistent"
    assert flags == {"ACCOUNT_HOLDER_MISMATCH": "critical"}


def test_mongolian_csv_credit_figures_are_not_overwritten_by_debits(tmp_path):
    path = tmp_path / "khan.csv"
    path.write_text(MONGOLIAN_CSV, encoding="utf-8")
    read = DocumentRead(doc_id="a", filename="khan.csv", mime_type="text/csv", result=ReadResult(
        document_type="bank_statement", language="mn", period_start="2026-01", period_end="2026-03",
        monthly_figures=[_fig("2026-01", 5_000_000, 800_000), _fig("2026-02", 5_200_000, 900_000), _fig("2026-03", 4_800_000, 800_000)],
        extracted_fields=[], quality_notes=[]))
    det = deterministic_checks(_declared(), [read], [_doc("a", "khan.csv", "text/csv", str(path))])
    arith = next(c for c in det["checks"] if c.check_name == "csv_arithmetic:khan.csv")
    assert arith.status == "verified"
    assert "Кредит гүйлгээ" in arith.detail
    assert [f.sales_or_inflows_mnt for f in read.result.monthly_figures] == [5_000_000, 5_200_000, 4_800_000]
    assert det["metrics"]["avg_monthly_inflow_mnt"] == 5_000_000


def test_signed_amount_csv_yields_gross_inflows(tmp_path):
    path = tmp_path / "pay.csv"
    path.write_text(SIGNED_CSV, encoding="utf-8")
    read = DocumentRead(doc_id="a", filename="pay.csv", mime_type="text/csv", result=ReadResult(
        document_type="bank_statement", language="en", period_start="2026-01", period_end="2026-01",
        monthly_figures=[_fig("2026-01", -113_000)], extracted_fields=[], quality_notes=[]))
    det = deterministic_checks(_declared(), [read], [_doc("a", "pay.csv", "text/csv", str(path))])
    assert read.result.monthly_figures[0].sales_or_inflows_mnt == 720_000
    assert read.result.monthly_figures[0].expenses_or_outflows_mnt == 833_000
    assert det["metrics"]["avg_monthly_inflow_mnt"] == 720_000
