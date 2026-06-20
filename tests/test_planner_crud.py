"""Planner CRUD behavior: create stamps a concrete owner, the ordinal uses an
integer-gap scheme, complete records status+timestamp, and plan sets the day.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from core.database import Base
from core.hub_models import PlanItem
import routes.planner_routes as planner_routes


def _session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _request(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _endpoint(router, path, method):
    full = f"/api/planner{path}"
    for route in router.routes:
        if route.path == full and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_create_stamps_owner_and_defaults(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create_item = _endpoint(router, "/items", "POST")
    list_items = _endpoint(router, "/items", "GET")

    out = create_item(_request("alice"), planner_routes.PlanItemCreate(title="ship report"))

    assert out["title"] == "ship report"
    assert out["status"] == "open"
    # owner stamped concretely: visible to alice, not to bob
    assert {it["id"] for it in list_items(_request("alice"))["items"]} == {out["id"]}
    assert list_items(_request("bob"))["items"] == []


def test_create_uses_integer_gap_ordinal(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create_item = _endpoint(router, "/items", "POST")

    first = create_item(_request("alice"), planner_routes.PlanItemCreate(title="a"))
    second = create_item(_request("alice"), planner_routes.PlanItemCreate(title="b"))

    assert first["ordinal"] == 1024
    assert second["ordinal"] == 2048


def test_complete_sets_status_and_timestamp(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create_item = _endpoint(router, "/items", "POST")
    complete_item = _endpoint(router, "/items/{item_id}/complete", "POST")

    created = create_item(_request("alice"), planner_routes.PlanItemCreate(title="done me"))
    out = complete_item(_request("alice"), item_id=created["id"])

    assert out["status"] == "done"
    assert out["completed_at"] is not None


def test_plan_sets_and_clears_day(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create_item = _endpoint(router, "/items", "POST")
    plan_item = _endpoint(router, "/items/{item_id}/plan", "POST")

    created = create_item(_request("alice"), planner_routes.PlanItemCreate(title="plan me"))

    planned = plan_item(_request("alice"), item_id=created["id"],
                        body=planner_routes.PlanItemPlan(planned_day="2026-06-17"))
    assert planned["planned_day"] == "2026-06-17"

    backlog = plan_item(_request("alice"), item_id=created["id"],
                        body=planner_routes.PlanItemPlan(planned_day=None))
    assert backlog["planned_day"] is None


def test_complete_404_for_cross_owner(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create_item = _endpoint(router, "/items", "POST")
    complete_item = _endpoint(router, "/items/{item_id}/complete", "POST")

    created = create_item(_request("alice"), planner_routes.PlanItemCreate(title="alice only"))

    with pytest.raises(Exception) as exc:
        complete_item(_request("bob"), item_id=created["id"])
    assert getattr(exc.value, "status_code", None) == 404
