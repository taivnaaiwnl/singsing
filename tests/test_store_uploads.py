"""Storage and upload hygiene: stored file names, reset, interrupted runs, offer validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.schemas import Assessment, DeclaredInfo
from app.store import INTERRUPTED_BY_RESTART, Store


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "assessments_dir", tmp_path / "assessments")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    settings.ensure_dirs()
    return tmp_path


def _assessment(aid="abc123def456", status="created"):
    declared = DeclaredInfo(business_name="Nar LLC", owner_name="Bold Bat", sector="", location="", years_in_business=1,
                            monthly_revenue_mnt=1, loan_amount_mnt=1, tenor_months=6, purpose="")
    return Assessment(id=aid, created_at="2026-01-01T00:00:00+00:00", declared=declared, status=status)


def test_cyrillic_and_duplicate_filenames_do_not_collide(data_dir):
    from app.main import _store_file, safe_filename

    assert safe_filename("Хаан банк хуулга.pdf") == "Хаан банк хуулга.pdf"
    assert safe_filename("../../etc/passwd.csv") == "passwd.csv"
    assert safe_filename('a<b>:"|?*.csv') == "a_b_.csv"
    d1 = _store_file("aid", "Хаан банк хуулга.pdf", b"%PDF KHAN")
    d2 = _store_file("aid", "Голомт банк хуулга.pdf", b"%PDF GOLOMT")
    d3 = _store_file("aid", "statement.pdf", b"one")
    d4 = _store_file("aid", "statement.pdf", b"two")
    assert d1.filename == "Хаан банк хуулга.pdf" and d2.filename == "Голомт банк хуулга.pdf"
    assert len({d.path for d in (d1, d2, d3, d4)}) == 4
    assert Path(d1.path).read_bytes() == b"%PDF KHAN"
    assert Path(d3.path).read_bytes() == b"one" and Path(d4.path).read_bytes() == b"two"
    assert Path(d1.path).name.startswith(d1.doc_id + "_")


def test_clear_removes_uploaded_documents_too(data_dir):
    from app.main import _store_file

    store = Store()
    a = _assessment()
    doc = _store_file(a.id, "bank_statement.csv", b"Date,Credit\n")
    a.documents.append(doc)
    store.put(a)
    _store_file("adhoc", "x.csv", b"Date,Credit\n")
    assert store.clear() == 1
    assert store.get(a.id) is None
    assert not Path(doc.path).exists()
    assert not (settings.uploads_dir / a.id).exists()
    assert not (settings.uploads_dir / "adhoc").exists()
    assert settings.assessments_dir.exists() and not list(settings.assessments_dir.glob("*.json"))


def test_running_assessment_is_reloaded_as_error(data_dir):
    a = _assessment(status="running")
    path = settings.assessments_dir / f"{a.id}.json"
    path.write_text(a.model_dump_json(indent=1), encoding="utf-8")
    store = Store()
    loaded = store.get(a.id)
    assert loaded.status == "error"
    assert loaded.error == INTERRUPTED_BY_RESTART
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "error"


def test_persona_field_is_gone_but_old_files_still_load():
    raw = _assessment().model_dump()
    raw["persona"] = "persona_a"
    a = Assessment.model_validate(raw)
    assert "persona" not in a.model_dump()


def test_offer_validation():
    from pydantic import ValidationError

    from app.main import OfferIn

    ok = OfferIn(bank_name="  Khan Bank ", amount_mnt=5_000_000, tenor_months=12, annual_rate=0.18)
    assert ok.bank_name == "Khan Bank"
    for bad in (
        {"bank_name": "   ", "amount_mnt": 1, "tenor_months": 1, "annual_rate": 0.1},
        {"bank_name": "B", "amount_mnt": -5, "tenor_months": 1, "annual_rate": 0.1},
        {"bank_name": "B", "amount_mnt": 1, "tenor_months": 0, "annual_rate": 0.1},
        {"bank_name": "B", "amount_mnt": 1, "tenor_months": 1, "annual_rate": 5},
    ):
        with pytest.raises(ValidationError):
            OfferIn(**bad)


def test_anonymize_with_blank_business_name_leaves_text_intact():
    from app.main import _anonymize_text

    a = _assessment()
    a.declared.business_name = ""
    assert _anonymize_text("<h1>Credit memo</h1>", a) == "<h1>Credit memo</h1>"
    a.declared.business_name = "Nar LLC"
    assert _anonymize_text("Memo for Nar LLC", a) == "Memo for [business — identity hidden]"
