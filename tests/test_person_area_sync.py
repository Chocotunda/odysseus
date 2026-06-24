"""Slice 2 — Person + Area delta contract: seq + tombstone + /changes feed.

Mirrors the Link sync test (test_link_sync.py) applied to Person and Area.
Critical gotcha: ensure_seeded_areas must stamp seq on first-touch so seeded
areas appear in /areas/changes.
"""
import sqlite3
import uuid
from types import SimpleNamespace
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import (
    Person, Area,
    next_person_seq, next_area_seq,
)
import core.hub_models as hub_models
import routes.people_routes as people_routes
import routes.area_routes as area_routes
from src.areas import ensure_seeded_areas


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


# ---------------------------------------------------------------------------
# next_person_seq / next_area_seq: per-owner monotonic
# ---------------------------------------------------------------------------

def test_next_person_seq_is_per_owner_monotonic():
    db = _db()
    assert next_person_seq(db, "alice") == 1
    p = Person(id=str(uuid.uuid4()), owner="alice", name="A")
    p.seq = next_person_seq(db, "alice")
    db.add(p); db.commit()
    assert next_person_seq(db, "alice") == 2
    assert next_person_seq(db, "bob") == 1   # independent per owner


def test_next_area_seq_is_per_owner_monotonic():
    db = _db()
    assert next_area_seq(db, "alice") == 1
    a = Area(id=str(uuid.uuid4()), owner="alice", name="Work")
    a.seq = next_area_seq(db, "alice")
    db.add(a); db.commit()
    assert next_area_seq(db, "alice") == 2
    assert next_area_seq(db, "bob") == 1


# ---------------------------------------------------------------------------
# ensure_seeded_areas stamps seq on each seeded row
# ---------------------------------------------------------------------------

def test_ensure_seeded_areas_stamps_seq(monkeypatch):
    """Seeded areas must have seq > 0 so they appear in /areas/changes?since=0."""
    SF = _sf()
    db = SF()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)

    ensure_seeded_areas(db, "alice")
    areas = db.query(Area).filter(Area.owner == "alice").all()
    assert len(areas) == 3
    for a in areas:
        assert a.seq is not None and a.seq > 0, f"area {a.name!r} has seq={a.seq}"


def test_ensure_seeded_areas_visible_in_changes_feed(monkeypatch):
    """After ensure_seeded_areas, GET /areas/changes?since=0 returns all 3 seeded rows."""
    SF = _sf()
    db = SF()
    ensure_seeded_areas(db, "alice")
    db.close()

    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    changes = _areas_changes_endpoint(router)

    out = changes(_req("alice"), since=0)
    assert len(out["areas"]) == 3
    for row in out["areas"]:
        assert row["seq"] is not None and row["seq"] > 0
        assert row["color"] is not None   # seeded areas have colors


def _areas_changes_endpoint(router):
    for r in router.routes:
        if r.path == "/api/areas/changes" and "GET" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: GET /api/areas/changes")


def _people_changes_endpoint(router):
    for r in router.routes:
        if r.path == "/api/people/changes" and "GET" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: GET /api/people/changes")


# ---------------------------------------------------------------------------
# Person CRUD: create/update/delete stamp seq and appear in /people/changes
# ---------------------------------------------------------------------------

def _list_people_endpoint(router):
    for r in router.routes:
        if r.path == "/api/people" and "GET" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: GET /api/people")


def _create_person_endpoint(router):
    for r in router.routes:
        if r.path == "/api/people" and "POST" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: POST /api/people")


def _update_person_endpoint(router):
    for r in router.routes:
        if r.path == "/api/people/{person_id}" and "PUT" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: PUT /api/people/{person_id}")


def _delete_person_endpoint(router):
    for r in router.routes:
        if r.path == "/api/people/{person_id}" and "DELETE" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: DELETE /api/people/{person_id}")


def _create_area_endpoint(router):
    for r in router.routes:
        if r.path == "/api/areas" and "POST" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: POST /api/areas")


def _update_area_endpoint(router):
    for r in router.routes:
        if r.path == "/api/areas/{area_id}" and "PUT" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: PUT /api/areas/{area_id}")


def _delete_area_endpoint(router):
    for r in router.routes:
        if r.path == "/api/areas/{area_id}" and "DELETE" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: DELETE /api/areas/{area_id}")


def _list_areas_endpoint(router):
    for r in router.routes:
        if r.path == "/api/areas" and "GET" in r.methods:
            return r.endpoint
    raise AssertionError("route not found: GET /api/areas")


