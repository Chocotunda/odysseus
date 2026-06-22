import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from core.database import Base
from core.hub_models import PlanItem
import routes.planner_routes as planner_routes


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _endpoint(router, path, method):
    full = f"/api/planner{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_create_honors_client_id(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    cid = str(uuid.uuid4())
    out = create(_req("alice"), body=planner_routes.PlanItemCreate(id=cid, title="A"))
    assert out["id"] == cid


def test_create_same_id_is_idempotent(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    cid = str(uuid.uuid4())
    first = create(_req("alice"), body=planner_routes.PlanItemCreate(id=cid, title="A"))
    second = create(_req("alice"), body=planner_routes.PlanItemCreate(id=cid, title="A retried"))
    assert second["id"] == cid
    # idempotent: returns the existing row, does NOT create a duplicate or overwrite via create
    assert second["title"] == "A"
    db = SF()
    try:
        assert db.query(PlanItem).filter(PlanItem.id == cid).count() == 1
    finally:
        db.close()


def test_create_client_id_cross_owner_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    cid = str(uuid.uuid4())
    create(_req("bob"), body=planner_routes.PlanItemCreate(id=cid, title="bob's"))
    with pytest.raises(HTTPException) as exc:
        create(_req("alice"), body=planner_routes.PlanItemCreate(id=cid, title="alice steal"))
    assert exc.value.status_code == 404


def test_create_without_id_still_generates(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    out = create(_req("alice"), body=planner_routes.PlanItemCreate(title="no id"))
    assert out["id"]  # a server-generated uuid
