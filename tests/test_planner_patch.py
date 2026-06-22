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


def _seed(SF, owner, **kw):
    db = SF()
    try:
        it = PlanItem(id=str(uuid.uuid4()), owner=owner, title=kw.pop("title", "t"), **kw)
        db.add(it); db.commit()
        return it.id
    finally:
        db.close()


def _endpoint(router, path, method):
    full = f"/api/planner{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_patch_applies_only_sent_fields(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice", title="orig", notes="keep", planned_day="2026-06-22")
    router = planner_routes.setup_planner_routes()
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    out = patch(_req("alice"), item_id=iid, body=planner_routes.PlanItemPatch(title="new"))
    assert out["title"] == "new"
    assert out["notes"] == "keep"          # untouched
    assert out["planned_day"] == "2026-06-22"


def test_patch_explicit_null_clears_field(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice", planned_day="2026-06-22")
    router = planner_routes.setup_planner_routes()
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    body = planner_routes.PlanItemPatch.model_validate({"planned_day": None})
    out = patch(_req("alice"), item_id=iid, body=body)
    assert out["planned_day"] is None


def test_patch_clamps_bad_priority(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice")
    router = planner_routes.setup_planner_routes()
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    out = patch(_req("alice"), item_id=iid, body=planner_routes.PlanItemPatch(priority="bogus"))
    assert out["priority"] == "normal"


def test_patch_cross_owner_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "bob")
    router = planner_routes.setup_planner_routes()
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    with pytest.raises(HTTPException) as exc:
        patch(_req("alice"), item_id=iid, body=planner_routes.PlanItemPatch(title="x"))
    assert exc.value.status_code == 404
