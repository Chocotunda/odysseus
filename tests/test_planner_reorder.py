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


def _seed(SF, owner, ordinal, title="t"):
    db = SF()
    try:
        it = PlanItem(id=str(uuid.uuid4()), owner=owner, title=title, ordinal=ordinal)
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


def test_between_formula():
    f = planner_routes._ordinal_between
    assert f(None, None) == 0
    assert f(None, 1024.0) == 1024.0 - 1024.0
    assert f(2048.0, None) == 2048.0 + 1024.0
    assert f(1024.0, 2048.0) == 1536.0


def test_reorder_into_middle_sets_float_midpoint(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    a = _seed(SF, "alice", 1024.0, "a")
    b = _seed(SF, "alice", 2048.0, "b")
    mover = _seed(SF, "alice", 4096.0, "mover")
    router = planner_routes.setup_planner_routes()
    reorder = _endpoint(router, "/items/{item_id}/reorder", "POST")
    out = reorder(_req("alice"), item_id=mover, body=planner_routes.ReorderBody(before_id=a, after_id=b))
    assert out["ordinal"] == 1536.0


def test_reorder_unknown_neighbor_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    mover = _seed(SF, "alice", 1024.0)
    router = planner_routes.setup_planner_routes()
    reorder = _endpoint(router, "/items/{item_id}/reorder", "POST")
    with pytest.raises(HTTPException) as exc:
        reorder(_req("alice"), item_id=mover, body=planner_routes.ReorderBody(before_id="nope", after_id=None))
    assert exc.value.status_code == 404
