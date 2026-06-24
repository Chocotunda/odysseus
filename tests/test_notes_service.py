import os
from datetime import datetime, timezone

from core.database import SessionLocal, Note, init_db
import src.note_vault as note_vault
import src.notes_service as notes_service


def test_persist_note_sets_seq_and_writes_vault(tmp_path, monkeypatch):
    init_db()
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    db = SessionLocal()
    try:
        n = Note(id="svc-1", owner="svc-owner", title="Hello", content="body")
        db.add(n)
        notes_service.persist_note(db, n, links=[])
        db.commit()
        assert n.seq >= 1
        assert note_vault.vault_path_for(n).exists()
    finally:
        db.close()


def test_delete_note_soft_deletes_and_removes_vault(tmp_path, monkeypatch):
    init_db()
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    db = SessionLocal()
    try:
        n = Note(id="svc-2", owner="svc-owner", title="Bye", content="body")
        db.add(n)
        notes_service.persist_note(db, n, links=[])
        db.commit()
        path = note_vault.vault_path_for(n)
        assert path.exists()
        notes_service.delete_note(db, n)
        db.commit()
        assert n.deleted_at is not None
        assert not path.exists()
    finally:
        db.close()
