"""Meeting-note save wires the graph edges and promotes action items into linked
tasks (with the soft-field cache). Promotion is idempotent."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, PlanItem, Note
from src import links as L
from src import meeting_notes as MN


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_save_writes_note_meeting_and_person_edges():
    db = _db()
    out = MN.save_meeting_note(
        db, "alice", title="1:1 Wiggert", content="Discussed Q3.",
        action_items=[{"text": "Send deck", "done": False}],
        person_id="p1", event_uid="m1", make_tasks=False)
    note_id = out["note"]["id"]
    # Note -> Meeting and Note -> Person edges exist
    assert {(e.rel, e.to_id) for e in L.links_from(db, "alice", L.NODE_NOTE, note_id)} == {
        (L.REL_NOTE_OF, "m1"), (L.REL_ABOUT, "p1")}
    # Meeting -> Person (so the person page shows the meeting)
    assert {e.to_id for e in L.links_from(db, "alice", L.NODE_MEETING, "m1", rel=L.REL_ATTENDED_BY)} == {"p1"}
    assert out["tasks"] == []  # make_tasks=False


def test_make_tasks_promotes_unchecked_items_with_links_and_softfields():
    db = _db()
    out = MN.save_meeting_note(
        db, "alice", title="1:1", content="",
        action_items=[{"text": "Send deck", "done": False},
                      {"text": "already done", "done": True}],
        person_id="p1", event_uid="m1", make_tasks=True)
    assert len(out["tasks"]) == 1                      # only the unchecked one
    task = db.query(PlanItem).filter(PlanItem.title == "Send deck").one()
    assert task.owner == "alice"
    assert task.source_note_id == out["note"]["id"]    # soft-field cache
    assert task.source_event_id == "m1"
    assert task.person_id == "p1"
    # graph edges Task -> Note and Task -> Person
    assert {(e.rel, e.to_id) for e in L.links_from(db, "alice", L.NODE_TASK, task.id)} == {
        (L.REL_FROM_NOTE, out["note"]["id"]), (L.REL_ABOUT, "p1")}


def test_promotion_is_idempotent():
    db = _db()
    out = MN.save_meeting_note(db, "alice", title="1:1", content="",
                               action_items=[{"text": "Send deck", "done": False}],
                               person_id="p1", event_uid="m1", make_tasks=True)
    note_id = out["note"]["id"]
    # promote the same item again -> no duplicate task
    MN.promote_action_item(db, "alice", note_id, "Send deck", person_id="p1")
    assert db.query(PlanItem).filter(PlanItem.title == "Send deck").count() == 1


def test_save_with_area_stamps_note_and_tasks():
    db = _db()
    out = MN.save_meeting_note(
        db, "alice", title="1:1", content="",
        action_items=[{"text": "Send deck", "done": False}],
        person_id="p1", event_uid="m1", make_tasks=True, area_id="areaWork")
    note_id = out["note"]["id"]
    assert [e.to_id for e in L.links_from(db, "alice", L.NODE_NOTE, note_id, rel=L.REL_IN_AREA)] == ["areaWork"]
    task = db.query(PlanItem).filter(PlanItem.title == "Send deck").one()
    assert [e.to_id for e in L.links_from(db, "alice", L.NODE_TASK, task.id, rel=L.REL_IN_AREA)] == ["areaWork"]


async def test_enrich_writes_suggested_items(monkeypatch):
    db = _db()
    out = MN.save_meeting_note(db, "alice", title="1:1", content="Wiggert to send the deck by Friday.",
                               action_items=[], person_id="p1", event_uid=None, make_tasks=False)
    note_id = out["note"]["id"]

    import src.planner_ai as pai
    async def fake_extract(content, person_name, owner=None):
        return [{"title": "Send the deck", "owner": person_name, "due_hint": "2026-06-26"}]
    monkeypatch.setattr(pai, "extract_action_items", fake_extract)
    # use the SAME engine/session for the background fn
    import src.meeting_notes as MNmod
    monkeypatch.setattr(MNmod, "SessionLocal", lambda: db, raising=False)

    await MN.enrich_meeting_note(note_id, "alice")
    refreshed = MN._note_dict(db.query(Note).filter(Note.id == note_id).one())
    assert refreshed["ai_enriched"] is True
    assert [s["title"] for s in refreshed["suggested_action_items"]] == ["Send the deck"]
