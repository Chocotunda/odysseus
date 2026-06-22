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


def test_changes_no_since_returns_all_with_int_cursor(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    changes = _endpoint(router, "/items/changes", "GET")
    a = create(_req("alice"), body=planner_routes.PlanItemCreate(title="A"))
    out = changes(_req("alice"), since=None)
    assert {i["id"] for i in out["items"]} == {a["id"]}
    assert out["cursor"] == a["seq"]


def test_changes_since_is_exclusive_and_includes_tombstones(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    delete = _endpoint(router, "/items/{item_id}", "DELETE")
    changes = _endpoint(router, "/items/changes", "GET")

    a = create(_req("alice"), body=planner_routes.PlanItemCreate(title="A"))   # seq 1
    b = create(_req("alice"), body=planner_routes.PlanItemCreate(title="B"))   # seq 2
    tomb = delete(_req("alice"), item_id=b["id"])                              # seq 3, tombstone

    out = changes(_req("alice"), since=a["seq"])   # since=1 -> seq>1
    ids = {i["id"]: i for i in out["items"]}
    assert a["id"] not in ids            # seq 1 not > 1
    assert b["id"] in ids                # seq 2 and seq 3 both belong to b's id after delete
    assert ids[b["id"]]["deleted"] is True
    assert out["cursor"] == tomb["seq"]  # max seq returned


def test_changes_cursor_empty_batch_echoes_since(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    changes = _endpoint(router, "/items/changes", "GET")
    a = create(_req("alice"), body=planner_routes.PlanItemCreate(title="A"))
    out = changes(_req("alice"), since=a["seq"])   # nothing newer
    assert out["items"] == []
    assert out["cursor"] == a["seq"]
