"""Slice 1 schema/config: Person.source, CalendarCal.link_attendees, feature flag."""
import os
import sqlite3
import importlib
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import Person, next_person_seq


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_person_source_defaults_manual_and_in_dict():
    import routes.people_routes as pr
    db = _db()
    p = Person(id=str(uuid.uuid4()), owner="alice", name="Bob", email="b@x.com")
    p.seq = next_person_seq(db, "alice")
    db.add(p); db.commit()
    assert p.source == "manual"                      # model default
    d = pr._person_to_dict(p)
    assert d["source"] == "manual"                   # exposed in sync dict


def test_calendar_link_attendees_defaults_true():
    from core.database import CalendarCal
    db = _db()
    c = CalendarCal(id="c1", owner="alice", name="Work")
    db.add(c); db.commit()
    assert c.link_attendees is True


def test_flag_default_off():
    import src.constants as constants
    importlib.reload(constants)
    assert constants.CALENDAR_ATTENDEE_LINKING is False


def test_person_source_migration_idempotent(tmp_path, monkeypatch):
    # Simulate an OLD db where `people` has no `source` column.
    dbfile = tmp_path / "old.db"
    conn = sqlite3.connect(dbfile)
    conn.execute("CREATE TABLE people (id TEXT PRIMARY KEY, owner TEXT, name TEXT)")
    conn.execute("INSERT INTO people (id, owner, name) VALUES ('p1','alice','Old')")
    conn.commit(); conn.close()

    import core.database as cdb
    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{dbfile}")
    cdb._migrate_add_person_source()
    cdb._migrate_add_person_source()   # idempotent: second run must not raise

    conn = sqlite3.connect(dbfile)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(people)")]
    val = conn.execute("SELECT source FROM people WHERE id='p1'").fetchone()[0]
    conn.close()
    assert "source" in cols
    assert val == "manual"
