# tests/test_links.py
"""The Link join table IS the graph: idempotent edges, reverse-query backlinks,
and strict owner isolation (a new owner-scoped security boundary)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import Link
from src import links as L


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_add_link_is_idempotent():
    db = _db()
    a = L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    b = L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert a.id == b.id
    assert db.query(Link).count() == 1


def test_links_from_and_to_are_reverse_queries():
    db = _db()
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    L.add_link(db, "alice", L.NODE_TASK, "t2", L.REL_ABOUT, L.NODE_PERSON, "p1")
    # forward: edges out of t1
    assert {e.to_id for e in L.links_from(db, "alice", L.NODE_TASK, "t1")} == {"p1"}
    # reverse: everything pointing AT person p1
    assert {e.from_id for e in L.links_to(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_ABOUT)} == {"t1", "t2"}


def test_links_are_owner_isolated():
    db = _db()
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert L.links_to(db, "bob", L.NODE_PERSON, "p1") == []


def test_remove_links_for_clears_both_directions():
    db = _db()
    L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_NOTE_OF, L.NODE_MEETING, "m1")
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_FROM_NOTE, L.NODE_NOTE, "n1")
    removed = L.remove_links_for(db, "alice", L.NODE_NOTE, "n1")
    assert removed == 2
    # Edges are SOFT-deleted (tombstoned), not removed, so they sync to Tide as
    # deletes — the rows stay but reverse-reads no longer return them.
    assert db.query(Link).count() == 2
    assert all(r.deleted_at is not None for r in db.query(Link).all())
    assert L.links_from(db, "alice", L.NODE_NOTE, "n1") == []
    assert L.links_to(db, "alice", L.NODE_NOTE, "n1") == []
