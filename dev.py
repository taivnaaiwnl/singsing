"""Local dev launcher.

    python dev.py            # live Gemini (needs GEMINI_API_KEY or Vertex env vars)
    python dev.py --offline  # developer fixture mode, no Gemini calls

Loads a local .env file if present (never committed).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    load_dotenv(Path(__file__).parent / ".env")
    if "--offline" in sys.argv:
        os.environ["KERVAN_OFFLINE"] = "1"
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8080")), reload="--reload" in sys.argv)
