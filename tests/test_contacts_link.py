"""Orchestration: link_event_attendees writes attended_by edges, skips self."""
import uuid
from icalendar import Event, vCalAddress, vText
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import Person, Link
import src.links as L
from src.contacts import link_event_attendees


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _att(addr, cn=None):
    a = vCalAddress(addr)
    if cn:
        a.params["CN"] = vText(cn)
    return a


def _event(*attendees):
    ev = Event()
    for a in attendees:
        ev.add("attendee", a)
    return ev


def test_links_non_self_attendees_and_skips_self():
    db = _db()
    ev = _event(_att("mailto:wiggert@firm.nl", "Wiggert"),
                _att("mailto:me@work.com", "Me"))
    n = link_event_attendees(db, "alice", "evt-1", ev,
                             self_addrs={"me@work.com"}, cache={})
    db.commit()
    assert n == 1                                   # self skipped
    links = db.query(Link).filter(Link.rel == L.REL_ATTENDED_BY,
                                  Link.from_id == "evt-1",
                                  Link.deleted_at.is_(None)).all()
    assert len(links) == 1
    pid = links[0].to_id
    p = db.query(Person).filter(Person.id == pid).first()
    assert p.email == "wiggert@firm.nl" and p.source == "calendar"
    assert links[0].from_type == L.NODE_MEETING and links[0].to_type == L.NODE_PERSON


def test_idempotent_on_resync():
    db = _db()
    ev = _event(_att("mailto:a@x.com", "A"))
    link_event_attendees(db, "alice", "evt-2", ev, self_addrs=set(), cache={}); db.commit()
    link_event_attendees(db, "alice", "evt-2", ev, self_addrs=set(), cache={}); db.commit()
    links = db.query(Link).filter(Link.rel == L.REL_ATTENDED_BY,
                                  Link.from_id == "evt-2",
                                  Link.deleted_at.is_(None)).all()
    assert len(links) == 1                          # no duplicate edge
    people = db.query(Person).filter(Person.email == "a@x.com").all()
    assert len(people) == 1                         # no duplicate person


def test_empty_event_no_writes():
    db = _db()
    n = link_event_attendees(db, "alice", "evt-3", Event(), self_addrs=set(), cache={})
    db.commit()
    assert n == 0
    assert db.query(Link).count() == 0
