"""Runtime configuration for Kervan Credit Passport.

Everything is read from environment variables so the same image runs locally
and on Cloud Run. Nothing secret is stored in the repo.

    GEMINI_API_KEY                 Gemini Developer API key (or GOOGLE_API_KEY)
    GOOGLE_GENAI_USE_VERTEXAI      "true" to use Vertex AI instead of the API key
    GOOGLE_CLOUD_PROJECT           Vertex AI project
    GOOGLE_CLOUD_LOCATION          Vertex AI location (default: global)
    GEMINI_MODEL                   Model ID (default below, overridable)
    GEMINI_TIMEOUT_S               Per-call timeout in seconds (default 90)
    GEMINI_THINKING_LEVEL          "minimal" | "low" | "medium" | "high", or "none" for the model default (default "low")
    KERVAN_DATA_DIR                Where assessments + cache JSON files are written
    KERVAN_OFFLINE                 "1" = developer-only fixture mode, no Gemini calls
    KERVAN_ADMIN_TOKEN             Secret required (X-Admin-Token header) by DELETE /api/assessments;
                                   unset = the reset endpoint is disabled
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Current stable, fast multimodal Gemini model (images + PDFs + text).
# Override with GEMINI_MODEL if Google ships a newer Flash model.
DEFAULT_MODEL = "gemini-3.8-flash"


def _flag(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.gemini_api_key: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.use_vertex: bool = _flag("GOOGLE_GENAI_USE_VERTEXAI")
        self.project: str | None = os.getenv("GOOGLE_CLOUD_PROJECT")
        self.location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
        self.model: str = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.timeout_s: float = float(os.getenv("GEMINI_TIMEOUT_S", "90"))
        # "low" keeps a cold run under a minute; set GEMINI_THINKING_LEVEL=high (or "none" to
        # leave the model default) if you prefer deeper reasoning over speed.
        raw_level = os.getenv("GEMINI_THINKING_LEVEL", "low").strip().lower()
        self.thinking_level: str | None = None if raw_level in {"", "none", "default"} else raw_level
        self.data_dir: Path = Path(os.getenv("KERVAN_DATA_DIR", str(ROOT / "data")))
        self.cache_dir: Path = self.data_dir / "cache"
        self.assessments_dir: Path = self.data_dir / "assessments"
        self.uploads_dir: Path = self.data_dir / "uploads"
        self.fixtures_dir: Path = ROOT / "fixtures" / "personas"  # synthetic test fixtures (tests + offline dev mode only)
        self.prompts_dir: Path = ROOT / "prompts"
        self.static_dir: Path = ROOT / "static"
        self.offline: bool = _flag("KERVAN_OFFLINE")
        self.max_upload_mb: int = int(os.getenv("KERVAN_MAX_UPLOAD_MB", "15"))
        self.admin_token: str | None = (os.getenv("KERVAN_ADMIN_TOKEN") or "").strip() or None

    @property
    def gemini_configured(self) -> bool:
        return self.use_vertex or bool(self.gemini_api_key)

    @property
    def mode(self) -> str:
        if self.offline:
            return "offline-fixtures"
        if self.use_vertex:
            return "vertex-ai"
        if self.gemini_api_key:
            return "gemini-api"
        return "not-configured"

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.assessments_dir, self.uploads_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
