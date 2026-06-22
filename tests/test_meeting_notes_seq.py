"""Regression: promote_action_item must assign a non-NULL, monotonic seq.

These tests would FAIL on the old code (seq was never set → NULL) and PASS
after the fix (item.seq = next_plan_item_seq(db, owner) before commit).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import PlanItem
from src import meeting_notes as MN


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_promote_action_item_seq_not_null():
    """Promoted PlanItem must have seq set (not None) so it appears in ?since= feed."""
    db = _db()
    out = MN.save_meeting_note(
        db, "alice", title="1:1", content="",
        action_items=[{"text": "Write spec", "done": False}],
        person_id=None, event_uid=None, make_tasks=True)
    task = db.query(PlanItem).filter(PlanItem.title == "Write spec").one()
    assert task.seq is not None
    assert task.seq >= 1


def test_promote_action_item_seq_monotonic():
    """Two sequential promotes get strictly increasing seq values."""
    db = _db()
    note_out = MN.save_meeting_note(
        db, "alice", title="1:1", content="",
        action_items=[], person_id=None, event_uid=None, make_tasks=False)
    note_id = note_out["note"]["id"]

    MN.promote_action_item(db, "alice", note_id, "Task one")
    MN.promote_action_item(db, "alice", note_id, "Task two")

    items = db.query(PlanItem).filter(PlanItem.owner == "alice").order_by(PlanItem.seq).all()
    seqs = [i.seq for i in items]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)  # all unique
    assert seqs[0] >= 1 and seqs[1] > seqs[0]


def test_promote_seq_visible_in_changes_feed():
    """A promoted item must appear when queried with seq > 0 (i.e. not filtered out by NULL seq)."""
    db = _db()
    out = MN.save_meeting_note(
        db, "alice", title="1:1", content="",
        action_items=[{"text": "Review PR", "done": False}],
        person_id=None, event_uid=None, make_tasks=True)
    # simulate the ?since= filter: seq > 0 (any prior seq means this row MUST appear)
    visible = db.query(PlanItem).filter(PlanItem.owner == "alice", PlanItem.seq > 0).all()
    assert len(visible) == 1
    assert visible[0].title == "Review PR"
