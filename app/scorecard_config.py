"""Kervan scorecard configuration.

This is Kervan's own scorecard (v0.1). Weights, bands, grade thresholds,
PD estimates and rate bands are Kervan's initial policy; they are NOT derived
from any bank's scorecard, policy, portfolio data or pricing, and should be
recalibrated on observed portfolio performance before being relied on for
lending decisions.

The scorer in app/steps/score.py reads only this file. No LLM output can
change anything here at runtime: Gemini reads, verifies, explains and writes;
the score comes from these tables.
"""

from __future__ import annotations

import math

SCORECARD_VERSION = "kervan-scorecard-v0.1"

# Annual rate used only to compute a *reference* installment for the coverage
# factor (the actual rate band comes out of the grade, so we avoid circularity).
REFERENCE_ANNUAL_RATE = 0.20

# How much of a factor's points survive depending on verification status.
# Self-declared / unverified inputs count at half weight; inconsistent at zero.
STATUS_WEIGHTS: dict[str, float] = {
    "verified": 1.0,
    "partially_verified": 0.75,
    "unverified": 0.5,
    "inconsistent": 0.0,
}

# Each band: (threshold, points, reason_code, reason_text).
# For "higher is better" factors the first band whose threshold <= value wins.
# For "lower is better" factors the first band whose threshold >= value wins.
FACTORS: list[dict] = [
    {
        "key": "months_documented",
        "label": "Months of documented cash flow",
        "max_points": 20,
        "lower_is_better": False,
        "bands": [
            (12, 20, "MD12", "12 or more months of bank-documented cash flow"),
            (9, 16, "MD09", "9-11 months of documented cash flow"),
            (6, 11, "MD06", "6-8 months of documented cash flow"),
            (3, 4, "MD03", "Only 3-5 months documented (thin file)"),
            (0, 0, "MD00", "Fewer than 3 months documented"),
        ],
    },
    {
        "key": "installment_coverage",
        "label": "Inflow coverage of reference installment",
        "max_points": 25,
        "lower_is_better": False,
        "bands": [
            (6.0, 25, "IC06", "Average monthly inflows cover the installment 6x or more"),
            (4.0, 20, "IC04", "Inflows cover the installment 4-6x"),
            (3.0, 14, "IC03", "Inflows cover the installment 3-4x"),
            (2.0, 7, "IC02", "Inflows cover the installment only 2-3x (tight)"),
            (0.0, 0, "IC00", "Inflows cover the installment less than 2x"),
        ],
    },
    {
        "key": "inflow_stability",
        "label": "Inflow stability (coefficient of variation)",
        "max_points": 15,
        "lower_is_better": True,
        "bands": [
            (0.15, 15, "ST15", "Very stable monthly inflows (CV <= 0.15)"),
            (0.30, 11, "ST30", "Stable monthly inflows (CV <= 0.30)"),
            (0.50, 6, "ST50", "Moderately volatile inflows (CV <= 0.50)"),
            (math.inf, 2, "ST99", "Highly volatile inflows (CV > 0.50)"),
        ],
    },
    {
        "key": "years_in_business",
        "label": "Years in business",
        "max_points": 15,
        "lower_is_better": False,
        "bands": [
            (5, 15, "YB05", "5+ years in business"),
            (3, 11, "YB03", "3-5 years in business"),
            (1, 6, "YB01", "1-3 years in business"),
            (0, 2, "YB00", "Less than 1 year in business"),
        ],
    },
    {
        "key": "verified_share",
        "label": "Share of declared figures verified",
        "max_points": 15,
        "lower_is_better": False,
        "proportional": True,  # points = share * max_points
        "bands": [
            (0.9, 15, "VS90", "Declared figures are fully verified against documents"),
            (0.6, 10, "VS60", "Declared figures are mostly verified"),
            (0.0, 0, "VS00", "Declared figures are largely unverified or inconsistent"),
        ],
    },
    {
        "key": "corroborating_sources",
        "label": "Independent sources that agree",
        "max_points": 10,
        "lower_is_better": False,
        "bands": [
            (3, 10, "CS03", "Three or more independent, consistent document sources"),
            (2, 7, "CS02", "Two consistent document sources"),
            (1, 3, "CS01", "Single document source"),
            (0, 0, "CS00", "No usable document source"),
        ],
    },
]

