import asyncio
import uuid
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


def test_seq_strictly_increases_per_write(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    delete = _endpoint(router, "/items/{item_id}", "DELETE")

    a = create(_req("alice"), body=planner_routes.PlanItemCreate(title="A"))
    b = create(_req("alice"), body=planner_routes.PlanItemCreate(title="B"))
    pa = patch(_req("alice"), item_id=a["id"], body=planner_routes.PlanItemPatch(title="A2"))
    db = delete(_req("alice"), item_id=b["id"])

    seqs = [a["seq"], b["seq"], pa["seq"], db["seq"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 4  # strictly increasing, unique
    assert pa["seq"] > b["seq"]   # patching A moved it past B in the change feed


def test_seq_is_per_owner(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    a1 = create(_req("alice"), body=planner_routes.PlanItemCreate(title="a1"))
    b1 = create(_req("bob"), body=planner_routes.PlanItemCreate(title="b1"))
    a2 = create(_req("alice"), body=planner_routes.PlanItemCreate(title="a2"))
    assert a1["seq"] == 1 and a2["seq"] == 2   # alice's own sequence
    assert b1["seq"] == 1                        # bob's independent sequence


def test_capture_assigns_seq(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    capture = _endpoint(router, "/capture", "POST")

    async def run():
        return await capture(_req("alice"), body=planner_routes.CaptureBody(text="buy milk"),
                             background_tasks=SimpleNamespace(add_task=lambda *a, **k: None))

    out = asyncio.run(run())
    assert out["seq"] == 1
