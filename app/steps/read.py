"""Step 1 — READ. Gemini multimodal extraction, one call per document."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from app.config import settings
from app.gemini_client import GeminiStepFailure, gemini, load_prompt, render
from app.schemas import DocumentRead, ReadResult, StepTrace, UploadedDoc
from app.tabular import monthly_totals_from_csv

MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".txt": "text/plain",
}
TEXT_MIMES = {"text/csv", "text/plain", "application/csv"}
MAX_TEXT_CHARS = 60_000

# Month spellings the model emits for real documents: 2025-10, 2025-10-01, 2025.10, 2025/10,
# 10/2025, Oct 2025, October 2025, 2025 оны 10 сар. Each pattern yields (year, month) groups.
_MONTH_PATTERNS = [
    (re.compile(r"^(\d{4})[-./](\d{1,2})(?!\d)"), "ym"),
    (re.compile(r"^(\d{1,2})[-./](\d{4})$"), "my"),
    (re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{4})$"), "name"),
    (re.compile(r"^(\d{4})\s*(?:оны|он)?\s*(\d{1,2})(?!\d)"), "ym"),
]
_MONTH_NAMES = {n: i for i, n in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH_NAMES["sept"] = 9


def parse_month(text: str) -> str | None:
    """Normalise a month label to YYYY-MM; None when it is not a real calendar month."""
    s = (text or "").strip()
    for pattern, kind in _MONTH_PATTERNS:
        m = pattern.match(s)
        if not m:
            continue
        if kind == "ym":
            y, mo = int(m.group(1)), int(m.group(2))
        elif kind == "my":
            mo, y = int(m.group(1)), int(m.group(2))
        else:
            name = m.group(1).lower()
            mo = _MONTH_NAMES.get(name) or _MONTH_NAMES.get(name[:3], 0)
            y = int(m.group(2))
        if 1 <= mo <= 12 and 1900 <= y <= 2100:
            return f"{y:04d}-{mo:02d}"
        return None
    return None


def mime_for(filename: str, fallback: str = "application/octet-stream") -> str:
    return MIME_BY_EXT.get(Path(filename).suffix.lower(), fallback)


def kind_hint(filename: str, mime: str) -> str:
    name = filename.lower()
    if "ledger" in name or "дэвтэр" in name:
        return "sales ledger (photo of a handwritten or printed sales book)"
    if "statement" in name or "bank" in name or "данс" in name:
        return "bank account statement"
    if "payment" in name or "export" in name or "settlement" in name or "pay" in name:
        return "payment-provider settlement export"
    if mime.startswith("image/"):
        return "photo, probably a ledger"
    if mime == "application/pdf":
        return "PDF, probably a bank statement"
    if mime in TEXT_MIMES:
        return "CSV export"
    return "unknown"


def numbered_text(text: str) -> str:
    lines = text.splitlines()
    out = [f"{i} | {line}" for i, line in enumerate(lines, start=1)]
    joined = "\n".join(out)
    if len(joined) > MAX_TEXT_CHARS:
        joined = joined[:MAX_TEXT_CHARS] + "\n... (truncated)"
    return joined


def normalise(result: ReadResult, filename: str) -> ReadResult:
    """Force source pointers to the real file name and months to YYYY-MM."""
    figs = []
    for f in result.monthly_figures:
        month = parse_month(f.month)
        if not month:
            result.quality_notes.append(f"dropped a monthly figure with an unreadable month label '{f.month.strip()[:20]}'")
            continue
        f.month = month
        f.source_document = filename
        if not f.page_or_row:
            f.page_or_row = "n/a"
        figs.append(f)
    # de-duplicate months (keep first occurrence)
    seen: set[str] = set()
    unique = []
    for f in sorted(figs, key=lambda x: x.month):
        if f.month in seen:
            continue
        seen.add(f.month)
        unique.append(f)
    result.monthly_figures = unique
    for ef in result.extracted_fields:
        ef.source_document = filename
    return result


async def read_document(doc: UploadedDoc) -> tuple[DocumentRead, StepTrace]:
    data = Path(doc.path).read_bytes()
    mime = doc.mime_type or mime_for(doc.filename)
    template, version = load_prompt("read")
    prompt = render(template, filename=doc.filename, kind_hint=kind_hint(doc.filename, mime))

    if settings.offline:
        from app import offline

        result, trace = offline.read(doc, data, prompt_version=version)
        return DocumentRead(doc_id=doc.doc_id, filename=doc.filename, mime_type=mime, result=normalise(result, doc.filename)), trace

    if mime in TEXT_MIMES:
        text = data.decode("utf-8", errors="replace")
        parts = [f"--- BEGIN {doc.filename} (text; each line is prefixed with its row number) ---\n{numbered_text(text)}\n--- END {doc.filename} ---"]
        # Arithmetic belongs to Python: hand the model deterministic monthly column sums so it
        # transcribes and cites instead of adding hundreds of rows itself (faster and exact).
        totals = monthly_totals_from_csv(text)
        if totals:
            parts.append(
                "DETERMINISTIC HELP (computed in Python from this same file): monthly sums per numeric column, "
                "with the row ranges they cover. Use these for monthly_figures instead of adding rows yourself, "
                "pick the column that represents inflows (credits / net settlements) and outflows (debits), and cite the row ranges.\n"
                + json.dumps(totals, ensure_ascii=False)
            )
    else:
        parts = [gemini.file_part(data, mime)]

    result, trace = await gemini.generate_json(
        step="read",
        prompt_name="read",
        prompt_version=version,
        prompt_text=prompt,
        schema=ReadResult,
        parts=parts,
        parts_hashes=[doc.sha256],
        input_refs=[doc.filename],
        temperature=0.1,
    )
    return DocumentRead(doc_id=doc.doc_id, filename=doc.filename, mime_type=mime, result=normalise(result, doc.filename)), trace


async def read_all(docs: list[UploadedDoc]) -> tuple[list[DocumentRead], list[StepTrace], list[str]]:
    """Read every document concurrently. Failures are collected, not raised."""
    results = await asyncio.gather(*(read_document(d) for d in docs), return_exceptions=True)
    reads: list[DocumentRead] = []
    traces: list[StepTrace] = []
    errors: list[str] = []
    for doc, res in zip(docs, results):
        if isinstance(res, GeminiStepFailure):
            traces.append(res.trace)
            errors.append(f"{doc.filename}: {res.trace.error}")
        elif isinstance(res, Exception):
            traces.append(StepTrace(step="read", prompt_name="read", prompt_version="?", model=settings.model, latency_ms=0, schema_valid=False, attempts=1, cached=False, input_refs=[doc.filename], error=f"{type(res).__name__}: {res}"))
            errors.append(f"{doc.filename}: {type(res).__name__}: {res}")
        else:
            read, trace = res
            reads.append(read)
            traces.append(trace)
    return reads, traces, errors
