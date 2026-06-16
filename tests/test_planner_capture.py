"""NL quick-capture: the item is created immediately from the raw text
regardless of AI, the AI output is validated/coerced (never trusted for enum or
project correctness), and AI failure degrades to the raw item.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from core.database import Base, PlanItem, PlanProject
import routes.planner_routes as planner_routes
from src import planner_ai


# ---- pure coerce layer ----

def test_coerce_clamps_bad_priority():
    out = planner_ai.coerce_capture({"title": "x", "priority": "SUPER-URGENT"}, set())
    assert out["priority"] == "normal"


def test_coerce_keeps_valid_priority():
    out = planner_ai.coerce_capture({"title": "x", "priority": "urgent"}, set())
    assert out["priority"] == "urgent"


def test_coerce_drops_unknown_project():
    out = planner_ai.coerce_capture({"title": "x", "project": "ghost"}, {"p1", "p2"})
    assert out["project_id"] is None


def test_coerce_keeps_known_project():
    out = planner_ai.coerce_capture({"title": "x", "project": "p1"}, {"p1", "p2"})
    assert out["project_id"] == "p1"


def test_coerce_estimate_junk_becomes_none():
    out = planner_ai.coerce_capture({"title": "x", "estimate_minutes": "soon"}, set())
    assert out["estimate_minutes"] is None


def test_coerce_handles_non_dict():
    out = planner_ai.coerce_capture("not a dict", set())
    assert out["priority"] == "normal"
    assert out["project_id"] is None


# ---- capture endpoint ----

def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


def _capture_then_enrich(capture_ep, get_ep, user, text):
    """Run the capture endpoint (instant raw item), then drain its background
    enrichment task, then return (instant_response, enriched_item)."""
    from fastapi import BackgroundTasks

    async def run():
        bg = BackgroundTasks()
        out = await capture_ep(_request(user), planner_routes.CaptureBody(text=text), bg)
        await bg()  # execute the scheduled enrichment
        enriched = get_ep(_request(user), item_id=out["id"])
        return out, enriched
    return asyncio.run(run())


def test_capture_returns_raw_item_instantly(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)

    async def _slow(*a, **k):
        raise AssertionError("parse_capture must NOT run before the response returns")
    monkeypatch.setattr(planner_ai, "parse_capture", _slow)

    from fastapi import BackgroundTasks
    router = planner_routes.setup_planner_routes()
    capture = _endpoint(router, "/capture", "POST")

    async def run():
        return await capture(_request("alice"), planner_routes.CaptureBody(text="buy milk"), BackgroundTasks())
    out = asyncio.run(run())

    # immediate response is the raw item — no model in the request path
    assert out["title"] == "buy milk"
    assert out["status"] == "open"
    assert out["source"] == "capture"
    assert out["enriching"] is True
    assert out["ai_enriched"] is False


def test_enrichment_applies_coerced_ai_fields(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    db = SF()
    db.add(PlanProject(id="p1", owner="alice", name="Work"))
    db.commit()
    db.close()

    async def _parse(text, projects, owner=None):
        return {"title": "Ship report", "priority": "urgent",
                "estimate_minutes": 45, "project": "p1"}
    monkeypatch.setattr(planner_ai, "parse_capture", _parse)

    router = planner_routes.setup_planner_routes()
    capture = _endpoint(router, "/capture", "POST")
    get_item = _endpoint(router, "/items/{item_id}", "GET")

    raw, enriched = _capture_then_enrich(capture, get_item, "alice", "ship report fri ~45m #work !urgent")

    assert raw["title"] == "ship report fri ~45m #work !urgent"  # instant raw text
    assert enriched["title"] == "Ship report"                    # enriched
    assert enriched["priority"] == "urgent"
    assert enriched["estimate_minutes"] == 45
    assert enriched["project_id"] == "p1"
    assert enriched["ai_enriched"] is True


def test_enrichment_failure_keeps_raw_item(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)

    async def _boom(*a, **k):
        raise RuntimeError("model offline")
    monkeypatch.setattr(planner_ai, "parse_capture", _boom)

    router = planner_routes.setup_planner_routes()
    capture = _endpoint(router, "/capture", "POST")
    get_item = _endpoint(router, "/items/{item_id}", "GET")

    raw, enriched = _capture_then_enrich(capture, get_item, "alice", "buy milk")

    assert enriched["title"] == "buy milk"      # raw text preserved
    assert enriched["ai_enriched"] is True       # but enrichment was attempted (client stops polling)


def test_enrichment_drops_invalid_project(monkeypatch):
    SF = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)

    async def _parse(text, projects, owner=None):
        return {"title": "x", "project": "does-not-exist"}
    monkeypatch.setattr(planner_ai, "parse_capture", _parse)

    router = planner_routes.setup_planner_routes()
    capture = _endpoint(router, "/capture", "POST")
    get_item = _endpoint(router, "/items/{item_id}", "GET")

    raw, enriched = _capture_then_enrich(capture, get_item, "alice", "x")
    assert enriched["project_id"] is None
