# `/today` Daily Day-Planner Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new top-level `/today` page — a two-view (Overview + Timeline) daily day-planner that aggregates the day's tasks and calendar meetings over the existing management-hub graph, with internal-only drag time-blocking.

**Architecture:** A pure-logic `src/today.py` `day_view()` gathers meetings (reusing the calendar feature's rrule-expansion) + tasks (partitioned scheduled/unscheduled/overdue) + per-item Area, owner-scoped. A thin `routes/today_routes.py` exposes it plus a task-detail and a time-block `schedule` endpoint. A `static/js/today.js` page renders both views and (Phase 2) drag time-blocking. One new nullable `PlanItem.planned_start` field; no migration.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy (SQLite), pytest (`asyncio_mode=auto`), vanilla JS (no build step).

**Spec:** `docs/superpowers/specs/2026-06-19-management-hub-today-surface-design.md`

## Global Constraints

- **Owner-scoping is a security boundary.** Every query filters by `owner`. Routes use the `_owner = require_user(request) or None` convention then `if owner is not None and row.owner != owner: 404` (codebase-wide; the sec-scan "fail-open IDOR" flag on it is a known false positive — do not special-case).
- **`Link` is canonical for edges**; resolve Area via reverse-`Link` (`in_area`), never a per-row column. Go through `src/links.py` helpers.
- **No migrations** — new column appears via `create_all` (`planned_start` is NULL on existing rows = unscheduled, the correct default).
- **Local-string day convention**: `planned_day`/`due_date`/`planned_start` are local strings (`YYYY-MM-DD`, `HH:MM`); day/time matching is pure string equality. Only meetings use real datetime range math.
- **Paths/values via constants** where one exists; the 30-minute default duration lives in one named constant.
- **Frontend style is enforced**: reuse CSS vars (`--red`/`--fg`/`--bg`/`--card`/`--border`) and existing component classes, inline monochrome SVG (**no emoji**), Fira Code, dark theme. Escape every untrusted string via the module's `_esc`.
- **Conventional Commits** (`feat(hub): …`, `test(hub): …`). Commit message footer is up to the executor; keep PRs/commits small and single-purpose.
- Backend does **not** auto-reload — restart uvicorn on `:7860` before any live verify.

---

## File Structure

- **Create** `src/today.py` — `day_view()` + `DEFAULT_ESTIMATE_MIN` constant + `_local_today()`. Pure logic, owner-scoped. (Tasks 1–2)
- **Create** `routes/today_routes.py` — `setup_today_routes()` → `GET /api/today`, `GET /api/today/task/{id}`, `POST /api/today/task/{id}/schedule`. HTTP-thin. (Tasks 3–4, 6)
- **Modify** `core/database.py` — add `PlanItem.planned_start` column. (Task 1)
- **Modify** `app.py` — register the router + `/today` SPA shell route. (Task 5)
- **Create** `static/js/today.js` — the page (both views, detail panel, day-nav, complete; drag in Task 7). (Tasks 5, 7)
- **Modify** `static/index.html` — nav button `tool-today-btn` + favicon/title entry. (Task 5)
- **Modify** `static/app.js` — import + button wiring + `/today` route. (Task 5)
- **Modify** `static/js/slashCommands.js` — `/today` command + `_cmdOpen` target. (Task 5)
- **Modify** `static/style.css` — `.today-*` block. (Tasks 5, 7)
- **Create** `tests/test_today.py` — `day_view` unit tests. (Tasks 1–2)
- **Create** `tests/test_today_routes.py` — route tests. (Tasks 3–4, 6)

**Build order:** Tasks 1–5 = Phase 1 (read foundation, ships on its own). Tasks 6–7 = Phase 2 (interactivity).

Run all tests with the venv: `./venv/bin/python -m pytest <file> -q`.

---

## Task 1: `planned_start` field + task partitioning in `day_view`

**Files:**
- Modify: `core/database.py` (PlanItem, ~line 1683 near `estimate_minutes`)
- Create: `src/today.py`
- Test: `tests/test_today.py`

**Interfaces:**
- Produces: `src.today.DEFAULT_ESTIMATE_MIN = 30`; `src.today._local_today() -> str` (`"YYYY-MM-DD"`); `src.today.day_view(db, owner, day, *, today=None) -> dict` with keys `day, is_today, meetings, scheduled_tasks, unscheduled_tasks, overdue_tasks, capacity_minutes`. Each task dict: `{id,title,planned_start,planned_day,due_date,priority,estimate_minutes,status,area_id,area_name,area_color}`.
- `PlanItem.planned_start`: nullable `String`, `"HH:MM"` local. Task is scheduled iff `planned_start` non-null **and** `planned_day == day`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_today.py`:

```python
"""The /today daily day-planner: day_view aggregation + routes."""
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, PlanItem, CalendarCal, CalendarEvent, Area, utcnow_naive
from src import links as L
from src import today as T


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_day_view_partitions_tasks():
    db = _db()
    db.add(PlanItem(id="s1", owner="alice", title="Blocked", status="open",
                    planned_day="2026-06-20", planned_start="09:00", estimate_minutes=45))
    db.add(PlanItem(id="u1", owner="alice", title="Anytime", status="open",
                    planned_day="2026-06-20"))                       # no planned_start -> unscheduled
    db.add(PlanItem(id="o1", owner="alice", title="Late", status="open",
                    due_date="2026-06-18"))                          # overdue
    db.add(PlanItem(id="d1", owner="alice", title="Done", status="done",
                    planned_day="2026-06-20", planned_start="10:00"))  # excluded
    db.commit()

    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert [t["id"] for t in v["scheduled_tasks"]] == ["s1"]
    assert [t["id"] for t in v["unscheduled_tasks"]] == ["u1"]
    assert [t["id"] for t in v["overdue_tasks"]] == ["o1"]
    assert v["is_today"] is True
    # capacity = 45 (s1) + 30 default (u1); overdue not counted toward the day
    assert v["capacity_minutes"] == 75


def test_overdue_only_when_viewing_today():
    db = _db()
    db.add(PlanItem(id="o1", owner="alice", title="Late", status="open", due_date="2026-06-18"))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-21", today="2026-06-20")   # viewing a future day
    assert v["overdue_tasks"] == []
    assert v["is_today"] is False


def test_day_view_owner_isolated():
    db = _db()
    db.add(PlanItem(id="s1", owner="alice", title="A", status="open",
                    planned_day="2026-06-20", planned_start="09:00"))
    db.add(PlanItem(id="s2", owner="bob", title="B", status="open",
                    planned_day="2026-06-20", planned_start="09:00"))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert [t["id"] for t in v["scheduled_tasks"]] == ["s1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_today.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.today'` (and `PlanItem` has no `planned_start`).

- [ ] **Step 3: Add the column**

In `core/database.py`, in `PlanItem`, immediately after the `due_date` line (~1678) add:

```python
    planned_start = Column(String, nullable=True)              # 'HH:MM' local; set => time-blocked
```

- [ ] **Step 4: Create `src/today.py`**

```python
"""The /today daily day-planner read model.

Pure, owner-scoped logic that gathers a single day's tasks + meetings for the
/today surface. Tasks use the local-string convention (planned_day/planned_start
'HH:MM', due_date 'YYYY-MM-DD'); only meetings need real datetime math, for which
we reuse the calendar feature's rrule expansion. Areas are resolved via the
reverse-Link (in_area) spine, never a per-row column.
"""
from datetime import datetime
from typing import Optional

from core.database import PlanItem, CalendarCal, CalendarEvent, Area, Link
from src.links import REL_IN_AREA, NODE_TASK, NODE_MEETING

DEFAULT_ESTIMATE_MIN = 30


def _local_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _est(t: PlanItem) -> int:
    return t.estimate_minutes if t.estimate_minutes else DEFAULT_ESTIMATE_MIN


def _task_dict(t: PlanItem) -> dict:
    return {
        "id": t.id, "title": t.title, "planned_start": t.planned_start,
        "planned_day": t.planned_day, "due_date": t.due_date,
        "priority": t.priority, "estimate_minutes": t.estimate_minutes,
        "status": t.status,
        "area_id": None, "area_name": None, "area_color": None,
    }


def day_view(db, owner: Optional[str], day: str, *, today: Optional[str] = None) -> dict:
    today = today or _local_today()
    is_today = (day == today)

    q = db.query(PlanItem).filter(PlanItem.status == "open")
    if owner is not None:
        q = q.filter(PlanItem.owner == owner)
    open_tasks = q.all()

    scheduled, unscheduled, overdue = [], [], []
    for t in open_tasks:
        if t.planned_day == day and t.planned_start:
            scheduled.append(t)
        elif t.planned_day == day:
            unscheduled.append(t)
        elif is_today and t.due_date and t.due_date < today and t.planned_day != day:
            overdue.append(t)
    scheduled.sort(key=lambda t: (t.planned_start, t.ordinal or 0))
    unscheduled.sort(key=lambda t: (t.ordinal or 0))
    overdue.sort(key=lambda t: (t.due_date, t.ordinal or 0))

    day_tasks = scheduled + unscheduled
    capacity = sum(_est(t) for t in day_tasks)

    meetings = _meetings_for_day(db, owner, day)

    sched_d = [_task_dict(t) for t in scheduled]
    unsched_d = [_task_dict(t) for t in unscheduled]
    overdue_d = [_task_dict(t) for t in overdue]
    _attach_areas(db, owner, sched_d + unsched_d + overdue_d, meetings)

    return {
        "day": day, "is_today": is_today,
        "meetings": meetings,
        "scheduled_tasks": sched_d,
        "unscheduled_tasks": unsched_d,
        "overdue_tasks": overdue_d,
        "capacity_minutes": capacity,
    }


def _meetings_for_day(db, owner, day):     # filled in Task 2
    return []


def _attach_areas(db, owner, task_dicts, meeting_dicts):   # filled in Task 2
    return
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_today.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add core/database.py src/today.py tests/test_today.py
git commit -m "feat(hub): planned_start field + day_view task partitioning"
```

---

## Task 2: Meetings (rrule-aware) + Area resolution in `day_view`

**Files:**
- Modify: `src/today.py` (`_meetings_for_day`, `_attach_areas`)
- Test: `tests/test_today.py`

**Interfaces:**
- Consumes: `routes.calendar_routes._expand_rrule(ev, start_dt, end_dt) -> list[dict]` and `routes.calendar_routes._event_to_dict(ev) -> dict` (module-level helpers); `src.links` Area constants.
- Produces: `_meetings_for_day` returns meeting dicts each with at least `uid, series_uid, summary, dtstart, dtend, all_day, location` (+ `area_*` after `_attach_areas`). `_attach_areas` mutates the passed dicts in place, setting `area_id/area_name/area_color`; for meetings it keys off `series_uid` (the base uid the Link points at).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_today.py`:

```python
def _on(day, hour):
    return datetime.strptime(f"{day} {hour:02d}:00", "%Y-%m-%d %H:%M")


def test_day_view_includes_meetings_on_day():
    db = _db()
    db.add(CalendarCal(id="c1", owner="alice", name="Cal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Standup",
                         dtstart=_on("2026-06-20", 9), dtend=_on("2026-06-20", 10)))
    db.add(CalendarEvent(uid="m2", calendar_id="c1", summary="Other day",
                         dtstart=_on("2026-06-21", 9), dtend=_on("2026-06-21", 10)))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert [m["summary"] for m in v["meetings"]] == ["Standup"]


def test_day_view_meetings_owner_isolated():
    db = _db()
    db.add(CalendarCal(id="c1", owner="bob", name="Bob cal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Bob mtg",
                         dtstart=_on("2026-06-20", 9), dtend=_on("2026-06-20", 10)))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert v["meetings"] == []


def test_day_view_attaches_area_to_task_and_meeting():
    db = _db()
    db.add(Area(id="aw", owner="alice", name="Work", color="#a60717"))
    db.add(PlanItem(id="s1", owner="alice", title="T", status="open",
                    planned_day="2026-06-20", planned_start="09:00"))
    db.add(CalendarCal(id="c1", owner="alice", name="Cal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Mtg",
                         dtstart=_on("2026-06-20", 11), dtend=_on("2026-06-20", 12)))
    db.commit()
    L.set_area(db, "alice", L.NODE_TASK, "s1", "aw")
    L.set_area(db, "alice", L.NODE_MEETING, "m1", "aw")

    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert v["scheduled_tasks"][0]["area_name"] == "Work"
    assert v["scheduled_tasks"][0]["area_color"] == "#a60717"
    assert v["meetings"][0]["area_name"] == "Work"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_today.py -q`
Expected: FAIL — meetings empty / `area_name` is None.

- [ ] **Step 3: Implement `_meetings_for_day` and `_attach_areas`**

Replace the two stubs in `src/today.py`:

```python
def _meetings_for_day(db, owner, day):
    # Reuse the calendar feature's range + rrule expansion so recurring
    # meetings (standups, 1:1s) correctly land on the day. tz handling matches
    # the calendar's own list_events (naive-local windows; is_utc honored on
    # serialization). This is the one place we use real datetime math.
    from routes.calendar_routes import _expand_rrule
    start_dt = datetime.strptime(f"{day} 00:00", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(days=1)

    cq = db.query(CalendarCal)
    if owner is not None:
        cq = cq.filter(CalendarCal.owner == owner)
    cal_ids = [c.id for c in cq.all()]
    if not cal_ids:
        return []

    rows = (db.query(CalendarEvent)
            .filter(CalendarEvent.calendar_id.in_(cal_ids),
                    CalendarEvent.dtstart < end_dt,
                    CalendarEvent.dtend > start_dt)
            .all())
    out = []
    for ev in rows:
        for d in _expand_rrule(ev, start_dt, end_dt):
            d.setdefault("series_uid", ev.uid)
            d["area_id"] = None
            d["area_name"] = None
            d["area_color"] = None
            out.append(d)
    out.sort(key=lambda m: (not m.get("all_day", False), str(m.get("dtstart") or "")))
    return out


def _attach_areas(db, owner, task_dicts, meeting_dicts):
    pairs = [(NODE_TASK, t["id"]) for t in task_dicts]
    pairs += [(NODE_MEETING, m.get("series_uid") or m.get("uid")) for m in meeting_dicts]
    if not pairs:
        return
    from_ids = list({pid for _, pid in pairs})
    edges = (db.query(Link)
             .filter(Link.owner == owner, Link.rel == REL_IN_AREA,
                     Link.from_id.in_(from_ids))
             .all())
    area_of = {(e.from_type, e.from_id): e.to_id for e in edges}
    aids = set(area_of.values())
    areas = {a.id: a for a in db.query(Area).filter(Area.id.in_(aids)).all()} if aids else {}

    def _stamp(d, node_type, node_id):
        aid = area_of.get((node_type, node_id))
        a = areas.get(aid) if aid else None
        if a:
            d["area_id"], d["area_name"], d["area_color"] = a.id, a.name, a.color

    for t in task_dicts:
        _stamp(t, NODE_TASK, t["id"])
    for m in meeting_dicts:
        _stamp(m, NODE_MEETING, m.get("series_uid") or m.get("uid"))
```

Add `timedelta` to the top import: `from datetime import datetime, timedelta`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_today.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/today.py tests/test_today.py
git commit -m "feat(hub): day_view meetings (rrule-aware) + Area resolution"
```

---

## Task 3: `GET /api/today` + task-detail routes

**Files:**
- Create: `routes/today_routes.py`
- Test: `tests/test_today_routes.py`

**Interfaces:**
- Consumes: `src.today.day_view`, `src.auth_helpers.require_user`, `src.links` helpers.
- Produces: `setup_today_routes() -> APIRouter` (prefix `/api/today`). `GET ""` (query `day` optional) → `day_view`. `GET /task/{task_id}` → `{task, people, source_note, area}` with cross-owner 404.

- [ ] **Step 1: Write the failing test**

Create `tests/test_today_routes.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_today_routes.py -q`
Expected: FAIL — `No module named 'routes.today_routes'`.

- [ ] **Step 3: Create `routes/today_routes.py`**

```python
# routes/today_routes.py
"""The /today daily day-planner API — HTTP-thin. Logic lives in src/today.py."""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, PlanItem, Person, Note, Area
from src.auth_helpers import require_user
from src import today as today_logic
from src import links as L

logger = logging.getLogger(__name__)


def _valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _valid_time(s: str) -> bool:
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except (ValueError, TypeError):
        return False


class ScheduleBody(BaseModel):
    planned_day: str
    planned_start: Optional[str] = None


def setup_today_routes() -> APIRouter:
    router = APIRouter(prefix="/api/today", tags=["today"])

    def _owner(request: Request) -> Optional[str]:
        return require_user(request) or None

    def _load_task(db, request: Request, task_id: str) -> PlanItem:
        owner = _owner(request)
        t = db.query(PlanItem).filter(PlanItem.id == task_id).first()
        if not t or (owner is not None and t.owner != owner):
            raise HTTPException(status_code=404, detail="task not found")
        return t

    @router.get("")
    def get_today(request: Request, day: str = ""):
        owner = _owner(request)
        if day and not _valid_date(day):
            raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
        db = SessionLocal()
        try:
            return today_logic.day_view(db, owner, day or today_logic._local_today())
        finally:
            db.close()

    @router.get("/task/{task_id}")
    def task_detail(request: Request, task_id: str):
        db = SessionLocal()
        try:
            t = _load_task(db, request, task_id)
            owner = t.owner
            people: List[Dict[str, Any]] = []
            for e in L.links_from(db, owner, L.NODE_TASK, t.id, rel=L.REL_ABOUT):
                if e.to_type == L.NODE_PERSON:
                    p = db.query(Person).filter(Person.id == e.to_id).first()
                    if p:
                        people.append({"id": p.id, "name": p.name, "role": p.role})
            source_note = None
            for e in L.links_from(db, owner, L.NODE_TASK, t.id, rel=L.REL_FROM_NOTE):
                n = db.query(Note).filter(Note.id == e.to_id).first()
                if n:
                    source_note = {"id": n.id, "title": n.title}
                    break
            area = None
            for e in L.links_from(db, owner, L.NODE_TASK, t.id, rel=L.REL_IN_AREA):
                a = db.query(Area).filter(Area.id == e.to_id).first()
                if a:
                    area = {"id": a.id, "name": a.name, "color": a.color}
                    break
            return {
                "task": {"id": t.id, "title": t.title, "notes": t.notes,
                         "planned_day": t.planned_day, "planned_start": t.planned_start,
                         "due_date": t.due_date, "priority": t.priority,
                         "estimate_minutes": t.estimate_minutes, "status": t.status},
                "people": people, "source_note": source_note, "area": area,
            }
        finally:
            db.close()

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_today_routes.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add routes/today_routes.py tests/test_today_routes.py
git commit -m "feat(hub): /api/today day view + task-detail routes"
```

---

## Task 4: `POST /api/today/task/{id}/schedule` (time-block set/move/clear)

**Files:**
- Modify: `routes/today_routes.py` (add the endpoint before `return router`)
- Test: `tests/test_today_routes.py`

**Interfaces:**
- Produces: `POST /task/{task_id}/schedule` body `{planned_day: str, planned_start: str|null}` → updated task dict `{id,planned_day,planned_start}`. Sets/moves the block; `planned_start=null` returns it to the rail. Validates formats (400); cross-owner 404.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_today_routes.py`:

```python
def test_schedule_sets_and_clears(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF(); db.add(PlanItem(id="t1", owner="alice", title="T", status="open")); db.commit(); db.close()
    router = today_routes.setup_today_routes()
    ep = _ep(router, "/task/{task_id}/schedule", "POST")

    out = ep(_request("alice"), "t1", today_routes.ScheduleBody(planned_day="2026-06-20", planned_start="09:30"))
    assert out["planned_day"] == "2026-06-20" and out["planned_start"] == "09:30"

    out2 = ep(_request("alice"), "t1", today_routes.ScheduleBody(planned_day="2026-06-20", planned_start=None))
    assert out2["planned_start"] is None


def test_schedule_bad_time_400(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF(); db.add(PlanItem(id="t1", owner="alice", title="T", status="open")); db.commit(); db.close()
    router = today_routes.setup_today_routes()
    with pytest.raises(HTTPException) as ei:
        _ep(router, "/task/{task_id}/schedule", "POST")(
            _request("alice"), "t1", today_routes.ScheduleBody(planned_day="2026-06-20", planned_start="9am"))
    assert ei.value.status_code == 400


def test_schedule_cross_owner_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(today_routes, "SessionLocal", SF)
    db = SF(); db.add(PlanItem(id="t1", owner="alice", title="T", status="open")); db.commit(); db.close()
    router = today_routes.setup_today_routes()
    with pytest.raises(HTTPException) as ei:
        _ep(router, "/task/{task_id}/schedule", "POST")(
            _request("bob"), "t1", today_routes.ScheduleBody(planned_day="2026-06-20", planned_start="09:00"))
    assert ei.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_today_routes.py -q`
Expected: FAIL — route `/api/today/task/{task_id}/schedule` not found.

- [ ] **Step 3: Add the endpoint**

In `routes/today_routes.py`, before `return router`:

```python
    @router.post("/task/{task_id}/schedule")
    def schedule_task(request: Request, task_id: str, body: ScheduleBody):
        if not _valid_date(body.planned_day):
            raise HTTPException(status_code=400, detail="planned_day must be YYYY-MM-DD")
        if body.planned_start is not None and not _valid_time(body.planned_start):
            raise HTTPException(status_code=400, detail="planned_start must be HH:MM")
        db = SessionLocal()
        try:
            t = _load_task(db, request, task_id)
            t.planned_day = body.planned_day
            t.planned_start = body.planned_start
            db.commit()
            db.refresh(t)
            return {"id": t.id, "planned_day": t.planned_day, "planned_start": t.planned_start}
        finally:
            db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_today_routes.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add routes/today_routes.py tests/test_today_routes.py
git commit -m "feat(hub): time-block schedule endpoint for /today tasks"
```

---

## Task 5: Wire `/today` into the app + the read-only frontend (both views)

**Files:**
- Modify: `app.py` (router registration ~line 777; SPA shell route ~line 878)
- Create: `static/js/today.js`
- Modify: `static/index.html` (nav button near `tool-areas-btn` ~line 936; favicon/title inline script)
- Modify: `static/app.js` (import ~line 30; button wiring ~line 945; route map ~line 1038)
- Modify: `static/js/slashCommands.js` (`_cmdOpen` map ~line 1352; command def ~line 6010)
- Modify: `static/style.css` (append `.today-*` block)

**Interfaces:**
- Consumes: `GET /api/today`, `GET /api/today/task/{id}`, `POST /api/planner/items/{id}/complete`.
- Produces: `todayModule` default export with `openToday()`; nav id `tool-today-btn`; route `/today`; slash `/today`.

- [ ] **Step 1: Register the router and shell route in `app.py`**

After the area router registration (~line 777) add:

```python
from routes.today_routes import setup_today_routes
app.include_router(setup_today_routes())
```

After `serve_area` (~line 882) add:

```python
@app.get("/today")
async def serve_today(request: Request):
    return await serve_index(request)
```

- [ ] **Step 2: Verify the router mounts**

Run: `./venv/bin/python -c "import app; paths=[r.path for r in app.app.routes]; assert '/api/today' in paths and '/today' in paths, paths; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Create `static/js/today.js`**

Mirror `static/js/areas.js` (injected-panel pattern, `_esc`, esc-to-close, inline SVG). Write the full module:

```javascript
// static/js/today.js
// /today — daily day-planner. Two views (Overview / Timeline) over one day
// model (tasks + meetings + areas). Mirrors the Areas/People injected-panel
// pattern; reuses global CSS vars. No build step, no emoji.
//
// Exposes: window.openToday().

const API_BASE = window.location.origin;
const HOUR_START = 6, HOUR_END = 22, PX_PER_MIN = 0.8, DEFAULT_EST = 30;

let _open = false;
let _escHandler = null;
let _view = 'overview';                 // 'overview' | 'timeline'
let _day = null;                        // 'YYYY-MM-DD' currently shown

const ICON_TODAY =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>';
const ICON_CLOSE =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function _shiftDay(day, delta) {
  const [y, m, d] = day.split('-').map(Number);
  const dt = new Date(y, m - 1, d + delta);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

async function _fetchDay(day) {
  const res = await fetch(`${API_BASE}/api/today?day=${encodeURIComponent(day)}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`today ${res.status}`);
  return res.json();
}
async function _fetchTask(id) {
  const res = await fetch(`${API_BASE}/api/today/task/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`task ${res.status}`);
  return res.json();
}
async function _completeTask(id) {
  await fetch(`${API_BASE}/api/planner/items/${encodeURIComponent(id)}/complete`,
              { method: 'POST', credentials: 'same-origin' });
}

function _fmtTime(hhmm) { return hhmm || ''; }
function _evTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return _esc(String(iso).slice(11, 16));
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function _areaChip(item) {
  if (!item.area_name) return '';
  return `<span class="today-area-chip" style="border-color:${_esc(item.area_color || '#888')}">` +
         `${_esc(item.area_name)}</span>`;
}

function _taskRow(t, cls) {
  const est = t.estimate_minutes ? ` &middot; ~${t.estimate_minutes}m` : '';
  const due = t.due_date ? ` &middot; due ${_esc(t.due_date)}` : '';
  return `<div class="today-item ${cls}" data-task="${_esc(t.id)}">` +
    `<span class="today-item-title">${_esc(t.title || '(untitled)')}</span>` +
    _areaChip(t) +
    `<span class="today-item-meta">${_esc(t.priority || '')}${est}${due}</span></div>`;
}
function _meetingRow(m) {
  return `<div class="today-item today-meeting" data-meeting="${_esc(m.uid)}">` +
    `<span class="today-item-time">${m.all_day ? 'all-day' : _evTime(m.dtstart)}</span>` +
    `<span class="today-item-title">${_esc(m.summary || '(busy)')}</span>` +
    _areaChip(m) +
    (m.location ? `<span class="today-item-meta">${_esc(m.location)}</span>` : '') + `</div>`;
}

function _section(label, count, bodyHtml, empty) {
  return `<div class="today-section"><div class="today-section-header">${_esc(label)}` +
    `<span class="today-count">${count}</span></div>` +
    `<div class="today-section-body">${count ? bodyHtml : `<div class="today-empty">${_esc(empty)}</div>`}</div></div>`;
}

function _renderOverview(v) {
  const cap = v.capacity_minutes ? `${Math.floor(v.capacity_minutes / 60)}h ${v.capacity_minutes % 60}m planned` : 'nothing planned';
  const overdue = v.is_today
    ? _section('Overdue', v.overdue_tasks.length, v.overdue_tasks.map(t => _taskRow(t, 'today-overdue')).join(''), 'no overdue tasks')
    : '';
  const meetings = _section('Meetings', v.meetings.length, v.meetings.map(_meetingRow).join(''), 'no meetings');
  const tasks = _section('Tasks', v.scheduled_tasks.length + v.unscheduled_tasks.length,
    v.scheduled_tasks.concat(v.unscheduled_tasks).map(t => _taskRow(t, '')).join(''), 'no tasks for this day');
  return `<div class="today-overview">${overdue}${meetings}${tasks}` +
    `<div class="today-capacity">${_esc(cap)}</div></div>`;
}

function _renderTimeline(v) {
  let hours = '';
  for (let h = HOUR_START; h <= HOUR_END; h++) {
    hours += `<div class="today-hour" style="height:${60 * PX_PER_MIN}px"><span class="today-hour-label">` +
      `${String(h).padStart(2, '0')}:00</span></div>`;
  }
  let blocks = '';
  const place = (startMin, mins, title, cls, attr) => {
    const top = (startMin - HOUR_START * 60) * PX_PER_MIN;
    const height = Math.max(18, mins * PX_PER_MIN);
    return `<div class="today-block ${cls}" ${attr} style="top:${top}px;height:${height}px">` +
      `<span class="today-block-title">${_esc(title)}</span></div>`;
  };
  v.meetings.filter(m => !m.all_day).forEach(m => {
    const t = _evTime(m.dtstart); if (!t) return;
    const [hh, mm] = t.split(':').map(Number);
    const durMin = 60; // visual default; real end parsed below if available
    let end = m.dtend ? _evTime(m.dtend) : '';
    let dm = durMin;
    if (end) { const [eh, em] = end.split(':').map(Number); dm = Math.max(15, (eh * 60 + em) - (hh * 60 + mm)); }
    blocks += place(hh * 60 + mm, dm, m.summary || '(busy)', 'today-block-meeting', `data-meeting="${_esc(m.uid)}"`);
  });
  v.scheduled_tasks.forEach(t => {
    const [hh, mm] = (t.planned_start || '00:00').split(':').map(Number);
    blocks += place(hh * 60 + mm, t.estimate_minutes || DEFAULT_EST, t.title || '(task)',
                    'today-block-task', `data-task="${_esc(t.id)}"`);
  });
  const rail = v.unscheduled_tasks.concat(v.is_today ? v.overdue_tasks : [])
    .map(t => _taskRow(t, 'today-rail-item')).join('') || `<div class="today-empty">nothing unscheduled</div>`;
  return `<div class="today-timeline-wrap"><div class="today-grid"><div class="today-hours">${hours}` +
    `<div class="today-blocks">${blocks}</div></div></div>` +
    `<div class="today-rail"><div class="today-section-header">Unscheduled</div>${rail}</div></div>`;
}

function _render(v) {
  const panel = document.getElementById('today-body');
  if (!panel) return;
  document.getElementById('today-date-label').textContent =
    v.is_today ? `Today · ${v.day}` : v.day;
  panel.innerHTML = _view === 'overview' ? _renderOverview(v) : _renderTimeline(v);
  document.querySelectorAll('#today-body [data-task]').forEach(elm =>
    elm.addEventListener('click', () => _showTaskDetail(elm.getAttribute('data-task'))));
  document.querySelectorAll('#today-body [data-meeting]').forEach(elm =>
    elm.addEventListener('click', () => _showMeetingDetail(v, elm.getAttribute('data-meeting'))));
}

async function _reload() {
  try { _render(await _fetchDay(_day)); }
  catch (e) { const b = document.getElementById('today-body'); if (b) b.innerHTML = `<div class="today-empty">failed to load</div>`; }
}

async function _showTaskDetail(id) {
  let d; try { d = await _fetchTask(id); } catch (e) { return; }
  const t = d.task;
  const ppl = d.people.length ? `<div class="today-detail-row">People: ${d.people.map(p => _esc(p.name)).join(', ')}</div>` : '';
  const note = d.source_note ? `<div class="today-detail-row">From note: ${_esc(d.source_note.title)}</div>` : '';
  const area = d.area ? `<div class="today-detail-row">Area: ${_esc(d.area.name)}</div>` : '';
  _openDetail(t.title, [
    t.due_date ? `<div class="today-detail-row">Due ${_esc(t.due_date)}</div>` : '',
    t.priority ? `<div class="today-detail-row">Priority: ${_esc(t.priority)}</div>` : '',
    t.estimate_minutes ? `<div class="today-detail-row">Estimate: ~${t.estimate_minutes}m</div>` : '',
    area, ppl, note,
    t.notes ? `<div class="today-detail-notes">${_esc(t.notes)}</div>` : '',
  ].join(''), `<button class="today-complete-btn" data-complete="${_esc(t.id)}">Complete</button>`);
  const btn = document.querySelector('[data-complete]');
  if (btn) btn.addEventListener('click', async () => { await _completeTask(t.id); _closeDetail(); _reload(); });
}

function _showMeetingDetail(v, uid) {
  const m = v.meetings.find(x => x.uid === uid); if (!m) return;
  _openDetail(m.summary || '(busy)', [
    `<div class="today-detail-row">${m.all_day ? 'All day' : _evTime(m.dtstart) + ' – ' + _evTime(m.dtend)}</div>`,
    m.location ? `<div class="today-detail-row">${_esc(m.location)}</div>` : '',
    m.area_name ? `<div class="today-detail-row">Area: ${_esc(m.area_name)}</div>` : '',
    m.description ? `<div class="today-detail-notes">${_esc(m.description)}</div>` : '',
  ].join(''), '');
}

function _openDetail(title, bodyHtml, actionsHtml) {
  let p = document.getElementById('today-detail');
  if (!p) {
    p = document.createElement('div'); p.id = 'today-detail'; p.className = 'today-detail';
    document.getElementById('today-panel').appendChild(p);
  }
  p.innerHTML = `<div class="today-detail-head"><span>${_esc(title)}</span>` +
    `<button id="today-detail-close" class="icon-btn">${ICON_CLOSE}</button></div>` +
    `<div class="today-detail-body">${bodyHtml}</div><div class="today-detail-actions">${actionsHtml}</div>`;
  p.classList.add('open');
  document.getElementById('today-detail-close').addEventListener('click', _closeDetail);
}
function _closeDetail() { const p = document.getElementById('today-detail'); if (p) p.classList.remove('open'); }

function _close() {
  _open = false;
  const p = document.getElementById('today-panel'); if (p) p.remove();
  if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
}

function openToday() {
  if (_open) return;
  _open = true; _day = _day || _localToday();
  const panel = document.createElement('div');
  panel.id = 'today-panel'; panel.className = 'today-panel';
  panel.innerHTML =
    `<div class="today-header">` +
      `<div class="today-title">${ICON_TODAY}<span id="today-date-label">Today</span></div>` +
      `<div class="today-nav">` +
        `<button id="today-prev" class="icon-btn">&#8592;</button>` +
        `<button id="today-now" class="today-now-btn">Today</button>` +
        `<button id="today-next" class="icon-btn">&#8594;</button>` +
        `<input type="date" id="today-date-input" class="today-date-input">` +
      `</div>` +
      `<div class="today-views">` +
        `<button id="today-view-overview" class="today-view-btn active">Overview</button>` +
        `<button id="today-view-timeline" class="today-view-btn">Timeline</button>` +
      `</div>` +
      `<button id="today-close" class="icon-btn">${ICON_CLOSE}</button>` +
    `</div><div id="today-body" class="today-body"></div>`;
  document.body.appendChild(panel);

  const setView = (vw) => {
    _view = vw;
    document.getElementById('today-view-overview').classList.toggle('active', vw === 'overview');
    document.getElementById('today-view-timeline').classList.toggle('active', vw === 'timeline');
    _reload();
  };
  document.getElementById('today-close').addEventListener('click', _close);
  document.getElementById('today-view-overview').addEventListener('click', () => setView('overview'));
  document.getElementById('today-view-timeline').addEventListener('click', () => setView('timeline'));
  document.getElementById('today-prev').addEventListener('click', () => { _day = _shiftDay(_day, -1); _reload(); });
  document.getElementById('today-next').addEventListener('click', () => { _day = _shiftDay(_day, 1); _reload(); });
  document.getElementById('today-now').addEventListener('click', () => { _day = _localToday(); _reload(); });
  document.getElementById('today-date-input').addEventListener('change', (e) => {
    if (e.target.value) { _day = e.target.value; _reload(); }
  });
  _escHandler = (e) => { if (e.key === 'Escape') { if (document.getElementById('today-detail')?.classList.contains('open')) _closeDetail(); else _close(); } };
  document.addEventListener('keydown', _escHandler);
  _reload();
}

window.openToday = openToday;
export default { openToday };
```

- [ ] **Step 4: Add the nav button in `static/index.html`**

After the `tool-areas-btn` block (~line 936), add (match the existing markup of its siblings — copy `tool-areas-btn`'s structure, swapping id/label/icon to a calendar-day SVG and the text `Today`):

```html
        <div class="list-item" id="tool-today-btn">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
          <span>Today</span>
        </div>
```

In the inline favicon/title script (search for the `'/areas'` case), add a `'/today'` case alongside it setting `document.title = 'Today · Odysseus'` (mirror the area entry's shape exactly).

- [ ] **Step 5: Wire `static/app.js`**

Near the areas import (~line 30): `import todayModule from './js/today.js';`

In the button-wiring block (mirror `tool-areas-btn`, ~line 945):

```javascript
  const toolTodayBtn = el('tool-today-btn');
  if (toolTodayBtn) toolTodayBtn.addEventListener('click', () => { if (todayModule) todayModule.openToday(); });
```

In the route map (~line 1038, next to `'/areas'`): `'/today': () => todayModule && todayModule.openToday(),`

- [ ] **Step 6: Wire `static/js/slashCommands.js`**

In the `_cmdOpen` target map (~line 1352, next to `areas`): `today: ['tool-today-btn'],`

In the command registry (next to the `areas:` command ~line 6010), add a `today` command mirroring it (`handler: (args, ctx) => _cmdToolPanel('today', args, ctx)`, `usage: '/today'`). If `_cmdToolPanel` requires a panel-id map entry, add `today` there too, mirroring `areas`.

- [ ] **Step 7: Append the `.today-*` styles to `static/style.css`**

```css
/* --- /today daily day-planner ------------------------------------------- */
.today-panel { position: fixed; inset: 0; z-index: 1000; background: var(--bg);
  display: flex; flex-direction: column; font-family: 'Fira Code', monospace; }
.today-header { display: flex; align-items: center; gap: 14px; padding: 14px 18px;
  border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.today-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: var(--fg); }
.today-nav { display: flex; align-items: center; gap: 6px; }
.today-now-btn, .today-view-btn { background: var(--card); color: var(--fg);
  border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; cursor: pointer;
  font-family: inherit; font-size: 12px; }
.today-view-btn.active { border-color: var(--red); color: var(--red); }
.today-date-input { background: var(--card); color: var(--fg); border: 1px solid var(--border);
  border-radius: 6px; padding: 3px 6px; font-family: inherit; }
.today-views { margin-left: auto; display: flex; gap: 6px; }
.today-body { flex: 1; overflow: auto; padding: 16px 18px; }
.today-section { margin-bottom: 18px; }
.today-section-header { display: flex; align-items: center; gap: 8px; font-weight: 600;
  color: var(--fg); margin-bottom: 8px; text-transform: uppercase; font-size: 12px; letter-spacing: .04em; }
.today-count { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 0 7px; font-size: 11px; color: var(--fg); }
.today-item { display: flex; align-items: center; gap: 10px; padding: 8px 10px; cursor: pointer;
  border: 1px solid var(--border); border-radius: 6px; background: var(--card); margin-bottom: 6px; }
.today-item:hover { border-color: var(--red); }
.today-item-title { color: var(--fg); flex: 1; }
.today-item-time { color: var(--fg); opacity: .8; min-width: 52px; }
.today-item-meta { color: var(--fg); opacity: .6; font-size: 11px; }
.today-overdue { border-left: 3px solid var(--red); }
.today-area-chip { font-size: 10px; border: 1px solid; border-radius: 8px; padding: 0 6px; opacity: .85; }
.today-empty { color: var(--fg); opacity: .5; font-size: 12px; padding: 6px 2px; }
.today-capacity { color: var(--fg); opacity: .7; font-size: 12px; margin-top: 6px; }
.today-timeline-wrap { display: flex; gap: 16px; align-items: flex-start; }
.today-grid { flex: 1; position: relative; }
.today-hours { position: relative; }
.today-hour { position: relative; border-top: 1px solid var(--border); }
.today-hour-label { position: absolute; left: 0; top: -8px; font-size: 10px; color: var(--fg); opacity: .5; }
.today-blocks { position: absolute; top: 0; left: 56px; right: 0; bottom: 0; }
.today-block { position: absolute; left: 0; right: 0; border-radius: 5px; padding: 3px 6px;
  font-size: 11px; overflow: hidden; cursor: pointer; border: 1px solid var(--border); }
.today-block-meeting { background: color-mix(in srgb, var(--red) 18%, var(--card)); }
.today-block-task { background: var(--card); }
.today-rail { width: 240px; flex-shrink: 0; }
.today-detail { position: fixed; top: 0; right: 0; width: 340px; height: 100%; background: var(--card);
  border-left: 1px solid var(--border); transform: translateX(100%); transition: transform .18s; z-index: 1001; }
.today-detail.open { transform: translateX(0); }
.today-detail-head { display: flex; justify-content: space-between; align-items: center;
  padding: 14px; border-bottom: 1px solid var(--border); color: var(--fg); font-weight: 600; }
.today-detail-body { padding: 14px; }
.today-detail-row { color: var(--fg); margin-bottom: 8px; font-size: 13px; }
.today-detail-notes { color: var(--fg); opacity: .8; font-size: 12px; white-space: pre-wrap; margin-top: 10px; }
.today-detail-actions { padding: 14px; }
.today-complete-btn { background: var(--red); color: #fff; border: none; border-radius: 6px;
  padding: 7px 14px; cursor: pointer; font-family: inherit; }
```

- [ ] **Step 8: Static checks**

Run:
```bash
node --check static/js/today.js
node --check static/app.js
node --check static/js/slashCommands.js
./venv/bin/python -m compileall -q app.py routes/today_routes.py src/today.py
```
Expected: no output (all pass).

- [ ] **Step 9: Restart backend + live verify**

Restart uvicorn on `:7860` (see HANDOFF setup commands), open `http://127.0.0.1:7860/today` in Chrome (admin pw `TPsoIvoUQJvrq1y0QwSEhCrE`). Verify: Overview shows real iCloud meetings for today + the Wiggert/Work tasks; Today's overdue bucket lists past-due open tasks; Timeline places a real meeting at its hour; clicking a meeting/task opens the detail panel; Complete on a task removes it; ◀ ▶ navigates days. Take a screenshot to `data/hub-today.png`.

- [ ] **Step 10: Commit**

```bash
git add app.py static/js/today.js static/index.html static/app.js static/js/slashCommands.js static/style.css
git commit -m "feat(hub): /today page wiring + read-only Overview/Timeline views"
```

---

## Task 6: (Phase 2) — covered by Task 4 endpoint

The `schedule` endpoint is already built and tested in Task 4. No additional backend work for Phase 2; proceed to the drag UI.

---

## Task 7: (Phase 2) Drag-to-timeblock in the Timeline view

**Files:**
- Modify: `static/js/today.js` (timeline drag handlers + a `_scheduleTask` call)
- Modify: `static/style.css` (drag-affordance styles)

**Interfaces:**
- Consumes: `POST /api/today/task/{id}/schedule`.
- Produces: dragging a rail item onto an hour sets `planned_start` (snapped to 15 min); dragging a block moves it; dropping a block onto the rail clears `planned_start`.

- [ ] **Step 1: Add the schedule fetch helper**

In `static/js/today.js`, near the other fetch helpers, add:

```javascript
async function _scheduleTask(id, day, start) {
  await fetch(`${API_BASE}/api/today/task/${encodeURIComponent(id)}/schedule`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ planned_day: day, planned_start: start }),
  });
}
function _snap15(min) { return Math.max(0, Math.round(min / 15) * 15); }
function _hhmm(min) { return `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`; }
```

- [ ] **Step 2: Make rail items + blocks draggable in `_renderTimeline`**

In `_taskRow`, when `cls` includes `today-rail-item`, add `draggable="true"`. In `place(...)`, add `draggable="true"` to task blocks (not meeting blocks). Then in `_render`, after wiring click handlers, add drag wiring (drop target is `.today-blocks`):

```javascript
  const blocksEl = document.querySelector('.today-blocks');
  if (blocksEl) {
    document.querySelectorAll('#today-body [data-task][draggable="true"]').forEach(elm => {
      elm.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/task', elm.getAttribute('data-task')));
    });
    blocksEl.addEventListener('dragover', (e) => e.preventDefault());
    blocksEl.addEventListener('drop', async (e) => {
      e.preventDefault();
      const id = e.dataTransfer.getData('text/task'); if (!id) return;
      const rect = blocksEl.getBoundingClientRect();
      const min = _snap15((e.clientY - rect.top) / PX_PER_MIN) + HOUR_START * 60;
      await _scheduleTask(id, _day, _hhmm(min)); _reload();
    });
    const railEl = document.querySelector('.today-rail');
    if (railEl) {
      railEl.addEventListener('dragover', (e) => e.preventDefault());
      railEl.addEventListener('drop', async (e) => {
        e.preventDefault();
        const id = e.dataTransfer.getData('text/task'); if (!id) return;
        await _scheduleTask(id, _day, null); _reload();   // back to unscheduled
      });
    }
  }
```

(Click and dragstart coexist; a drag won't fire the click.)

- [ ] **Step 3: Add drag-affordance styles to `static/style.css`**

```css
.today-rail-item, .today-block-task { cursor: grab; }
.today-blocks.drag-over { outline: 2px dashed var(--red); outline-offset: -2px; }
```

- [ ] **Step 4: Static check**

Run: `node --check static/js/today.js`
Expected: no output.

- [ ] **Step 5: Restart backend + live verify**

Restart uvicorn, reload `/today`, switch to Timeline. Drag an unscheduled task from the rail onto ~10:00 → it becomes a block at 10:00 and persists across a day-nav round-trip; drag a block back to the rail → it clears. Screenshot to `data/hub-today-timeblock.png`.

- [ ] **Step 6: Commit**

```bash
git add static/js/today.js static/style.css
git commit -m "feat(hub): drag-to-timeblock in the /today timeline"
```

---

## Final verification

- [ ] Full slice tests green: `./venv/bin/python -m pytest tests/test_today.py tests/test_today_routes.py -q`
- [ ] Existing hub tests still green: `./venv/bin/python -m pytest tests/test_links.py tests/test_people_routes.py tests/test_areas.py -q`
- [ ] `./venv/bin/python -m compileall -q app.py routes src` clean.
- [ ] `node --check` on every changed JS clean.
- [ ] Live Chrome verify done (both phases) with screenshots in `data/`.
- [ ] Update `docs/ai-context/HANDOFF.md` with the new slice (newest-first), and the `management-hub-build` memory's roadmap (Today surface shipped; remaining: Horizon, more node types, external sync).

---

## Self-Review notes (author)

- **Spec coverage**: two views (Overview Task 5 / Timeline Tasks 5+7) ✓; shared day-model (`day_view` Tasks 1–2) ✓; date-nav (Task 5) ✓; internal-only time-blocks (`planned_start`, no CalDAV — Tasks 1,4,7) ✓; detail panel read+complete (Task 5) ✓; new `/today` page (Task 5) ✓; recurring meetings via `_expand_rrule` (Task 2) ✓; areas via reverse-Link (Task 2) ✓; capacity (Task 1) ✓; overdue-only-today (Task 1) ✓; owner-scope + 404 (Tasks 1,3,4) ✓; tests mirroring area/people (Tasks 1–4) ✓. Deferred items (Horizon, resizable blocks, area filtering) correctly absent.
- **Type consistency**: `day_view` keys (`scheduled_tasks/unscheduled_tasks/overdue_tasks/meetings/capacity_minutes/is_today/day`) used identically in routes + JS. `ScheduleBody{planned_day, planned_start}` matches the JS `_scheduleTask` body. `planned_start` "HH:MM" everywhere. `_expand_rrule(ev, start_dt, end_dt)` signature matches calendar_routes.
- **No placeholders**: every code step is complete and runnable.
