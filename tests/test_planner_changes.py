import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from core.database import Base, utcnow_naive
from core.hub_models import PlanItem
import routes.planner_routes as planner_routes


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _seed(SF, owner, updated_at, **kw):
    db = SF()
    try:
        it = PlanItem(id=str(uuid.uuid4()), owner=owner, title=kw.pop("title", "t"),
                      updated_at=updated_at, **kw)
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


def test_changes_no_since_returns_all_with_cursor(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    now = utcnow_naive()
    a = _seed(SF, "alice", now)
    router = planner_routes.setup_planner_routes()
    changes = _endpoint(router, "/items/changes", "GET")
    out = changes(_req("alice"), since=None)
    assert {i["id"] for i in out["items"]} == {a}
    assert out["cursor"] is not None


def test_changes_since_is_inclusive_and_includes_tombstones(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    old = utcnow_naive() - timedelta(hours=2)
    boundary = utcnow_naive() - timedelta(hours=1)
    old_id = _seed(SF, "alice", old, title="old")
    boundary_id = _seed(SF, "alice", boundary, title="boundary")
    tomb_id = _seed(SF, "alice", utcnow_naive(), title="tomb", deleted_at=utcnow_naive())
    router = planner_routes.setup_planner_routes()
    changes = _endpoint(router, "/items/changes", "GET")
    out = changes(_req("alice"), since=boundary.isoformat())
    ids = {i["id"] for i in out["items"]}
    assert boundary_id in ids        # inclusive >=
    assert old_id not in ids         # before the watermark
    assert tomb_id in ids            # tombstone surfaced
    assert next(i for i in out["items"] if i["id"] == tomb_id)["deleted"] is True


def test_changes_since_tz_aware_normalises_to_utc(monkeypatch):
    """A tz-aware ?since= in a non-UTC offset must be normalised to naive UTC.

    Passes the boundary instant as +05:30 offset; the endpoint must convert it
    back to the same UTC moment so the boundary row is returned (inclusive >=).
    This test FAILS against the buggy `astimezone(tz=None)` code (local tz) and
    PASSES after fixing to `astimezone(timezone.utc)`.
    """
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    old = utcnow_naive() - timedelta(hours=2)
    boundary = utcnow_naive() - timedelta(hours=1)
    _seed(SF, "alice", old, title="old")
    boundary_id = _seed(SF, "alice", boundary, title="boundary")
    router = planner_routes.setup_planner_routes()
    changes = _endpoint(router, "/items/changes", "GET")

    # Express the boundary instant as a tz-aware string in +05:30 (IST)
    boundary_utc_aware = boundary.replace(tzinfo=timezone.utc)
    boundary_ist = boundary_utc_aware.astimezone(timezone(timedelta(hours=5, minutes=30)))
    since_str = boundary_ist.isoformat()  # e.g. "...+05:30"

    out = changes(_req("alice"), since=since_str)
    ids = {i["id"] for i in out["items"]}
    assert boundary_id in ids   # normalised back to UTC → inclusive >=


def test_changes_malformed_since_400(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    changes = _endpoint(router, "/items/changes", "GET")
    with pytest.raises(HTTPException) as exc:
        changes(_req("alice"), since="not-a-date")
    assert exc.value.status_code == 400
