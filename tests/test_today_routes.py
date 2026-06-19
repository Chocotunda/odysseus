"""Routes for the /today surface."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, PlanItem, Person, Note
from src import links as L
import routes.today_routes as today_routes


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _request(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _ep(router, path, method):
    full = f"/api/today{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_get_today_explicit_day(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF()
    db.add(PlanItem(id="s1", owner="alice", title="T", status="open",
                    planned_day="2026-06-20", planned_start="09:00"))
    db.commit(); db.close()
    router = today_routes.setup_today_routes()
    out = _ep(router, "", "GET")(_request("alice"), day="2026-06-20")
    assert [t["id"] for t in out["scheduled_tasks"]] == ["s1"]


def test_get_today_bad_date_400(monkeypatch):
    monkeypatch.setattr(today_routes, "SessionLocal", _sf())
    router = today_routes.setup_today_routes()
    with pytest.raises(HTTPException) as ei:
        _ep(router, "", "GET")(_request("alice"), day="not-a-date")
    assert ei.value.status_code == 400


def test_task_detail_includes_links(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF()
    db.add(PlanItem(id="t1", owner="alice", title="Ship", status="open"))
    db.add(Person(id="p1", owner="alice", name="Wiggert"))
    db.add(Note(id="n1", owner="alice", title="1:1 note"))
    db.commit()
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_FROM_NOTE, L.NODE_NOTE, "n1")
    db.close()
    router = today_routes.setup_today_routes()
    out = _ep(router, "/task/{task_id}", "GET")(_request("alice"), "t1")
    assert out["task"]["title"] == "Ship"
    assert [p["name"] for p in out["people"]] == ["Wiggert"]
    assert out["source_note"]["title"] == "1:1 note"


def test_task_detail_cross_owner_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF(); db.add(PlanItem(id="t1", owner="alice", title="Ship", status="open")); db.commit(); db.close()
    router = today_routes.setup_today_routes()
    with pytest.raises(HTTPException) as ei:
        _ep(router, "/task/{task_id}", "GET")(_request("bob"), "t1")
    assert ei.value.status_code == 404
