"""Unit tests for the deterministic scorer — the auditable core."""

from __future__ import annotations

import pytest

from app import scorecard_config as cfg
from app.schemas import FlagRef, ScorerInputs
from app.steps.score import compute_score, monthly_installment, simulate_improvements


def strong_inputs(**overrides) -> ScorerInputs:
    base = dict(
        months_documented=12,
        months_documented_status="verified",
        avg_monthly_inflow_mnt=21_500_000,
        avg_monthly_inflow_status="verified",
        inflow_cv=0.10,
        inflow_cv_status="verified",
        years_in_business=6,
        years_in_business_status="verified",
        declared_monthly_revenue_mnt=22_000_000,
        declared_monthly_revenue_status="verified",
        corroborating_sources=3,
        loan_amount_mnt=40_000_000,
        tenor_months=24,
    )
    base.update(overrides)
    return ScorerInputs(**base)


def thin_inputs() -> ScorerInputs:
    return ScorerInputs(
        months_documented=4,
        months_documented_status="verified",
        avg_monthly_inflow_mnt=4_450_000,
        avg_monthly_inflow_status="partially_verified",
        inflow_cv=0.36,
        inflow_cv_status="partially_verified",
        years_in_business=1,
        years_in_business_status="partially_verified",
        declared_monthly_revenue_mnt=4_500_000,
        declared_monthly_revenue_status="verified",
        corroborating_sources=1,
        loan_amount_mnt=15_000_000,
        tenor_months=12,
        flags=[FlagRef(code="THIN_FILE", severity="info")],
    )


def test_config_max_points_is_100():
    assert cfg.MAX_POINTS == 100
    assert sum(f["max_points"] for f in cfg.FACTORS) == 100


def test_grade_ladder_is_descending():
    mins = [g["min_points"] for g in cfg.GRADES]
    assert mins == sorted(mins, reverse=True)
    assert cfg.GRADES[-1]["min_points"] == 0


def test_installment_formula():
    # 12M over 12 months at 20% -> ~1.11M per month
    inst = monthly_installment(12_000_000, 0.20, 12)
    assert 1_100_000 < inst < 1_120_000
    assert monthly_installment(12_000_000, 0.0, 12) == pytest.approx(1_000_000)
    assert monthly_installment(5_000_000, 0.2, 0) == 5_000_000


def test_strong_profile_is_top_grade():
    result = compute_score(strong_inputs())
    assert result.grade == "K1"
    assert result.total_points >= 85
    assert result.decision_hint == "auto_approve_candidate"
    assert result.suggested_limit_mnt <= 40_000_000
    assert result.suggested_tenor_months == 24


def test_thin_file_is_low_grade():
    result = compute_score(thin_inputs())
    assert result.grade in {"K6", "K7"}
    months = next(f for f in result.factors if f.key == "months_documented")
    assert months.reason_code == "MD03"


def test_every_factor_has_reason_code_and_bounds():
    result = compute_score(strong_inputs())
    assert len(result.factors) == len(cfg.FACTORS)
    for f in result.factors:
        assert f.reason_code
        assert 0 <= f.points <= f.max_points
        assert f.points <= f.base_points


def test_unverified_inputs_count_at_reduced_weight():
    full = compute_score(strong_inputs())
    half = compute_score(strong_inputs(avg_monthly_inflow_status="unverified"))
    f_full = next(f for f in full.factors if f.key == "installment_coverage")
    f_half = next(f for f in half.factors if f.key == "installment_coverage")
    assert f_half.base_points == f_full.base_points
    assert f_half.points == pytest.approx(f_full.points * cfg.STATUS_WEIGHTS["unverified"])
    assert half.total_points < full.total_points


def test_inconsistent_inputs_count_zero():
    result = compute_score(strong_inputs(years_in_business_status="inconsistent"))
    f = next(f for f in result.factors if f.key == "years_in_business")
    assert f.points == 0
    assert f.weight_applied == 0


def test_flags_penalise_and_are_capped():
    one = compute_score(strong_inputs(flags=[FlagRef(code="W1", severity="warning")]))
    none = compute_score(strong_inputs())
    assert none.total_points - one.total_points == pytest.approx(abs(cfg.PENALTIES["warning"]))
    many = compute_score(strong_inputs(flags=[FlagRef(code=f"C{i}", severity="critical") for i in range(10)]))
    assert none.total_points - many.total_points == pytest.approx(abs(cfg.PENALTIES["cap"]))
    assert any(p.code == "PEN_CAP" for p in many.penalties)


def test_info_flags_do_not_penalise():
    with_info = compute_score(strong_inputs(flags=[FlagRef(code="I", severity="info")]))
    without = compute_score(strong_inputs())
    assert with_info.total_points == without.total_points


def test_grade_is_monotonic_in_points():
    seen = []
    for pts in range(0, 101, 5):
        row = next(g for g in cfg.GRADES if pts >= g["min_points"])
        seen.append(row["grade"])
    # K7 ... K1 in non-increasing order of grade number
    numbers = [int(g[1]) for g in seen]
    assert numbers == sorted(numbers, reverse=True)


def test_simulations_come_from_rerunning_scorer():
    inputs = thin_inputs()
    base = compute_score(inputs)
    sims = simulate_improvements(inputs, base)
    assert sims, "thin file should have improvement options"
    assert all(s.points_gain > 0 for s in sims)
    assert sims == sorted(sims, key=lambda s: s.points_gain, reverse=True)
    extend = next(s for s in sims if s.action_key == "extend_history")
    alt = inputs.model_copy(deep=True)
    alt.months_documented = 12
    alt.months_documented_status = "verified"
    alt.avg_monthly_inflow_status = "verified"
    alt.inflow_cv_status = "verified"
    assert extend.new_points == compute_score(alt).total_points
    assert extend.expected_grade_impact.startswith(f"{base.grade}")


def test_declared_gap_simulation_clears_flag():
    inputs = strong_inputs(
        declared_monthly_revenue_status="unverified",
        flags=[FlagRef(code="DECLARED_VS_BANK", severity="warning")],
    )
    sims = simulate_improvements(inputs)
    fix = next(s for s in sims if s.action_key == "resolve_declared_gap")
    assert fix.points_gain >= abs(cfg.PENALTIES["warning"])


def test_scorer_inputs_contain_no_protected_attributes():
    forbidden = {"name", "gender", "sex", "age", "ethnicity", "religion", "nationality", "location", "owner"}
    for field in ScorerInputs.model_fields:
        assert not any(tok in field.lower() for tok in forbidden), field


def test_k7_has_no_offer():
    zero = ScorerInputs(loan_amount_mnt=10_000_000, tenor_months=12)
    result = compute_score(zero)
    assert result.grade == "K7"
    assert result.suggested_limit_mnt == 0
    assert result.rate_min is None
