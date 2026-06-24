"""Slice 1 — Link sync spine: soft-delete + revive + /links/changes delta feed.

The Link table is the graph spine; making it tombstone-capable lets edges sync
to Tide exactly like PlanItem. The critical trap is the uq_links_edge revive:
a tombstoned edge KEEPS its row, so re-adding the same tuple must REVIVE the
row (never re-INSERT, which would violate the unique constraint).
"""
import sqlite3
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import Link, next_link_seq
import core.hub_models as hub_models
from src import links as L
import routes.link_routes as link_routes


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# --------------------------------------------------------------------------
# add_link: idempotency + REVIVE (the critical correctness point)
# --------------------------------------------------------------------------

def test_add_link_is_idempotent_for_live_edge():
    db = _db()
    a = L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    b = L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert a.id == b.id
    assert db.query(Link).count() == 1
    assert a.deleted_at is None
    assert a.seq == b.seq            # no change on the second (idempotent) call


def test_add_link_stamps_seq_on_create():
    db = _db()
    a = L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    b = L.add_link(db, "alice", L.NODE_TASK, "t2", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert a.seq is not None and b.seq is not None
    assert b.seq > a.seq            # per-owner monotonic


def test_add_link_revives_tombstoned_edge_no_integrity_error():
    """The trap: tombstone an edge, then add_link the same tuple.

    Must REVIVE the existing row (same id, deleted_at cleared, seq advanced) and
    NEVER INSERT a second row (which would hit uq_links_edge).
    """
    db = _db()
    a = L.add_link(db, "alice", L.NODE_PERSON, "p1", L.REL_IN_AREA, L.NODE_AREA, "area1")
    original_id = a.id
    create_seq = a.seq

    # tombstone it via clear_area (row-enumerated soft-delete)
    L.clear_area(db, "alice", L.NODE_PERSON, "p1")
    tomb = db.query(Link).filter(Link.id == original_id).one()
    assert tomb.deleted_at is not None
    tomb_seq = tomb.seq
    assert tomb_seq > create_seq

    # re-add the SAME tuple -> revive (this is what would crash if it INSERTed)
    revived = L.add_link(db, "alice", L.NODE_PERSON, "p1", L.REL_IN_AREA, L.NODE_AREA, "area1")
    assert revived.id == original_id          # same physical row
    assert revived.deleted_at is None         # tombstone cleared
    assert revived.seq > tomb_seq             # seq advanced again
    assert db.query(Link).count() == 1        # still exactly ONE physical row


def test_add_link_find_existing_ignores_deleted_at():
    """A tombstoned row must be FOUND by add_link (so it revives, not duplicates)."""
    db = _db()
    a = L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    L.remove_links_for(db, "alice", L.NODE_NOTE, "n1")
    assert db.query(Link).filter(Link.id == a.id).one().deleted_at is not None
    again = L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert again.id == a.id
    assert db.query(Link).count() == 1


# --------------------------------------------------------------------------
# set_area: reassign tombstones old + revive on re-assign-back
# --------------------------------------------------------------------------

def test_set_area_reassign_tombstones_old_and_lives_new():
    db = _db()
    e1 = L.set_area(db, "alice", L.NODE_PERSON, "p1", "area1")
    assert e1.to_id == "area1" and e1.deleted_at is None
    e1_id, e1_create_seq = e1.id, e1.seq

    e2 = L.set_area(db, "alice", L.NODE_PERSON, "p1", "area2")
    assert e2.to_id == "area2" and e2.deleted_at is None

    # the area1 edge is tombstoned, seq advanced beyond its create seq
    old = db.query(Link).filter(Link.id == e1_id).one()
    assert old.deleted_at is not None
    assert old.seq > e1_create_seq

    # reverse-read sees only the live area2 edge
    live = L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA)
    assert {e.to_id for e in live} == {"area2"}


def test_set_area_reassign_back_revives_original_row():
    """A->area1, A->area2, A->area1 again: the original area1 row REVIVES
    (no uq_links_edge violation)."""
    db = _db()
    e1 = L.set_area(db, "alice", L.NODE_PERSON, "p1", "area1")
    e1_id = e1.id
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "area2")
    back = L.set_area(db, "alice", L.NODE_PERSON, "p1", "area1")
    assert back.id == e1_id                   # revived the ORIGINAL row
    assert back.deleted_at is None
    # exactly two physical edge rows ever (area1 + area2), area2 now tombstoned
    assert db.query(Link).count() == 2
    live = L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA)
    assert {e.to_id for e in live} == {"area1"}


