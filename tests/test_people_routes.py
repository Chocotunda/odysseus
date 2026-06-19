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
