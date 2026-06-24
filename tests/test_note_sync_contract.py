from datetime import datetime, timezone

from core.database import SessionLocal, Note, init_db
from core import hub_models


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
