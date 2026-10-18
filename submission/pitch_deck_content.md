# Kervan Credit Passport — pitch deck content

Paste into the prescribed Google Slides template; one section below per slide. Keep the template's layout and order, and map these sections to its headings (the template was not readable from the machine, so headings may need renaming).

## 1. Title
**Kervan Credit Passport**
An AI underwriting agent that turns a small business's informal records into a verified, explainable credit passport.
Google Cloud AI Builder Cup 2026 · BFSI: Intelligent Risk, Fraud & Financial Experiences · Mongolia

## 2. Problem
- Most Mongolian micro and small businesses keep records in a handwritten ledger, a bank account and a QR payment app.
- Banks cannot underwrite informal records at scale: manual review is slow, declared figures are unverifiable, and decisions are opaque.
- Result: thin-file businesses are refused or overpriced; banks miss good borrowers.

## 3. Solution
- The owner uploads a ledger photo, bank statement and payment export and declares the basics.
- Kervan reads, verifies, scores, explains and writes in under a minute.
- Output: a credit passport (grade K1–K7, PD estimate, rate band, suggested limit), an owner explanation in English and Mongolian, an improvement plan, and an underwriter-ready credit memo with an evidence appendix.

## 4. How it works (agent pipeline)
1. **READ** — Gemini multimodal, structured JSON: monthly figures and extracted fields, each with document + page/row pointer and confidence.
2. **VERIFY** — Python re-sums CSVs, checks month continuity, averages, volatility, declared-vs-bank, ledger-vs-bank, payments-vs-bank, totals, account age; Gemini adds qualitative checks. Conservative status wins.
3. **SCORE** — deterministic scorecard, no LLM. Unverified inputs count half, inconsistent inputs zero.
4. **EXPLAIN** — Gemini writes; expected gains come from re-running the scorer; number guardrail.
5. **WRITE** — Gemini drafts the credit memo; Python renders it with the scorecard table and evidence appendix.

## 5. Core principle: trust by design
- Gemini reads, verifies, explains and writes. It never sets or changes the score.
- Every number in generated text is checked against the inputs; violations retry once, then fall back to deterministic text.
- Evidence trail from every figure to its source; step trace with prompt version, model, latency and schema validation.
- Human approves edge cases; no protected attributes reach the scorer; the business controls identity disclosure to banks.

## 6. Demo (screenshots)
- Business view: live pipeline and passport with status-encoded factor bars and a real inconsistency flag (declared revenue 45% above bank inflows → warning, −3 points).
- Bank view: anonymized passport, Gemini-drafted memo, make offer → identity revealed on acceptance.
- Agent trust view: guardrails, trace, verified inputs, evidence trail.

## 7. Google Cloud and Gemini
- **Gemini 3.8 Flash** via the `google-genai` SDK: `response_schema` + Pydantic validation for all four model steps; results cached per file hash.
- **Cloud Run** hosts the FastAPI service and UI; **Cloud Build** builds from source; **Secret Manager** holds the API key; scales to zero.
- Storage: JSON on the instance for the prototype; **Firestore** is the planned persistence layer for passports and offers.

## 8. Why it is different
- Explainable by construction: a points-based scorecard with reason codes, not a black box.
- Verification, not extraction: declared vs documented, documents vs each other, arithmetic in Python.
- Owner-facing improvement plan with computed, not guessed, gains.
- Works on photos of handwritten Mongolian ledgers and bank CSV/PDF in local formats.

## 9. Impact and market
- Mongolia: hundreds of thousands of informal micro and small businesses; faster, fairer credit decisions and lower underwriting cost for banks.
- Roadmap: Kazakhstan, Uzbekistan, Kyrgyzstan — same pipeline, per-market scorecard config and language notes.

## 10. Next steps
- Login and Identity-Aware Proxy for bank users; Firestore persistence; registration-document checks to fully verify tenure.
- Scorecard calibration on portfolio data with a partner bank; pilot with a Mongolian lender.

## 11. Team and links
- Team: (names, roles)
- Deployed prototype: https://kervan-credit-passport-793182253173.asia-northeast1.run.app
- Repository: (GitHub URL)
- Demo video: (link)
