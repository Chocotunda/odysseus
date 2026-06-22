"""People routes must honor API-token scopes (people:read / people:write),
mirroring the planner's token gate. Browser sessions are unaffected."""
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from core.database import Base
from core.hub_models import Person
import routes.people_routes as people_routes


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _token_request(owner, scopes):
    return SimpleNamespace(state=SimpleNamespace(
        current_user="api", api_token=True, api_token_scopes=list(scopes), api_token_owner=owner))


def _endpoint(router, path, method):
    full = f"/api/people{path}"
    for route in router.routes:
        if route.path == full and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def _seed_person(SessionFactory, owner, name="Ada"):
    db = SessionFactory()
    try:
        p = Person(id=str(uuid.uuid4()), owner=owner, name=name)
        db.add(p); db.commit()
        return p.id
    finally:
        db.close()


def test_read_scope_token_can_list(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(people_routes, "SessionLocal", SessionFactory)
    pid = _seed_person(SessionFactory, "alice")
    router = people_routes.setup_people_routes()
    list_people = _endpoint(router, "", "GET")
    out = list_people(_token_request("alice", ["people:read"]))
    ids = {p["id"] for p in out["people"]}
    assert pid in ids


def test_write_endpoint_rejects_read_only_token(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(people_routes, "SessionLocal", SessionFactory)
    router = people_routes.setup_people_routes()
    create_person = _endpoint(router, "", "POST")
    body = people_routes.PersonCreate(name="Bob")
    with pytest.raises(HTTPException) as exc:
        create_person(_token_request("alice", ["people:read"]), body=body)
    assert exc.value.status_code == 403


def test_missing_people_scope_rejected(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(people_routes, "SessionLocal", SessionFactory)
    router = people_routes.setup_people_routes()
    list_people = _endpoint(router, "", "GET")
    with pytest.raises(HTTPException) as exc:
        list_people(_token_request("alice", ["todos:read"]))
    assert exc.value.status_code == 403
