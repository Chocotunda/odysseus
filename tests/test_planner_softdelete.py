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
import src.today as today_mod


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


def test_delete_soft_marks_and_excludes_from_list(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice", status="open")
    router = planner_routes.setup_planner_routes()
    delete = _endpoint(router, "/items/{item_id}", "DELETE")
    out = delete(_req("alice"), item_id=iid)
    assert out["deleted"] is True
    list_items = _endpoint(router, "/items", "GET")
    ids = {i["id"] for i in list_items(_req("alice"))["items"]}
    assert iid not in ids


def test_get_deleted_item_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice")
    router = planner_routes.setup_planner_routes()
    _endpoint(router, "/items/{item_id}", "DELETE")(_req("alice"), item_id=iid)
    get_item = _endpoint(router, "/items/{item_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        get_item(_req("alice"), item_id=iid)
    assert exc.value.status_code == 404


def test_day_view_excludes_tombstones(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice", status="open", planned_day="2026-06-22")
    router = planner_routes.setup_planner_routes()
    _endpoint(router, "/items/{item_id}", "DELETE")(_req("alice"), item_id=iid)
    db = SF()
    try:
        view = today_mod.day_view(db, "alice", "2026-06-22", today="2026-06-22")
    finally:
        db.close()
    # day_view returns scheduled_tasks / unscheduled_tasks / overdue_tasks
    all_ids = (
        {t["id"] for t in view.get("scheduled_tasks", [])}
        | {t["id"] for t in view.get("unscheduled_tasks", [])}
        | {t["id"] for t in view.get("overdue_tasks", [])}
    )
    assert iid not in all_ids
