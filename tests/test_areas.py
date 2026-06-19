"""Area node + single-primary membership via the in_area Link edge."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, Area, Link
from src import links as L


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_set_area_creates_single_edge():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaWork")
    edges = L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA)
    assert [(e.to_type, e.to_id) for e in edges] == [(L.NODE_AREA, "areaWork")]


def test_set_area_replaces_previous():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaWork")
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaPersonal")
    edges = L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA)
    assert [e.to_id for e in edges] == ["areaPersonal"]   # exactly one, the new one


def test_set_area_none_clears():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaWork")
    assert L.set_area(db, "alice", L.NODE_PERSON, "p1", None) is None
    assert L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA) == []


def test_clear_area_removes_edge():
    db = _db()
    L.set_area(db, "alice", L.NODE_TASK, "t1", "areaWork")
    assert L.clear_area(db, "alice", L.NODE_TASK, "t1") == 1
    assert L.links_from(db, "alice", L.NODE_TASK, "t1", rel=L.REL_IN_AREA) == []


def test_area_membership_is_owner_isolated():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaWork")
    assert L.links_to(db, "bob", L.NODE_AREA, "areaWork", rel=L.REL_IN_AREA) == []