def test_create_person_stamps_seq_and_appears_in_changes(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    create = _create_person_endpoint(router)
    changes = _people_changes_endpoint(router)

    body = SimpleNamespace(name="Alice", email=None, role=None, contact_uid=None, area_id=None)
    out = create(_req("alice"), body)
    pid = out["id"]
    assert out["seq"] is not None and out["seq"] > 0

    ch = changes(_req("alice"), since=0)
    ids = {p["id"] for p in ch["people"]}
    assert pid in ids
    found = next(p for p in ch["people"] if p["id"] == pid)
    assert found["deleted"] is False


def test_update_person_advances_seq_and_appears_in_changes(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    create = _create_person_endpoint(router)
    update = _update_person_endpoint(router)
    changes = _people_changes_endpoint(router)

    body = SimpleNamespace(name="Alice", email=None, role=None, contact_uid=None, area_id=None)
    p1 = create(_req("alice"), body)
    pid = p1["id"]
    create_seq = p1["seq"]

    upd_body = SimpleNamespace(name="Alice Updated", email=None, role=None, contact_uid=None, archived=None, area_id=None)
    p2 = update(_req("alice"), pid, upd_body)
    assert p2["seq"] > create_seq

    ch = changes(_req("alice"), since=create_seq)
    ids = {p["id"] for p in ch["people"]}
    assert pid in ids


def test_delete_person_is_soft_and_appears_as_tombstone(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    create = _create_person_endpoint(router)
    delete = _delete_person_endpoint(router)
    changes = _people_changes_endpoint(router)
    list_p = _list_people_endpoint(router)

    body = SimpleNamespace(name="Bob", email=None, role=None, contact_uid=None, area_id=None)
    out = create(_req("alice"), body)
    pid = out["id"]
    create_seq = out["seq"]

    delete(_req("alice"), pid)

    # tombstone in /changes
    ch = changes(_req("alice"), since=create_seq)
    dead = [p for p in ch["people"] if p["id"] == pid]
    assert len(dead) == 1
    assert dead[0]["deleted"] is True
    assert dead[0]["deleted_at"] is not None

    # excluded from live list
    lst = list_p(_req("alice"))
    assert not any(p["id"] == pid for p in lst["people"])


def test_person_changes_since_is_exclusive(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    create = _create_person_endpoint(router)
    changes = _people_changes_endpoint(router)

    b1 = SimpleNamespace(name="P1", email=None, role=None, contact_uid=None, area_id=None)
    b2 = SimpleNamespace(name="P2", email=None, role=None, contact_uid=None, area_id=None)
    p1 = create(_req("alice"), b1)
    p2 = create(_req("alice"), b2)

    ch = changes(_req("alice"), since=p1["seq"])
    ids = {p["id"] for p in ch["people"]}
    assert p2["id"] in ids
    assert p1["id"] not in ids
    assert ch["cursor"] == p2["seq"]


def test_person_changes_cursor_echoes_since_when_empty(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    create = _create_person_endpoint(router)
    changes = _people_changes_endpoint(router)

    body = SimpleNamespace(name="C", email=None, role=None, contact_uid=None, area_id=None)
    p = create(_req("alice"), body)

    ch = changes(_req("alice"), since=p["seq"])
    assert ch["people"] == []
    assert ch["cursor"] == p["seq"]


def test_person_changes_owner_scoped(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    create = _create_person_endpoint(router)
    changes = _people_changes_endpoint(router)

    b1 = SimpleNamespace(name="Alice's person", email=None, role=None, contact_uid=None, area_id=None)
    b2 = SimpleNamespace(name="Bob's person", email=None, role=None, contact_uid=None, area_id=None)
    create(_req("alice"), b1)
    create(_req("bob"), b2)

    ch = changes(_req("alice"), since=0)
    assert all(p["id"] != "bob-person" for p in ch["people"])
    names = {p["name"] for p in ch["people"]}
    assert "Alice's person" in names
    assert "Bob's person" not in names


def test_person_changes_scope_check(monkeypatch):
    from fastapi import HTTPException
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    changes = _people_changes_endpoint(router)

    bad = SimpleNamespace(state=SimpleNamespace(
        api_token=True, api_token_scopes=["todos:read"], api_token_owner="alice"))
    with pytest.raises(HTTPException) as ei:
        changes(bad, since=0)
    assert ei.value.status_code == 403

    good = SimpleNamespace(state=SimpleNamespace(
        api_token=True, api_token_scopes=["people:read"], api_token_owner="alice"))
    out = changes(good, since=0)
    assert "people" in out


def test_soft_deleted_person_excluded_from_list_but_in_changes(monkeypatch):
    """Spec §3: soft-deleted person disappears from list but stays in /changes."""
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    create = _create_person_endpoint(router)
    delete = _delete_person_endpoint(router)
    changes = _people_changes_endpoint(router)
    list_p = _list_people_endpoint(router)

    body = SimpleNamespace(name="Eve", email=None, role=None, contact_uid=None, area_id=None)
    p = create(_req("alice"), body)
    pid = p["id"]

    delete(_req("alice"), pid)

    # excluded from live list
    lst = list_p(_req("alice"))
    assert not any(row["id"] == pid for row in lst["people"])

    # present in /changes as tombstone
    ch = changes(_req("alice"), since=0)
    tombstones = [row for row in ch["people"] if row["id"] == pid]
    assert len(tombstones) == 1
    assert tombstones[0]["deleted"] is True


# ---------------------------------------------------------------------------
# Area CRUD: create/update/delete + /areas/changes
# ---------------------------------------------------------------------------

def test_create_area_stamps_seq_and_appears_in_changes(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    create = _create_area_endpoint(router)
    changes = _areas_changes_endpoint(router)

    body = SimpleNamespace(name="Work", color="#0066ff")
    out = create(_req("alice"), body)
    aid = out["id"]
    assert out["seq"] is not None and out["seq"] > 0

    ch = changes(_req("alice"), since=0)
    ids = {a["id"] for a in ch["areas"]}
    assert aid in ids
    found = next(a for a in ch["areas"] if a["id"] == aid)
    assert found["deleted"] is False


def test_update_area_advances_seq_and_appears_in_changes(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    create = _create_area_endpoint(router)
    update = _update_area_endpoint(router)
    changes = _areas_changes_endpoint(router)

    body = SimpleNamespace(name="Personal", color=None)
    a1 = create(_req("alice"), body)
    aid = a1["id"]
    create_seq = a1["seq"]

    upd = SimpleNamespace(name="Personal Life", color=None, sort_order=None, archived=None)
    a2 = update(_req("alice"), aid, upd)
    assert a2["seq"] > create_seq

    ch = changes(_req("alice"), since=create_seq)
    ids = {a["id"] for a in ch["areas"]}
    assert aid in ids


def test_delete_area_is_soft_and_appears_as_tombstone(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    create = _create_area_endpoint(router)
    delete = _delete_area_endpoint(router)
    changes = _areas_changes_endpoint(router)
    list_a = _list_areas_endpoint(router)

    body = SimpleNamespace(name="TempArea", color=None)
    out = create(_req("alice"), body)
    aid = out["id"]
    create_seq = out["seq"]

    delete(_req("alice"), aid)

    # tombstone in /changes
    ch = changes(_req("alice"), since=create_seq)
    dead = [a for a in ch["areas"] if a["id"] == aid]
    assert len(dead) == 1
    assert dead[0]["deleted"] is True
    assert dead[0]["deleted_at"] is not None

    # excluded from live list
    lst = list_a(_req("alice"))
    assert not any(a["id"] == aid for a in lst["areas"])


def test_area_changes_since_is_exclusive(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    create = _create_area_endpoint(router)
    changes = _areas_changes_endpoint(router)

    b1 = SimpleNamespace(name="A1", color=None)
    b2 = SimpleNamespace(name="A2", color=None)
    a1 = create(_req("alice"), b1)
    a2 = create(_req("alice"), b2)

    ch = changes(_req("alice"), since=a1["seq"])
    ids = {a["id"] for a in ch["areas"]}
    assert a2["id"] in ids
    assert a1["id"] not in ids
    assert ch["cursor"] == a2["seq"]


def test_area_changes_cursor_echoes_since_when_empty(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    create = _create_area_endpoint(router)
    changes = _areas_changes_endpoint(router)

    body = SimpleNamespace(name="Single", color=None)
    a = create(_req("alice"), body)

    ch = changes(_req("alice"), since=a["seq"])
    assert ch["areas"] == []
    assert ch["cursor"] == a["seq"]


def test_area_changes_owner_scoped(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    create = _create_area_endpoint(router)
    changes = _areas_changes_endpoint(router)

    create(_req("alice"), SimpleNamespace(name="Alice's area", color=None))
    create(_req("bob"), SimpleNamespace(name="Bob's area", color=None))

    ch = changes(_req("alice"), since=0)
    names = {a["name"] for a in ch["areas"]}
    assert "Alice's area" in names
    assert "Bob's area" not in names


def test_area_changes_scope_check(monkeypatch):
    from fastapi import HTTPException
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    changes = _areas_changes_endpoint(router)

    bad = SimpleNamespace(state=SimpleNamespace(
        api_token=True, api_token_scopes=["todos:read"], api_token_owner="alice"))
    with pytest.raises(HTTPException) as ei:
        changes(bad, since=0)
    assert ei.value.status_code == 403

    good = SimpleNamespace(state=SimpleNamespace(
        api_token=True, api_token_scopes=["areas:read"], api_token_owner="alice"))
    out = changes(good, since=0)
    assert "areas" in out


def test_soft_deleted_area_excluded_from_list_but_in_changes(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    create = _create_area_endpoint(router)
    delete = _delete_area_endpoint(router)
    changes = _areas_changes_endpoint(router)
    list_a = _list_areas_endpoint(router)

    body = SimpleNamespace(name="Temp", color=None)
    a = create(_req("alice"), body)
    aid = a["id"]

    delete(_req("alice"), aid)

    lst = list_a(_req("alice"))
    assert not any(row["id"] == aid for row in lst["areas"])

    ch = changes(_req("alice"), since=0)
    tombstones = [row for row in ch["areas"] if row["id"] == aid]
    assert len(tombstones) == 1
    assert tombstones[0]["deleted"] is True


# ---------------------------------------------------------------------------
# _person_to_dict / _area_to_dict expose seq + deleted + deleted_at
# ---------------------------------------------------------------------------

def test_person_to_dict_includes_sync_fields():
    db = _db()
    p = Person(id="p1", owner="alice", name="T", seq=5)
    p.deleted_at = None
    db.add(p); db.commit()
    d = people_routes._person_to_dict(p)
    assert "seq" in d
    assert "deleted" in d
    assert "deleted_at" in d
    assert d["seq"] == 5
    assert d["deleted"] is False


def test_area_to_dict_includes_sync_fields():
    db = _db()
    a = Area(id="a1", owner="alice", name="Work", seq=3)
    a.deleted_at = None
    db.add(a); db.commit()
    d = area_routes._area_to_dict(a)
    assert "seq" in d
    assert "deleted" in d
    assert "deleted_at" in d
    assert d["seq"] == 3
    assert d["deleted"] is False


# ---------------------------------------------------------------------------
# Migration helpers: seq + deleted_at on people / areas tables
# ---------------------------------------------------------------------------

def test_person_seq_migration_adds_and_backfills(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE people (id TEXT PRIMARY KEY, owner TEXT, updated_at TEXT)")
    conn.executemany(
        "INSERT INTO people (id, owner, updated_at) VALUES (?, ?, ?)",
        [("a", "alice", "2026-06-20"), ("b", "alice", "2026-06-21"), ("c", "bob", "2026-06-20")],
    )
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_person_seq_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(people)").fetchall()]
    seqs = dict(conn.execute("SELECT id, seq FROM people").fetchall())
    conn.close()
    assert "seq" in cols
    assert seqs == {"a": 1, "b": 2, "c": 1}


def test_person_deleted_at_migration_adds_column(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE people (id TEXT PRIMARY KEY, owner TEXT)")
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_person_deleted_at_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(people)").fetchall()]
    conn.close()
    assert "deleted_at" in cols


def test_area_seq_migration_adds_and_backfills(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE areas (id TEXT PRIMARY KEY, owner TEXT, updated_at TEXT)")
    conn.executemany(
        "INSERT INTO areas (id, owner, updated_at) VALUES (?, ?, ?)",
        [("x", "alice", "2026-06-20"), ("y", "alice", "2026-06-22"), ("z", "bob", "2026-06-21")],
    )
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_area_seq_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(areas)").fetchall()]
    seqs = dict(conn.execute("SELECT id, seq FROM areas").fetchall())
    conn.close()
    assert "seq" in cols
    assert seqs == {"x": 1, "y": 2, "z": 1}


def test_area_deleted_at_migration_adds_column(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE areas (id TEXT PRIMARY KEY, owner TEXT)")
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_area_deleted_at_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(areas)").fetchall()]
    conn.close()
    assert "deleted_at" in cols