MAX_POINTS = sum(f["max_points"] for f in FACTORS)  # = 100

# Penalty per inconsistency flag, and a cap on the total penalty.
PENALTIES: dict[str, float] = {
    "info": 0.0,
    "warning": -3.0,
    "critical": -8.0,
    "cap": -20.0,
}

# Grade ladder: first row whose min_points <= total wins. Labels are our own
# (K1 best .. K7 worst) on purpose; they are not rating-agency grades.
GRADES: list[dict] = [
    {"grade": "K1", "min_points": 85, "label": "Excellent", "pd": 0.015, "rate": (0.14, 0.16), "limit_multiple": 6.0, "max_tenor": 36, "decision": "auto_approve_candidate"},
    {"grade": "K2", "min_points": 75, "label": "Strong", "pd": 0.025, "rate": (0.16, 0.18), "limit_multiple": 5.0, "max_tenor": 36, "decision": "auto_approve_candidate"},
    {"grade": "K3", "min_points": 65, "label": "Good", "pd": 0.040, "rate": (0.18, 0.20), "limit_multiple": 4.0, "max_tenor": 24, "decision": "refer_to_underwriter"},
    {"grade": "K4", "min_points": 55, "label": "Fair", "pd": 0.065, "rate": (0.20, 0.23), "limit_multiple": 3.0, "max_tenor": 24, "decision": "refer_to_underwriter"},
    {"grade": "K5", "min_points": 45, "label": "Watch", "pd": 0.100, "rate": (0.23, 0.26), "limit_multiple": 2.0, "max_tenor": 18, "decision": "refer_to_underwriter"},
    {"grade": "K6", "min_points": 35, "label": "Weak", "pd": 0.160, "rate": (0.26, 0.30), "limit_multiple": 1.0, "max_tenor": 12, "decision": "refer_to_underwriter"},
    {"grade": "K7", "min_points": 0, "label": "Thin file / high risk", "pd": 0.250, "rate": None, "limit_multiple": 0.0, "max_tenor": 0, "decision": "decline_candidate"},
]

# Policy knobs used by the improvement simulator.
SIMULATION = {
    "full_history_months": 12,
    "reduce_loan_factor": 0.6,      # "ask for 60% of the amount"
    "tenor_steps": [12, 18, 24, 36],  # extend to the next step
    "route_cash_cap_ratio": 1.5,    # cap on inflow uplift from depositing cash sales
}

GUARDRAILS = [
    {
        "title": "LLM cannot set or change the score",
        "detail": "Gemini reads documents, verifies, explains and writes. The grade comes only from the deterministic scorecard in app/scorecard_config.py; LLM output is never an input to it.",
    },
    {
        "title": "Unverified inputs count less, inconsistent inputs count zero",
        "detail": "Each scorer input carries a verification status. Self-declared figures earn half weight; inconsistent ones earn nothing.",
    },
    {
        "title": "Arithmetic is done in Python, not by the model",
        "detail": "Sums, averages, coefficient of variation, date continuity and outlier checks run deterministically and are merged with Gemini's qualitative checks. Where they disagree, the more conservative status wins.",
    },
    {
        "title": "No invented numbers",
        "detail": "Explanation and memo prompts forbid new figures. Every number in the generated text is checked against the inputs; violations trigger one retry and then a deterministic fallback.",
    },
    {
        "title": "Human approves edge cases",
        "detail": "Grades K3-K6 are routed to an underwriter; the memo is a draft for a human decision, never an automatic approval.",
    },
    {
        "title": "No protected attributes used",
        "detail": "The scorer sees only cash-flow, tenure, verification and request figures. Names, gender, ethnicity, age and location are not scorer inputs.",
    },
]
