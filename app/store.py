"""In-memory store with JSON files on disk (prototype storage, no database)."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.schemas import Assessment

INTERRUPTED_BY_RESTART = "The run was interrupted by a server restart. Please run the assessment again."


class Store:
    def __init__(self) -> None:
        self._items: dict[str, Assessment] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        settings.ensure_dirs()
        for path in sorted(settings.assessments_dir.glob("*.json")):
            try:
                a = Assessment.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if a.status == "running":
                # The process that was running this pipeline is gone; leaving it "running"
                # would make the stream endpoint answer 409 forever.
                a.status = "error"
                a.error = INTERRUPTED_BY_RESTART
                try:
                    path.write_text(a.model_dump_json(indent=1), encoding="utf-8")
                except OSError:
                    pass
            self._items[path.stem] = a
        self._loaded = True

    def put(self, a: Assessment) -> None:
        self._items[a.id] = a
        settings.ensure_dirs()
        Path(settings.assessments_dir / f"{a.id}.json").write_text(a.model_dump_json(indent=1), encoding="utf-8")

    def get(self, assessment_id: str) -> Assessment | None:
        self.load()
        return self._items.get(assessment_id)

    def list(self) -> list[Assessment]:
        self.load()
        return sorted(self._items.values(), key=lambda a: a.created_at, reverse=True)

    def clear(self) -> int:
        """Delete every stored assessment and every uploaded document. The Gemini cache is kept."""
        self.load()
        n = len(self._items)
        self._items.clear()
        for path in settings.assessments_dir.glob("*.json"):
            path.unlink(missing_ok=True)
        if settings.uploads_dir.exists():
            for d in settings.uploads_dir.iterdir():
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
        return n


store = Store()
