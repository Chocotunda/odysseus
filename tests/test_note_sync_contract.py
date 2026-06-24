from datetime import datetime, timezone

from core.database import SessionLocal, Note, init_db
from core import hub_models
import src.notes_service as notes_service


def _new_note(db, owner, title):
    n = Note(id=f"n-{title}", owner=owner, title=title, content="x")
    n.seq = hub_models.next_note_seq(db, owner)
    db.add(n)
    db.commit()
    return n


def test_next_note_seq_is_monotonic_per_owner():
    init_db()
    db = SessionLocal()
    try:
        a1 = _new_note(db, "owner-seq-a", "a1").seq
        a2 = _new_note(db, "owner-seq-a", "a2").seq
        b1 = _new_note(db, "owner-seq-b", "b1").seq
        assert a2 > a1
        assert b1 >= 1            # per-owner stream starts independently
    finally:
        db.close()


def test_soft_deleted_note_excluded_from_list(monkeypatch, tmp_path):
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    init_db()
    db = SessionLocal()
    try:
        n = Note(id="del-list-1", owner="del-owner", title="t", content="c")
        db.add(n)
        notes_service.persist_note(db, n, links=[])
        db.commit()
        notes_service.delete_note(db, n)
        db.commit()
        live = (db.query(Note)
                  .filter(Note.owner == "del-owner", Note.deleted_at.is_(None))
                  .all())
        assert all(x.id != "del-list-1" for x in live)
    finally:
        db.close()