def test_set_area_falsy_clears_and_returns_none():
    db = _db()
    e1 = L.set_area(db, "alice", L.NODE_PERSON, "p1", "area1")
    out = L.set_area(db, "alice", L.NODE_PERSON, "p1", None)
    assert out is None
    assert db.query(Link).filter(Link.id == e1.id).one().deleted_at is not None
    assert L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA) == []


# --------------------------------------------------------------------------
# remove_links_for: soft-delete both ends, each with fresh seq
# --------------------------------------------------------------------------

def test_remove_links_for_soft_deletes_both_ends_with_fresh_seq():
    db = _db()
    L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_NOTE_OF, L.NODE_MEETING, "m1")   # from-end
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_FROM_NOTE, L.NODE_NOTE, "n1")    # to-end
    removed = L.remove_links_for(db, "alice", L.NODE_NOTE, "n1")
    assert removed == 2

    rows = db.query(Link).all()
    assert len(rows) == 2                       # rows KEPT (soft delete), not gone
    assert all(r.deleted_at is not None for r in rows)
    seqs = [r.seq for r in rows]
    assert all(s is not None for s in seqs)
    assert len(set(seqs)) == 2                  # each got its OWN fresh seq

    # reverse-reads no longer return them
    assert L.links_from(db, "alice", L.NODE_NOTE, "n1") == []
    assert L.links_to(db, "alice", L.NODE_NOTE, "n1") == []


def test_remove_links_for_skips_already_tombstoned():
    db = _db()
    L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_NOTE_OF, L.NODE_MEETING, "m1")
    assert L.remove_links_for(db, "alice", L.NODE_NOTE, "n1") == 1
    # second call finds nothing live to delete
    assert L.remove_links_for(db, "alice", L.NODE_NOTE, "n1") == 0


# --------------------------------------------------------------------------
# reads exclude tombstones; owner isolation preserved
# --------------------------------------------------------------------------

def test_links_from_and_to_exclude_tombstones():
    db = _db()
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    L.add_link(db, "alice", L.NODE_TASK, "t2", L.REL_ABOUT, L.NODE_PERSON, "p1")
    L.remove_links_for(db, "alice", L.NODE_TASK, "t1")
    assert {e.from_id for e in L.links_to(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_ABOUT)} == {"t2"}
    assert L.links_from(db, "alice", L.NODE_TASK, "t1") == []


def test_links_owner_isolation_preserved():
    db = _db()
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert L.links_to(db, "bob", L.NODE_PERSON, "p1") == []


def test_clear_area_returns_count_and_tombstones():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "area1")
    n = L.clear_area(db, "alice", L.NODE_PERSON, "p1")
    assert n == 1
    assert L.clear_area(db, "alice", L.NODE_PERSON, "p1") == 0   # nothing live left


# --------------------------------------------------------------------------
# next_link_seq
# --------------------------------------------------------------------------

def test_next_link_seq_is_per_owner_monotonic():
    db = _db()
    assert next_link_seq(db, "alice") == 1
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert next_link_seq(db, "alice") == 2
    assert next_link_seq(db, "bob") == 1     # independent per owner


# --------------------------------------------------------------------------
# /links/changes endpoint (mirror /items/changes)
# --------------------------------------------------------------------------

def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _changes_endpoint(router):
    for r in router.routes:
        if r.path == "/api/links/changes" and "GET" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: GET /api/links/changes")


def test_links_changes_since0_returns_all_live_and_tombstoned(monkeypatch):
    SF = _sf()
    db = SF()
    a = L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    b = L.add_link(db, "alice", L.NODE_TASK, "t2", L.REL_ABOUT, L.NODE_PERSON, "p1")
    a_id, b_id = a.id, b.id
    L.remove_links_for(db, "alice", L.NODE_TASK, "t1")   # tombstone a
    db.close()

    monkeypatch.setattr(link_routes, "SessionLocal", SF)
    router = link_routes.setup_link_routes()
    changes = _changes_endpoint(router)

    out = changes(_req("alice"), since=0)
    by_id = {l["id"]: l for l in out["links"]}
    assert b_id in by_id and by_id[b_id]["deleted"] is False
    assert a_id in by_id and by_id[a_id]["deleted"] is True     # tombstone included
    # monotonic seq, ascending
    seqs = [l["seq"] for l in out["links"]]
    assert seqs == sorted(seqs)
    assert out["cursor"] == max(seqs)


