"""The bank view must not reveal the business or owner until the business accepts an offer."""

from __future__ import annotations

import json

from app.main import _bank_view
from app.schemas import Assessment, DeclaredInfo, Offer, VerifiedInput, VerifyCheck, VerifyFlag, VerifyResult


def _assessment(revealed: bool) -> Assessment:
    declared = DeclaredInfo(business_name="Darkhan Fresh Bakery", owner_name="Nomin Erdenebat", sector="Bakery", location="Darkhan",
                            years_in_business=1, monthly_revenue_mnt=4_500_000, loan_amount_mnt=5_000_000, tenor_months=12, purpose="oven")
    verify = VerifyResult(
        checks=[VerifyCheck(check_name="account_holder_matches_business", status="verified", source="deterministic",
                            detail="Account holder 'Darkhan Fresh Bakery' vs declared business 'Darkhan Fresh Bakery' (token overlap 100%).",
                            evidence_refs=["bank_statement.csv header"])],
        flags=[VerifyFlag(severity="info", code="X", source="gemini", message="Darkhan Fresh Bakery shows a seasonal pattern; owner Nomin Erdenebat declared 4.5M")],
        verified_inputs=[VerifiedInput(name="years_in_business", value=1, status="partially_verified", basis="Darkhan Fresh Bakery account opened 2025")],
    )
    return Assessment(id="abc123def456", created_at="2026-10-18T00:00:00Z", status="done", declared=declared, verify=verify,
                      offers=[Offer(id="o1", bank_name="Bank", amount_mnt=1, tenor_months=12, annual_rate=0.2, status="accepted" if revealed else "offered")],
                      identity_revealed=revealed)


def test_hidden_bank_view_never_contains_identity():
    a = _assessment(revealed=False)
    payload = json.dumps(_bank_view(a, full=True), ensure_ascii=False)
    assert "Darkhan Fresh Bakery" not in payload
    assert "Nomin Erdenebat" not in payload
    assert "Nomin" not in payload
    assert "identity hidden" in payload


def test_revealed_bank_view_shows_identity():
    a = _assessment(revealed=True)
    payload = json.dumps(_bank_view(a, full=True), ensure_ascii=False)
    assert "Darkhan Fresh Bakery" in payload
    assert "Nomin Erdenebat" in payload
