# tests/test_people_routes.py
"""Person is a first-class owner-scoped node: CRUD + strict owner isolation."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace
from fastapi import HTTPException

from core.database import Base
import routes.people_routes as people_routes


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _request(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _ep(router, path, method):
    full = f"/api/people{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_create_and_get_person(monkeypatch):
    monkeypatch.setattr(people_routes, "SessionLocal", _sf())
    router = people_routes.setup_people_routes()
    create = _ep(router, "", "POST")
    out = create(_request("alice"), people_routes.PersonCreate(name="Wiggert", role="Direct report"))
    assert out["name"] == "Wiggert"
    got = _ep(router, "/{person_id}", "GET")(_request("alice"), out["id"])
    assert got["role"] == "Direct report"


def test_people_are_owner_isolated(monkeypatch):
    monkeypatch.setattr(people_routes, "SessionLocal", _sf())
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="Wiggert"))
    # bob cannot see or fetch alice's person
    assert _ep(router, "", "GET")(_request("bob"))["people"] == []
    with pytest.raises(HTTPException) as ei:
        _ep(router, "/{person_id}", "GET")(_request("bob"), p["id"])
    assert ei.value.status_code == 404


def test_update_person(monkeypatch):
    monkeypatch.setattr(people_routes, "SessionLocal", _sf())
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="Wig"))
    upd = _ep(router, "/{person_id}", "PUT")(_request("alice"), p["id"],
                                             people_routes.PersonUpdate(email="w@x.io"))
    assert upd["email"] == "w@x.io"


def test_person_page_aggregates_open_tasks_meetings_notes(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    import src.meeting_notes as MN
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="Wiggert"))

    db = SF()
    # seed a calendar event the note will attach to
    from core.database import CalendarCal, CalendarEvent, utcnow_naive
    db.add(CalendarCal(id="c1", owner="alice", name="Personal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Weekly 1:1",
                         dtstart=utcnow_naive(), dtend=utcnow_naive()))
    db.commit()
    MN.save_meeting_note(db, "alice", title="1:1", content="notes",
                         action_items=[{"text": "Send deck", "done": False}],
                         person_id=p["id"], event_uid="m1", make_tasks=True)
    db.close()

    page = _ep(router, "/{person_id}/page", "GET")(_request("alice"), p["id"])
    assert page["person"]["id"] == p["id"]
    assert [t["title"] for t in page["open_tasks"]] == ["Send deck"]
    assert [m["uid"] for m in page["meetings"]] == ["m1"]
    assert len(page["notes"]) == 1


def test_person_page_open_tasks_include_note_title_and_overdue(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    import src.meeting_notes as MN
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="Wiggert"))

    db = SF()
    from core.database import CalendarCal, CalendarEvent, utcnow_naive
    db.add(CalendarCal(id="c2", owner="alice", name="Work"))
    db.add(CalendarEvent(uid="m2", calendar_id="c2", summary="Catchup",
                         dtstart=utcnow_naive(), dtend=utcnow_naive()))
    db.commit()
    # Task 1: clearly overdue (2020-01-01), promoted from a meeting note titled "Old 1:1"
    MN.save_meeting_note(db, "alice", title="Old 1:1", content="old notes",
                         action_items=[{"text": "Send overdue deck", "done": False}],
                         person_id=p["id"], event_uid="m2", make_tasks=True)
    # Task 2: due today (not overdue), note titled "Recent 1:1"
    MN.save_meeting_note(db, "alice", title="Recent 1:1", content="recent notes",
                         action_items=[{"text": "Send current deck", "done": False}],
                         person_id=p["id"], event_uid="m2", make_tasks=True)
    db.close()

    # Back-date the first task to a clearly past date
    from datetime import datetime
    db2 = SF()
    from core.database import PlanItem
    overdue_task = db2.query(PlanItem).filter(PlanItem.title == "Send overdue deck").first()
    assert overdue_task is not None
    overdue_task.due_date = "2020-01-01"
    today_str = datetime.now().strftime("%Y-%m-%d")
    current_task = db2.query(PlanItem).filter(PlanItem.title == "Send current deck").first()
    assert current_task is not None
    current_task.due_date = today_str
    db2.commit()
    db2.close()

    page = _ep(router, "/{person_id}/page", "GET")(_request("alice"), p["id"])
    tasks_by_title = {t["title"]: t for t in page["open_tasks"]}

    # overdue task: note_title matches source note, overdue=True
    overdue = tasks_by_title["Send overdue deck"]
    assert overdue["note_title"] == "Old 1:1", f"expected 'Old 1:1', got {overdue['note_title']!r}"
    assert overdue["overdue"] is True, "task due 2020-01-01 must be overdue"

    # current task: note_title matches source note, overdue=False (due today)
    current = tasks_by_title["Send current deck"]
    assert current["note_title"] == "Recent 1:1", f"expected 'Recent 1:1', got {current['note_title']!r}"
    assert current["overdue"] is False, "task due today must not be overdue"


def test_person_page_excludes_other_persons_and_owners(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    import src.meeting_notes as MN
    router = people_routes.setup_people_routes()
    p1 = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="W"))
    p2 = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="X"))
    db = SF()
    MN.save_meeting_note(db, "alice", title="n", content="", person_id=p2["id"],
                         action_items=[{"text": "X task", "done": False}],
                         event_uid=None, make_tasks=True)
    db.close()
    page = _ep(router, "/{person_id}/page", "GET")(_request("alice"), p1["id"])
    assert page["open_tasks"] == []   # p1 has none; p2's task must not leak


def test_person_create_with_area_and_reassign(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"),
                                people_routes.PersonCreate(name="Wiggert", area_id="areaWork"))
    assert p["area_id"] == "areaWork"
    # get echoes the area
    assert _ep(router, "/{person_id}", "GET")(_request("alice"), p["id"])["area_id"] == "areaWork"
    # reassign via update -> exactly one area, the new one
    upd = _ep(router, "/{person_id}", "PUT")(_request("alice"), p["id"],
                                            people_routes.PersonUpdate(area_id="areaPersonal"))
    assert upd["area_id"] == "areaPersonal"
    import src.links as L
    db = SF()
    edges = L.links_from(db, "alice", L.NODE_PERSON, p["id"], rel=L.REL_IN_AREA)
    assert [e.to_id for e in edges] == ["areaPersonal"]
    db.close()
