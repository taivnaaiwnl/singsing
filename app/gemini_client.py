"""Single wrapper around the google-genai SDK.

Every Gemini call in the app goes through `GeminiClient.generate_json`:

* structured output (`response_mime_type="application/json"` + `response_schema`)
* Pydantic validation; on failure, ONE retry that feeds the validation error back
* per-call timeout
* a disk cache keyed by (model, prompt version, prompt text, input hashes)
* a `StepTrace` with latency, attempts, schema-validation result and cache status
  for the Agent-trust view
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas import StepTrace

T = TypeVar("T", bound=BaseModel)

_VERSION_RE = re.compile(r"^#\s*version:\s*(\S+)", re.I)


class GeminiError(Exception):
    """Raised when a step cannot get a valid structured response."""


def load_prompt(name: str) -> tuple[str, str]:
    """Return (prompt_text, version) for prompts/<name>.txt."""
    path = settings.prompts_dir / f"{name}.txt"
    raw = path.read_text(encoding="utf-8")
    version = "0"
    lines = []
    for line in raw.splitlines():
        m = _VERSION_RE.match(line)
        if m and version == "0":
            version = m.group(1)
            continue
        lines.append(line)
    return "\n".join(lines).strip(), version


def render(template: str, **values: Any) -> str:
    out = template
    for key, val in values.items():
        if not isinstance(val, str):
            val = json.dumps(val, ensure_ascii=False, indent=2, default=str)
        out = out.replace("{{" + key + "}}", val)
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class GeminiClient:
    def __init__(self) -> None:
        self._client = None

    # ------------------------------------------------------------------ #
    def _get_client(self):
        if self._client is None:
            from google import genai
            from google.genai import types

            http_options = types.HttpOptions(timeout=int(settings.timeout_s * 1000))
            if settings.use_vertex:
                self._client = genai.Client(
                    vertexai=True,
                    project=settings.project,
                    location=settings.location,
                    http_options=http_options,
                )
            else:
                if not settings.gemini_api_key:
                    raise GeminiError(
                        "Gemini is not configured. Set GEMINI_API_KEY, or GOOGLE_GENAI_USE_VERTEXAI=true with "
                        "GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION."
                    )
                self._client = genai.Client(api_key=settings.gemini_api_key, http_options=http_options)
        return self._client

    @staticmethod
    def file_part(data: bytes, mime_type: str):
        from google.genai import types

        return types.Part.from_bytes(data=data, mime_type=mime_type)

    # ------------------------------------------------------------------ #
    def _cache_path(self, key: str) -> Path:
        return settings.cache_dir / f"{key}.json"

    def _cache_key(self, *, prompt_name: str, prompt_version: str, prompt_text: str, schema_name: str, parts_hashes: list[str]) -> str:
        payload = json.dumps(
            {
                "model": settings.model,
                "thinking_level": settings.thinking_level,
                "prompt_name": prompt_name,
                "prompt_version": prompt_version,
                "prompt_sha": sha256_bytes(prompt_text.encode("utf-8")),
                "schema": schema_name,
                "parts": parts_hashes,
            },
            sort_keys=True,
        )
        return sha256_bytes(payload.encode("utf-8"))[:32]

    # ------------------------------------------------------------------ #
    async def generate_json(
        self,
        *,
        step: str,
        prompt_name: str,
        prompt_version: str,
        prompt_text: str,
        schema: type[T],
        parts: list[Any] | None = None,
        parts_hashes: list[str] | None = None,
        input_refs: list[str] | None = None,
        temperature: float = 0.2,
        use_cache: bool = True,
    ) -> tuple[T, StepTrace]:
        """Call Gemini with structured output and validate against `schema`.

        `parts` are extra content parts (file bytes as Part objects or strings)
        placed before the prompt text. `parts_hashes` identify them for caching.
        """
        parts = parts or []
        parts_hashes = parts_hashes or []
        input_refs = input_refs or []
        settings.ensure_dirs()
        key = self._cache_key(
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            prompt_text=prompt_text,
            schema_name=schema.__name__,
            parts_hashes=parts_hashes,
        )
        cache_file = self._cache_path(key)
        started = datetime.now(timezone.utc).isoformat()

        if use_cache and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                obj = schema.model_validate(cached["data"])
                return obj, StepTrace(
                    step=step,
                    prompt_name=prompt_name,
                    prompt_version=prompt_version,
                    model=cached.get("model", settings.model),
                    latency_ms=0,
                    schema_valid=True,
                    attempts=0,
                    cached=True,
                    input_refs=input_refs,
                    started_at=started,
                    note=f"cache hit (original call {cached.get('latency_ms', '?')} ms, {cached.get('created_at', '')[:19]})",
                )
            except Exception:
                cache_file.unlink(missing_ok=True)

        from google.genai import types

        client = self._get_client()
        config_kwargs: dict[str, Any] = dict(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
        )
        if settings.thinking_level:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=settings.thinking_level)
        config = types.GenerateContentConfig(**config_kwargs)

        t0 = time.perf_counter()
        attempts = 0
        last_error: str | None = None
        contents: list[Any] = [*parts, prompt_text]
        while attempts < 2:
            attempts += 1
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(model=settings.model, contents=contents, config=config),
                    timeout=settings.timeout_s + 5,
                )
                text = response.text
                if not text:
                    raise GeminiError("Empty response from model (possibly blocked)")
                data = json.loads(text)
                obj = schema.model_validate(data)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                if use_cache:
                    cache_file.write_text(
                        json.dumps(
                            {
                                "data": obj.model_dump(mode="json"),
                                "model": settings.model,
                                "latency_ms": latency_ms,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                                "prompt_name": prompt_name,
                                "prompt_version": prompt_version,
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                return obj, StepTrace(
                    step=step,
                    prompt_name=prompt_name,
                    prompt_version=prompt_version,
                    model=settings.model,
                    latency_ms=latency_ms,
                    schema_valid=True,
                    attempts=attempts,
                    cached=False,
                    input_refs=input_refs,
                    started_at=started,
                    note="" if attempts == 1 else f"validated on retry after: {last_error[:160] if last_error else ''}",
                )
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:800]}"
                contents = [
                    *parts,
                    prompt_text,
                    "\n\nYour previous answer did not validate against the required JSON schema. "
                    f"Error:\n{last_error}\n\nReturn the corrected JSON only.",
                ]
            except asyncio.TimeoutError:
                last_error = f"Timeout after {settings.timeout_s:.0f}s"
            except Exception as exc:  # API / network errors: one retry
                last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                await asyncio.sleep(1.0)

        latency_ms = int((time.perf_counter() - t0) * 1000)
        trace = StepTrace(
            step=step,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            model=settings.model,
            latency_ms=latency_ms,
            schema_valid=False,
            attempts=attempts,
            cached=False,
            input_refs=input_refs,
            started_at=started,
            error=last_error,
        )
        raise GeminiStepFailure(trace)


class GeminiStepFailure(GeminiError):
    def __init__(self, trace: StepTrace) -> None:
        super().__init__(trace.error or "Gemini step failed")
        self.trace = trace


gemini = GeminiClient()
