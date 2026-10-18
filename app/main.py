"""Kervan Credit Passport — FastAPI app.

Routes
    GET  /                                   UI
    GET  /api/health (and /healthz locally)  liveness + mode/model
    GET  /api/config                         mode, model, scorecard summary, guardrails
    POST /api/assessments                    create from form + uploaded files
    DELETE /api/assessments?confirm=yes      reset all assessments (requires X-Admin-Token = KERVAN_ADMIN_TOKEN)
    GET  /api/assessments                    list (summaries)
    GET  /api/assessments/{id}               full assessment (business + agent-trust views)
    GET  /api/assessments/{id}/stream        run the pipeline, stream progress (SSE)
    GET  /api/assessments/{id}/documents/{doc_id}   serve an uploaded document
    GET  /api/bank/passports                 anonymized list for the bank view
    GET  /api/bank/passports/{id}            anonymized passport + memo
    POST /api/assessments/{id}/offers        bank makes an offer
    POST /api/assessments/{id}/offers/{oid}/{accept|decline}   business responds
    POST /api/steps/score                    what-if: scorer over explicit inputs
    POST /api/steps/read                     run READ on one uploaded file
    POST /api/assessments/{id}/steps/{step}  re-run verify|score|explain|write on stored state
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app import scorecard_config as cfg
from app.config import settings
from app.pipeline import run_pipeline
from app.schemas import Assessment, DeclaredInfo, Offer, ScorerInputs, UploadedDoc
from app.steps.read import mime_for, read_document
from app.steps.score import compute_score, inputs_from_verification, simulate_improvements
from app.store import store

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".csv", ".txt"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    store.load()
    print(f"[kervan] mode={settings.mode} model={settings.model} data_dir={settings.data_dir}")
    yield


app = FastAPI(title="Kervan Credit Passport", version="0.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _alias(a: Assessment) -> str:
    return f"KCP-{a.id[:4].upper()}"


def safe_filename(filename: str) -> str:
    """Keep Unicode letters (Cyrillic file names are the norm), drop path parts and reserved characters."""
    name = Path(filename.replace("\\", "/")).name
    return re.sub(r"[^\w.\- ]+", "_", name).strip() or "file"


def _store_file(assessment_id: str, filename: str, data: bytes) -> UploadedDoc:
    safe = safe_filename(filename)
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"{safe} is larger than {settings.max_upload_mb} MB")
    folder = settings.uploads_dir / assessment_id
    folder.mkdir(parents=True, exist_ok=True)
    # Prefix the stored file with the document id so two uploads with the same name
    # (statement.pdf from two banks) never overwrite each other on disk.
    doc_id = uuid.uuid4().hex[:8]
    path = folder / f"{doc_id}_{safe}"
    path.write_bytes(data)
    return UploadedDoc(doc_id=doc_id, filename=safe, mime_type=mime_for(safe), size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), path=str(path))


def _get(assessment_id: str) -> Assessment:
    a = store.get(assessment_id)
    if not a:
        raise HTTPException(404, "Assessment not found")
    return a


def _summary(a: Assessment) -> dict:
    return {
        "id": a.id,
        "alias": _alias(a),
        "created_at": a.created_at,
        "status": a.status,
        "business_name": a.declared.business_name,
        "sector": a.declared.sector,
        "location": a.declared.location,
        "loan_amount_mnt": a.declared.loan_amount_mnt,
        "tenor_months": a.declared.tenor_months,
        "grade": a.score.grade if a.score else None,
        "points": a.score.total_points if a.score else None,
        "offers": len(a.offers),
        "identity_revealed": a.identity_revealed,
        "mode": a.mode,
    }


def _anonymize_text(text: str, a: Assessment) -> str:
    if a.identity_revealed:
        return text
    name = a.declared.business_name.strip()
    out = text.replace(name, "[business — identity hidden]") if name else text
    owner = a.declared.owner_name.strip()
    if owner:
        out = out.replace(owner, "[owner — identity hidden]")
        first = owner.split()[0]
        if len(first) > 3:
            out = out.replace(first, "[owner]")
    return out


def _anonymize_obj(obj, a: Assessment):
    """Apply _anonymize_text to every string inside a dict/list payload."""
    if isinstance(obj, str):
        return _anonymize_text(obj, a)
    if isinstance(obj, list):
        return [_anonymize_obj(x, a) for x in obj]
    if isinstance(obj, dict):
        return {k: _anonymize_obj(v, a) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------- #
# Static + health
# --------------------------------------------------------------------------- #
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((settings.static_dir / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
@app.get("/healthz")  # note: Google's front end answers /healthz on *.run.app itself, so use /api/health there
async def healthz() -> dict:
    return {"status": "ok", "mode": settings.mode, "model": settings.model, "time": _now()}


@app.get("/api/config")
async def config() -> dict:
    return {
        "mode": settings.mode,
        "model": "offline-fixture" if settings.offline else settings.model,
        "gemini_configured": settings.gemini_configured or settings.offline,
        "scorecard": {
            "version": cfg.SCORECARD_VERSION,
            "max_points": cfg.MAX_POINTS,
            "factors": [{"key": f["key"], "label": f["label"], "max_points": f["max_points"]} for f in cfg.FACTORS],
            "grades": [{"grade": g["grade"], "min_points": g["min_points"], "label": g["label"], "pd": g["pd"], "rate": g["rate"], "max_tenor": g["max_tenor"], "decision": g["decision"]} for g in cfg.GRADES],
            "status_weights": cfg.STATUS_WEIGHTS,
            "penalties": cfg.PENALTIES,
        },
        "guardrails": cfg.GUARDRAILS,
        "prompts": [p.name for p in sorted(settings.prompts_dir.glob("*.txt"))],
    }


# --------------------------------------------------------------------------- #
# Assessments
# --------------------------------------------------------------------------- #
@app.post("/api/assessments")
async def create_assessment(
    business_name: str = Form(...),
    owner_name: str = Form(""),
    sector: str = Form(""),
    location: str = Form(""),
    years_in_business: float = Form(...),
    monthly_revenue_mnt: float = Form(...),
    loan_amount_mnt: float = Form(...),
    tenor_months: int = Form(...),
    purpose: str = Form(""),
    files: list[UploadFile] = File(default=[]),
) -> dict:
    if not business_name.strip():
        raise HTTPException(400, "Business name is required.")
    if not files:
        raise HTTPException(400, "Upload at least one document (ledger photo, bank statement or payment export).")
    aid = _new_id()
    declared = DeclaredInfo(
        business_name=business_name.strip(), owner_name=owner_name.strip(), sector=sector.strip(), location=location.strip(),
        years_in_business=years_in_business, monthly_revenue_mnt=monthly_revenue_mnt, loan_amount_mnt=loan_amount_mnt,
        tenor_months=tenor_months, purpose=purpose.strip(),
    )
    docs = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        docs.append(_store_file(aid, f.filename or "upload", data))
    if not docs:
        raise HTTPException(400, "All uploaded files were empty.")
    a = Assessment(id=aid, created_at=_now(), declared=declared, documents=docs, mode=settings.mode)
    store.put(a)
    return {"id": aid, "documents": [d.model_dump(exclude={"path"}) for d in docs]}


@app.get("/api/assessments")
async def list_assessments() -> dict:
    return {"assessments": [_summary(a) for a in store.list()]}


@app.delete("/api/assessments")
async def clear_assessments(confirm: str = "", x_admin_token: str = Header("")) -> dict:
    """Reset: removes all assessments and uploaded documents (keeps the Gemini cache).

    Requires the X-Admin-Token header to equal KERVAN_ADMIN_TOKEN; with the variable unset
    the endpoint is disabled, so an anonymous visitor can never wipe real assessments.
    """
    if not settings.admin_token or not hmac.compare_digest(x_admin_token.encode("utf-8"), settings.admin_token.encode("utf-8")):
        raise HTTPException(403, "Admin token required")
    if confirm != "yes":
        raise HTTPException(400, "Add ?confirm=yes to clear all assessments")
    return {"cleared": store.clear()}


@app.get("/api/assessments/{assessment_id}")
async def get_assessment(assessment_id: str) -> dict:
    a = _get(assessment_id)
    data = a.model_dump(mode="json")
    data["alias"] = _alias(a)
    for d in data["documents"]:
        d.pop("path", None)
    return data


@app.get("/api/assessments/{assessment_id}/documents/{doc_id}")
async def get_document(assessment_id: str, doc_id: str):
    a = _get(assessment_id)
    doc = next((d for d in a.documents if d.doc_id == doc_id), None)
    if not doc or not Path(doc.path).exists():
        raise HTTPException(404, "Document not found")
    return FileResponse(doc.path, media_type=doc.mime_type, filename=doc.filename)


@app.get("/api/assessments/{assessment_id}/stream")
async def stream_assessment(assessment_id: str):
    a = _get(assessment_id)
    if a.status == "running":
        raise HTTPException(409, "This assessment is already running")
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: dict) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            await run_pipeline(a, emit)
        except Exception as exc:  # never crash the pipeline view
            a.status = "error"
            a.error = f"{type(exc).__name__}: {exc}"
            store.put(a)
            await queue.put({"step": "pipeline", "status": "error", "message": f"Unexpected error: {str(exc)[:300]}"})
        finally:
            if a.status == "running":  # cancelled (client gone, shutdown): never leave it stuck in "running"
                a.status = "error"
                a.error = "The run was interrupted (connection closed). Please run the assessment again."
                store.put(a)
            await queue.put(None)

    task = asyncio.create_task(runner())

    async def gen():
        yield ": connected\n\n"
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev is None:
                    break
                yield f"data: {json.dumps(ev, default=str, ensure_ascii=False)}\n\n"
        finally:
            # A client disconnect cancels this generator; shield the pipeline so a page refresh
            # mid-run lets the assessment finish in the background instead of killing it.
            if not task.done():
                await asyncio.shield(task)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# --------------------------------------------------------------------------- #
# Bank view
# --------------------------------------------------------------------------- #
def _bank_view(a: Assessment, full: bool) -> dict:
    d = a.declared
    out = {
        "id": a.id,
        "alias": _alias(a),
        "created_at": a.created_at,
        "status": a.status,
        "identity_revealed": a.identity_revealed,
        "business_name": d.business_name if a.identity_revealed else None,
        "owner_name": d.owner_name if a.identity_revealed else None,
        "sector": d.sector,
        "location": d.location,
        "years_in_business": d.years_in_business,
        "loan_amount_mnt": d.loan_amount_mnt,
        "tenor_months": d.tenor_months,
        "purpose": d.purpose,
        "grade": a.score.grade if a.score else None,
        "grade_label": a.score.grade_label if a.score else None,
        "points": a.score.total_points if a.score else None,
        "pd": a.score.pd_illustrative if a.score else None,
        "rate_band": a.score.rate_band if a.score else None,
        "rate_min": a.score.rate_min if a.score else None,
        "rate_max": a.score.rate_max if a.score else None,
        "suggested_limit_mnt": a.score.suggested_limit_mnt if a.score else None,
        "suggested_tenor_months": a.score.suggested_tenor_months if a.score else None,
        "decision_hint": a.score.decision_hint if a.score else None,
        "flags": [f.model_dump() for f in a.verify.flags] if a.verify else [],
        "offers": [o.model_dump() for o in a.offers],
        "documents": [{"filename": x.filename, "type": next((r.result.document_type for r in a.reads if r.doc_id == x.doc_id), None)} for x in a.documents],
    }
    out["flags"] = _anonymize_obj(out["flags"], a)
    if full:
        out["factors"] = [f.model_dump() for f in a.score.factors] if a.score else []
        out["penalties"] = [p.model_dump() for p in a.score.penalties] if a.score else []
        out["checks"] = _anonymize_obj([c.model_dump() for c in a.verify.checks] if a.verify else [], a)
        out["verified_inputs"] = _anonymize_obj([v.model_dump() for v in a.verify.verified_inputs] if a.verify else [], a)
        out["memo_html"] = _anonymize_text(a.write.html, a) if a.write else None
        out["memo_markdown"] = _anonymize_text(a.write.markdown, a) if a.write else None
        out["memo_guardrail"] = a.write.guardrail.model_dump() if a.write else None
        out["monthly_series"] = a.verify.monthly_series if a.verify else []
    return out


@app.get("/api/bank/passports")
async def bank_passports() -> dict:
    return {"passports": [_bank_view(a, full=False) for a in store.list() if a.score]}


@app.get("/api/bank/passports/{assessment_id}")
async def bank_passport(assessment_id: str) -> dict:
    return _bank_view(_get(assessment_id), full=True)


class OfferIn(BaseModel):
    bank_name: str = Field(min_length=1, max_length=200)
    amount_mnt: float = Field(gt=0)
    tenor_months: int = Field(ge=1, le=60)
    annual_rate: float = Field(ge=0, le=1, description="Annual rate as a fraction, e.g. 0.18 for 18% p.a.")

    @field_validator("bank_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("bank_name is required")
        return v


@app.post("/api/assessments/{assessment_id}/offers")
async def make_offer(assessment_id: str, body: OfferIn) -> dict:
    a = _get(assessment_id)
    if not a.score:
        raise HTTPException(400, "Run the assessment first")
    offer = Offer(id=_new_id()[:8], bank_name=body.bank_name, amount_mnt=body.amount_mnt, tenor_months=body.tenor_months, annual_rate=body.annual_rate)
    a.offers.append(offer)
    store.put(a)
    return {"offer": offer.model_dump(), "identity_revealed": a.identity_revealed}


@app.post("/api/assessments/{assessment_id}/offers/{offer_id}/{action}")
async def respond_offer(assessment_id: str, offer_id: str, action: str) -> dict:
    a = _get(assessment_id)
    offer = next((o for o in a.offers if o.id == offer_id), None)
    if not offer:
        raise HTTPException(404, "Offer not found")
    if action == "accept":
        offer.status = "accepted"
        a.identity_revealed = True
    elif action == "decline":
        offer.status = "declined"
    else:
        raise HTTPException(400, "action must be accept or decline")
    store.put(a)
    return {"offer": offer.model_dump(), "identity_revealed": a.identity_revealed}


# --------------------------------------------------------------------------- #
# Individual step endpoints
# --------------------------------------------------------------------------- #
@app.post("/api/steps/score")
async def step_score(inputs: ScorerInputs) -> dict:
    score = compute_score(inputs)
    return {"score": score.model_dump(mode="json"), "simulations": [s.model_dump() for s in simulate_improvements(inputs, score)]}


@app.post("/api/steps/read")
async def step_read(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    doc = _store_file("adhoc", file.filename or "upload", data)
    try:
        read, trace = await read_document(doc)
    except Exception as exc:
        raise HTTPException(502, f"READ failed: {exc}")
    return {"read": read.model_dump(mode="json"), "trace": trace.model_dump(mode="json")}


@app.post("/api/assessments/{assessment_id}/steps/{step}")
async def rerun_step(assessment_id: str, step: str) -> dict:
    a = _get(assessment_id)
    if step == "verify":
        from app.steps.verify import verify

        if not a.reads:
            raise HTTPException(400, "Run READ first")
        a.verify, trace = await verify(a.declared, a.reads, a.documents)
    elif step == "score":
        if not a.verify:
            raise HTTPException(400, "Run VERIFY first")
        inputs = inputs_from_verification(a.verify, a.declared)
        det_codes = {f.code for f in a.verify.flags if f.source == "deterministic"}
        inputs.flags = [f for f in inputs.flags if f.code in det_codes]
        a.score = compute_score(inputs)
        a.simulations = simulate_improvements(inputs, a.score)
        trace = None
    elif step == "explain":
        from app.steps.explain import explain

        if not a.score:
            raise HTTPException(400, "Run SCORE first")
        a.explain, trace = await explain(a)
    elif step == "write":
        from app.steps.write import write_memo

        if not a.score:
            raise HTTPException(400, "Run SCORE first")
        a.write, trace = await write_memo(a)
    else:
        raise HTTPException(400, "step must be verify, score, explain or write")
    if trace:
        a.trace.append(trace)
    store.put(a)
    return {"ok": True, "step": step, "trace": trace.model_dump(mode="json") if trace else None}


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {str(exc)[:300]}"})
