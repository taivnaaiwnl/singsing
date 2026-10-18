"""Orchestrates READ → VERIFY → SCORE → EXPLAIN → WRITE and emits progress events.

Events are plain dicts: {"step", "status": running|done|error|skipped, "message", "latency_ms", "data"}.
The SSE endpoint in app/main.py streams them to the UI.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from app import scorecard_config as cfg
from app.config import settings
from app.schemas import Assessment, StepTrace
from app.steps.explain import explain
from app.steps.read import read_all
from app.steps.score import compute_score, inputs_from_verification, simulate_improvements
from app.steps.verify import verify
from app.steps.write import write_memo
from app.store import store

Emit = Callable[[dict], Awaitable[None]]


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def friendly(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    if "not configured" in text:
        return "Gemini is not configured on the server (set GEMINI_API_KEY)."
    if "Timeout" in text or "timeout" in text:
        return "The model took too long to answer. Please run the assessment again."
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "The model is rate-limited right now. Please wait a moment and run again."
    return text[:300]


async def run_pipeline(a: Assessment, emit: Emit) -> Assessment:
    a.status = "running"
    a.error = None
    a.mode = settings.mode
    a.trace = []
    model_label = "offline fixtures" if settings.offline else settings.model
    store.put(a)

    # ---------------- READ ----------------
    t0 = time.perf_counter()
    await emit({"step": "read", "status": "running", "message": f"Gemini ({model_label}) is reading {len(a.documents)} document(s) with structured output…"})
    reads, traces, errors = await read_all(a.documents)
    a.reads = reads
    a.trace.extend(traces)
    if not reads:
        a.status = "error"
        a.error = "Could not read any document. " + "; ".join(errors)
        store.put(a)
        await emit({"step": "read", "status": "error", "message": friendly(Exception(a.error)), "latency_ms": _ms(t0)})
        await emit({"step": "pipeline", "status": "error", "message": a.error})
        return a
    summary = ", ".join(f"{r.filename} → {r.result.document_type.replace('_', ' ')} ({len(r.result.monthly_figures)} months, {len(r.result.extracted_fields)} fields)" for r in reads)
    if errors:
        summary += f"; failed: {'; '.join(errors)}"
    store.put(a)
    await emit({
        "step": "read", "status": "done", "latency_ms": _ms(t0),
        "message": f"Read {len(reads)}/{len(a.documents)} documents. {summary}",
        "data": {"documents": [{"filename": r.filename, "type": r.result.document_type, "months": len(r.result.monthly_figures), "language": r.result.language, "quality_notes": r.result.quality_notes} for r in reads]},
    })

    # ---------------- VERIFY ----------------
    t0 = time.perf_counter()
    await emit({"step": "verify", "status": "running", "message": "Python re-computes sums, continuity and ratios; Gemini adds qualitative checks…"})
    try:
        v, vtrace = await verify(a.declared, a.reads, a.documents)
    except Exception as exc:  # deterministic half should never fail, but never crash the view
        a.status = "error"
        a.error = f"VERIFY failed: {exc}"
        store.put(a)
        await emit({"step": "verify", "status": "error", "message": friendly(exc), "latency_ms": _ms(t0)})
        await emit({"step": "pipeline", "status": "error", "message": a.error})
        return a
    a.verify = v
    a.trace.append(vtrace)
    store.put(a)
    n_warn = len([f for f in v.flags if f.severity in {"warning", "critical"}])
    status_counts = {}
    for c in v.checks:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1
    await emit({
        "step": "verify", "status": "done", "latency_ms": _ms(t0),
        "message": f"{len(v.checks)} checks ({', '.join(f'{k.replace('_', ' ')} {n}' for k, n in status_counts.items())}); {len(v.flags)} flags, {n_warn} warning/critical."
                   + (" Gemini verification unavailable — deterministic checks only." if vtrace.error else ""),
        "data": {"flags": [{"severity": f.severity, "message": f.message, "source": f.source} for f in v.flags], "metrics": v.metrics},
    })

    # ---------------- SCORE (deterministic) ----------------
    t0 = time.perf_counter()
    await emit({"step": "score", "status": "running", "message": "Deterministic scorecard over verified inputs (no LLM)…"})
    inputs = inputs_from_verification(v, a.declared)
    # Only deterministic flags carry penalties: model observations never move the score.
    inputs.flags = [f for f in inputs.flags if f.code and not f.code.startswith("MODEL_OBS")]
    det_flag_codes = {f.code for f in v.flags if f.source == "deterministic"}
    inputs.flags = [f for f in inputs.flags if f.code in det_flag_codes]
    score = compute_score(inputs)
    sims = simulate_improvements(inputs, score)
    a.score = score
    a.simulations = sims
    a.trace.append(StepTrace(
        step="score", prompt_name="scorecard_config.py", prompt_version=cfg.SCORECARD_VERSION, model="deterministic (no LLM)",
        latency_ms=_ms(t0), schema_valid=True, attempts=1, cached=False,
        input_refs=[f"{i.name}={i.value:g} [{i.status}]" for i in v.verified_inputs],
        note=f"{len(sims)} improvement simulations by re-running the scorer",
    ))
    store.put(a)
    await emit({
        "step": "score", "status": "done", "latency_ms": _ms(t0),
        "message": f"Grade {score.grade} ({score.grade_label}) — {score.total_points:g}/{score.max_points:g} points · PD estimate {score.pd_illustrative:.1%} · rate band {score.rate_band} · {len(sims)} improvement action(s) simulated by re-running the scorer.",
        "data": {"grade": score.grade, "points": score.total_points, "pd": score.pd_illustrative, "rate_band": score.rate_band},
    })

    # ---------------- EXPLAIN ----------------
    t0 = time.perf_counter()
    await emit({"step": "explain", "status": "running", "message": "Gemini writes the owner explanation; number guardrail checks every figure…"})
    try:
        ex, etrace = await explain(a)
        a.explain = ex
        a.trace.append(etrace)
        store.put(a)
        g = ex.guardrail
        await emit({
            "step": "explain", "status": "done", "latency_ms": _ms(t0),
            "message": f"Owner summary + {len(ex.improvement_plan)} ranked actions. Guardrail: {g.numbers_checked} numbers checked"
                       + (f", fallback used ({g.note})" if g.fallback_used else ", all traceable to inputs" if not g.violations else f", {len(g.violations)} flagged"),
        })
    except Exception as exc:
        a.trace.append(StepTrace(step="explain", prompt_name="explain", prompt_version="?", model=settings.model, latency_ms=_ms(t0), schema_valid=False, attempts=1, cached=False, error=str(exc)[:300]))
        store.put(a)
        await emit({"step": "explain", "status": "error", "message": friendly(exc), "latency_ms": _ms(t0)})

    # ---------------- WRITE ----------------
    t0 = time.perf_counter()
    await emit({"step": "write", "status": "running", "message": "Gemini drafts the underwriter credit memo with an evidence appendix…"})
    try:
        w, wtrace = await write_memo(a)
        a.write = w
        a.trace.append(wtrace)
        store.put(a)
        await emit({
            "step": "write", "status": "done", "latency_ms": _ms(t0),
            "message": f"Credit memo drafted: {len(w.memo.key_risks)} risks, {len(w.memo.mitigants)} mitigants, {len(w.memo.evidence_appendix)} evidence rows. Guardrail: {w.guardrail.note}",
        })
    except Exception as exc:
        a.trace.append(StepTrace(step="write", prompt_name="write", prompt_version="?", model=settings.model, latency_ms=_ms(t0), schema_valid=False, attempts=1, cached=False, error=str(exc)[:300]))
        store.put(a)
        await emit({"step": "write", "status": "error", "message": friendly(exc), "latency_ms": _ms(t0)})

    a.status = "done"
    store.put(a)
    total_ms = sum(t.latency_ms for t in a.trace)
    await emit({"step": "pipeline", "status": "done", "message": f"Assessment complete in {total_ms / 1000:.1f}s of model/compute time.", "assessment_id": a.id})
    return a
