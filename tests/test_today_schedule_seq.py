"""Regression: schedule_task must assign a non-NULL, increasing seq.

These tests would FAIL on the old code (seq never set → NULL after schedule)
and PASS after the fix (t.seq = next_plan_item_seq(db, t.owner) before commit).
"""
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import PlanItem
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


def test_schedule_task_assigns_seq(monkeypatch):
    """After scheduling, the PlanItem must have a non-NULL seq."""
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF()
    db.add(PlanItem(id="t1", owner="alice", title="Ship", status="open"))
    db.commit(); db.close()

    router = today_routes.setup_today_routes()
    _ep(router, "/task/{task_id}/schedule", "POST")(
        _request("alice"), "t1",
        today_routes.ScheduleBody(planned_day="2026-06-22", planned_start="10:00"))

    db2 = SF()
    t = db2.query(PlanItem).filter(PlanItem.id == "t1").one()
    assert t.seq is not None
    assert t.seq >= 1
    db2.close()


def test_schedule_task_seq_increases_past_existing(monkeypatch):
    """Scheduling must bump seq above the current max so it's visible in ?since=."""
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF()
    # seed one item with seq=5 (simulating prior writes)
    db.add(PlanItem(id="t1", owner="alice", title="Ship", status="open", seq=5))
    db.commit(); db.close()

    router = today_routes.setup_today_routes()
    _ep(router, "/task/{task_id}/schedule", "POST")(
        _request("alice"), "t1",
        today_routes.ScheduleBody(planned_day="2026-06-22", planned_start="10:00"))

    db2 = SF()
    t = db2.query(PlanItem).filter(PlanItem.id == "t1").one()
    assert t.seq == 6  # must be max(5) + 1
    db2.close()


def test_schedule_task_seq_visible_in_changes_filter(monkeypatch):
    """Scheduled item must appear in a seq > 0 filter (the ?since= pattern)."""
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF()
    db.add(PlanItem(id="t1", owner="alice", title="Ship", status="open"))
    db.commit(); db.close()

    router = today_routes.setup_today_routes()
    _ep(router, "/task/{task_id}/schedule", "POST")(
        _request("alice"), "t1",
        today_routes.ScheduleBody(planned_day="2026-06-22", planned_start=None))

    db2 = SF()
    visible = db2.query(PlanItem).filter(PlanItem.owner == "alice", PlanItem.seq > 0).all()
    assert len(visible) == 1
    assert visible[0].id == "t1"
    db2.close()
