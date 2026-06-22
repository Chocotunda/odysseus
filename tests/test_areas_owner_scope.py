"""Areas routes must honor API-token scopes (areas:read / areas:write),
mirroring the planner's token gate. Browser sessions are unaffected."""
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from core.database import Base
from core.hub_models import Area
import routes.area_routes as area_routes


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _token_request(owner, scopes):
    return SimpleNamespace(state=SimpleNamespace(
        current_user="api", api_token=True, api_token_scopes=list(scopes), api_token_owner=owner))


def _endpoint(router, path, method):
    full = f"/api/areas{path}"
    for route in router.routes:
        if route.path == full and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def _seed_area(SessionFactory, owner, name="Work"):
    db = SessionFactory()
    try:
        a = Area(id=str(uuid.uuid4()), owner=owner, name=name)
        db.add(a); db.commit()
        return a.id
    finally:
        db.close()


def test_read_scope_token_can_list(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(area_routes, "SessionLocal", SessionFactory)
    aid = _seed_area(SessionFactory, "alice")
    router = area_routes.setup_area_routes()
    list_areas = _endpoint(router, "", "GET")
    out = list_areas(_token_request("alice", ["areas:read"]))
    ids = {a["id"] for a in out["areas"]}
    assert aid in ids


def test_write_endpoint_rejects_read_only_token(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(area_routes, "SessionLocal", SessionFactory)
    router = area_routes.setup_area_routes()
    create_area = _endpoint(router, "", "POST")
    body = area_routes.AreaCreate(name="Work")
    with pytest.raises(HTTPException) as exc:
        create_area(_token_request("alice", ["areas:read"]), body=body)
    assert exc.value.status_code == 403


def test_missing_areas_scope_rejected(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(area_routes, "SessionLocal", SessionFactory)
    router = area_routes.setup_area_routes()
    list_areas = _endpoint(router, "", "GET")
    with pytest.raises(HTTPException) as exc:
        list_areas(_token_request("alice", ["todos:read"]))
    assert exc.value.status_code == 403
