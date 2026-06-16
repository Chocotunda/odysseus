"""Owner-scoping is a security boundary for the planner feature.

These tests pin the two P0 footguns the design review flagged:
  - P0-1: the per-row ownership gate must be strict — a row owned by another
    user (or a null-owner row) must 404, not be served/mutated.
  - P0-2: the list endpoint must NOT leak null-owner rows (no include_shared);
    background actions can mint null-owner rows, and in multi-user mode those
    would otherwise be visible to everyone.

They run against a real in-memory SQLite engine so the actual SQLAlchemy filter
logic is exercised, not a hand-rolled fake query.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from core.database import Base, PlanItem
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


def _seed(SessionFactory, owner, title="x"):
    db = SessionFactory()
    try:
        item = PlanItem(id=str(uuid.uuid4()), owner=owner, title=title)
        db.add(item)
        db.commit()
        return item.id
    finally:
        db.close()


def _endpoint(router, path, method):
    full = f"/api/planner{path}"
    for route in router.routes:
        if route.path == full and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_list_items_excludes_other_and_null_owner(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SessionFactory)
    alice_id = _seed(SessionFactory, "alice", "alice task")
    _seed(SessionFactory, "bob", "bob task")
    _seed(SessionFactory, None, "orphan task")

    router = planner_routes.setup_planner_routes()
    list_items = _endpoint(router, "/items", "GET")

    out = list_items(_request("alice"))
    returned_ids = {it["id"] for it in out["items"]}

    assert returned_ids == {alice_id}


def test_get_item_404_for_cross_owner(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SessionFactory)
    bob_id = _seed(SessionFactory, "bob")

    router = planner_routes.setup_planner_routes()
    get_item = _endpoint(router, "/items/{item_id}", "GET")

    with pytest.raises(HTTPException) as exc:
        get_item(_request("alice"), item_id=bob_id)
    assert exc.value.status_code == 404


def test_get_item_404_for_null_owner(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SessionFactory)
    orphan_id = _seed(SessionFactory, None)

    router = planner_routes.setup_planner_routes()
    get_item = _endpoint(router, "/items/{item_id}", "GET")

    with pytest.raises(HTTPException) as exc:
        get_item(_request("alice"), item_id=orphan_id)
    assert exc.value.status_code == 404