def test_links_changes_since_is_exclusive(monkeypatch):
    SF = _sf()
    db = SF()
    a = L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")  # seq 1
    b = L.add_link(db, "alice", L.NODE_TASK, "t2", L.REL_ABOUT, L.NODE_PERSON, "p1")  # seq 2
    a_seq, b_id, b_seq = a.seq, b.id, b.seq
    db.close()

    monkeypatch.setattr(link_routes, "SessionLocal", SF)
    router = link_routes.setup_link_routes()
    changes = _changes_endpoint(router)

    out = changes(_req("alice"), since=a_seq)   # seq > 1
    ids = {l["id"] for l in out["links"]}
    assert b_id in ids
    assert len(out["links"]) == 1               # a (seq 1) excluded
    assert out["cursor"] == b_seq               # max seq returned


def test_links_changes_empty_batch_echoes_since(monkeypatch):
    SF = _sf()
    db = SF()
    a = L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    a_seq = a.seq
    db.close()

    monkeypatch.setattr(link_routes, "SessionLocal", SF)
    router = link_routes.setup_link_routes()
    changes = _changes_endpoint(router)

    out = changes(_req("alice"), since=a_seq)
    assert out["links"] == []
    assert out["cursor"] == a_seq


def test_links_changes_owner_scoped(monkeypatch):
    SF = _sf()
    db = SF()
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    L.add_link(db, "bob", L.NODE_TASK, "t9", L.REL_ABOUT, L.NODE_PERSON, "p9")
    db.close()

    monkeypatch.setattr(link_routes, "SessionLocal", SF)
    router = link_routes.setup_link_routes()
    changes = _changes_endpoint(router)

    out = changes(_req("alice"), since=0)
    assert all(l["from_id"] == "t1" for l in out["links"])


def test_links_changes_token_requires_links_read_scope(monkeypatch):
    from fastapi import HTTPException
    SF = _sf()
    monkeypatch.setattr(link_routes, "SessionLocal", SF)
    router = link_routes.setup_link_routes()
    changes = _changes_endpoint(router)

    # token caller WITHOUT links:read -> 403
    bad = SimpleNamespace(state=SimpleNamespace(
        api_token=True, api_token_scopes=["todos:read"], api_token_owner="alice"))
    with pytest.raises(HTTPException) as ei:
        changes(bad, since=0)
    assert ei.value.status_code == 403

    # token caller WITH links:read -> ok
    good = SimpleNamespace(state=SimpleNamespace(
        api_token=True, api_token_scopes=["links:read"], api_token_owner="alice"))
    out = changes(good, since=0)
    assert out["links"] == []


# --------------------------------------------------------------------------
# all-writers-set-seq: meeting-note save edges appear in /links/changes
# --------------------------------------------------------------------------

def test_meeting_note_edges_have_seq_for_changes_feed():
    from src import meeting_notes as MN
    db = _db()
    MN.save_meeting_note(
        db, "alice", title="1:1", content="",
        action_items=[{"text": "Write spec", "done": False}],
        person_id="p1", event_uid="m1", make_tasks=True)
    edges = db.query(Link).filter(Link.owner == "alice").all()
    assert len(edges) > 0
    assert all(e.seq is not None for e in edges)   # every writer stamps seq


# --------------------------------------------------------------------------
# migration: seq backfill + deleted_at column on legacy links table
# --------------------------------------------------------------------------

def test_link_seq_migration_adds_and_backfills(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE links (id TEXT PRIMARY KEY, owner TEXT, updated_at TEXT)")
    conn.executemany(
        "INSERT INTO links (id, owner, updated_at) VALUES (?, ?, ?)",
        [("a", "alice", "2026-06-20"), ("b", "alice", "2026-06-21"), ("c", "bob", "2026-06-20")],
    )
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_link_seq_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(links)").fetchall()]
    seqs = dict(conn.execute("SELECT id, seq FROM links").fetchall())
    conn.close()
    assert "seq" in cols
    assert seqs == {"a": 1, "b": 2, "c": 1}


def test_link_deleted_at_migration_adds_column(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE links (id TEXT PRIMARY KEY, owner TEXT)")
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_link_deleted_at_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(links)").fetchall()]
    conn.close()
    assert "deleted_at" in cols
