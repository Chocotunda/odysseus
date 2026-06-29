"""GET /meeting/{uid} returns the attendee roster from attended_by edges."""
import uuid
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, CalendarCal, CalendarEvent
from core.hub_models import Person, next_person_seq
import src.links as L
import routes.meeting_notes_routes as mnr
from datetime import datetime


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    cal = CalendarCal(id="cal1", owner="alice", name="Work")
    db.add(cal)
    ev = CalendarEvent(uid="evt-1", calendar_id="cal1", summary="Team Tea Time",
                       dtstart=datetime(2026, 7, 24, 13, 0), dtend=datetime(2026, 7, 24, 13, 30),
                       all_day=False, is_utc=True)
    db.add(ev)
    p = Person(id=str(uuid.uuid4()), owner="alice", name="Wiggert", email="w@firm.nl", source="calendar")
    p.seq = next_person_seq(db, "alice"); db.add(p); db.commit()
    L.add_link(db, "alice", L.NODE_MEETING, "evt-1", L.REL_ATTENDED_BY, L.NODE_PERSON, p.id)
    db.commit()
    return p


def _token_req(owner, scopes):
    return SimpleNamespace(state=SimpleNamespace(current_user="api", api_token=True,
                                                 api_token_scopes=list(scopes), api_token_owner=owner))


def _endpoint(mod):
    router = mod.setup_meeting_notes_routes()
    for r in router.routes:
        if r.path == "/api/meeting-notes/meeting/{uid}" and "GET" in r.methods:
            return r.endpoint
    raise AssertionError("route not found")


def test_detail_includes_attendees(monkeypatch):
    SF = _sf(); db = SF(); p = _seed(db)
    monkeypatch.setattr(mnr, "SessionLocal", SF)
    get_detail = _endpoint(mnr)
    result = get_detail(_token_req("alice", ["notes:read"]), uid="evt-1")
    assert [a["email"] for a in result["attendees"]] == ["w@firm.nl"]
    assert result["attendees"][0]["source"] == "calendar"
    assert result["attendees"][0]["name"] == "Wiggert"


def test_detail_empty_attendees(monkeypatch):
    SF = _sf(); db = SF()
    cal = CalendarCal(id="cal1", owner="alice", name="Work"); db.add(cal)
    db.add(CalendarEvent(uid="evt-2", calendar_id="cal1", summary="Solo",
                         dtstart=datetime(2026, 7, 1, 9, 0), dtend=datetime(2026, 7, 1, 9, 30),
                         all_day=False, is_utc=True)); db.commit()
    monkeypatch.setattr(mnr, "SessionLocal", SF)
    get_detail = _endpoint(mnr)
    result = get_detail(_token_req("alice", ["notes:read"]), uid="evt-2")
    assert result["attendees"] == []
