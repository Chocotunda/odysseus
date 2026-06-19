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


# ---------------------------------------------------------------------------
# Task 2: Area seeding + CRUD routes
# ---------------------------------------------------------------------------
from types import SimpleNamespace
from fastapi import HTTPException
import routes.area_routes as area_routes


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _request(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _ep(router, path, method):
    full = f"/api/areas{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_list_seeds_three_areas_once(monkeypatch):
    monkeypatch.setattr(area_routes, "SessionLocal", _sf())
    router = area_routes.setup_area_routes()
    out = _ep(router, "", "GET")(_request("alice"))
    names = [a["name"] for a in out["areas"]]
    assert names == ["Work", "Personal", "Krishna Movements"]
    # idempotent: a second call does not duplicate
    out2 = _ep(router, "", "GET")(_request("alice"))
    assert len(out2["areas"]) == 3


def test_area_crud_and_owner_isolation(monkeypatch):
    monkeypatch.setattr(area_routes, "SessionLocal", _sf())
    router = area_routes.setup_area_routes()
    a = _ep(router, "", "POST")(_request("alice"), area_routes.AreaCreate(name="Side project", color="#a60717"))
    assert a["name"] == "Side project"
    got = _ep(router, "/{area_id}", "GET")(_request("alice"), a["id"])
    assert got["color"] == "#a60717"
    # bob is isolated (bob's list seeds bob's OWN three; alice's area not visible/fetchable)
    assert a["id"] not in [x["id"] for x in _ep(router, "", "GET")(_request("bob"))["areas"]]
    with pytest.raises(HTTPException) as ei:
        _ep(router, "/{area_id}", "GET")(_request("bob"), a["id"])
    assert ei.value.status_code == 404
