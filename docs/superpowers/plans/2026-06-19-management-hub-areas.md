# Management Hub — Area/Context Backbone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class **Area** node (seeded Work/Personal/Krishna Movements) with single-primary membership via an `in_area` Link edge, plus an Area dashboard that aggregates People/Tasks/Notes/Meetings — turning the hub graph into a work+life+KM life-OS.

**Architecture:** Reuses the tracer-slice spine (`Link` table + Person-page aggregation pattern). An Area is a new owner-scoped node; a node's Area is a single `in_area` edge (`<node> → Area`), enforced by a `set_area` helper that deletes any prior edge before adding the new one. The Area dashboard is the Person-page reverse-`Link` aggregation keyed on the Area. No per-node schema changes.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), pytest (`asyncio_mode=auto`), vanilla JS in `static/`.

**Spec:** `docs/superpowers/specs/2026-06-19-management-hub-areas-design.md`
**Builds on:** the tracer slice (already in this branch's history): `src/links.py` (`add_link`/`links_from`/`links_to`/`remove_links_for` + `NODE_*`/`REL_*`), `routes/people_routes.py`, `src/meeting_notes.py`, `routes/meeting_notes_routes.py`.

## Global Constraints

- **Models** subclass `TimestampMixin, Base`; `id = Column(String, primary_key=True, index=True)`; `owner = Column(String, nullable=True, index=True)`; composite indexes in `__table_args__` with a unique non-colliding name. New tables auto-create via `create_all` — no migration.
- **Node-type / rel strings only via `src/links.py` constants** — never string literals at call sites. This slice adds `NODE_AREA="area"` and `REL_IN_AREA="in_area"`.
- **Single primary Area** — a node has at most one `in_area` edge. `set_area` enforces this (delete-then-add). `set_area(..., area_id=None|"")` clears.
- **Owner-scoping is a security boundary** — every query filters by `owner`; cross-owner/cross-area access 404s or returns empty. `Area` gets P0 isolation tests.
- **Routes are HTTP-thin** via `setup_<feature>_routes(...)` factories; `_owner(request) = require_user(request) or None`; `if user is not None and row.owner != user: raise HTTPException(404)`. List endpoints use `@router.get("")`.
- **Visual style (any `static/` change):** reuse CSS vars (`--card`,`--border`,`--fg`,`--red`,`--bg`) + existing button/input/card classes; **no Unicode emoji** — inline monochrome SVG only; Fira Code; dark default. Verify JS with `node --check` (skip vendored `static/lib/*`).
- **AI:** none in this slice. Area assignment is deterministic.
- **Commits:** Conventional Commits; end every commit message with these two trailers exactly:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01HVJRCpt1ARJmwonK13FTrx`

---

## File Structure

- **Modify** `core/database.py` — add `Area` model (after `Person`).
- **Modify** `src/links.py` — add `NODE_AREA`, `REL_IN_AREA`, `set_area`, `clear_area`.
- **Create** `src/areas.py` — `SEED_AREAS` + `ensure_seeded_areas(db, owner)`.
- **Create** `routes/area_routes.py` — `setup_area_routes()` (`/api/areas`): list(seeds)/create/get/update/delete + `/{id}/page` dashboard.
- **Modify** `app.py` — register the router; add `/areas` + `/areas/{area_id}` SPA routes.
- **Modify** `routes/people_routes.py` — `PersonCreate`/`PersonUpdate` accept `area_id`; create/update call `set_area`; person dicts include `area_id`.
- **Modify** `src/meeting_notes.py` + `routes/meeting_notes_routes.py` — `save_meeting_note`/`promote_action_item` accept `area_id` and stamp note + promoted tasks.
- **Create** `static/js/areas.js` (list + dashboard), `static/js/areaPicker.js` (reusable picker).
- **Modify** `static/js/people.js`, `static/js/meetingNote.js` (wire the picker), `static/index.html`, `static/app.js`, `static/js/slashCommands.js`, `static/style.css`.
- **Create tests** `tests/test_areas.py`; additions to `tests/test_people_routes.py`, `tests/test_meeting_notes.py`.

---

## Task 1: Area model + `set_area`/`clear_area` helpers

**Files:**
- Modify: `core/database.py` (add `Area` after the `Person` class)
- Modify: `src/links.py` (add constants + helpers)
- Test: `tests/test_areas.py`

**Interfaces:**
- Produces:
  - `core.database.Area` (cols: `id, owner, name, color, sort_order, archived` + timestamps)
  - `src.links.NODE_AREA = "area"`, `src.links.REL_IN_AREA = "in_area"`
  - `src.links.set_area(db, owner, node_type, node_id, area_id) -> Link | None` — deletes any existing `in_area` edge from the node, then adds `node → area_id`; if `area_id` is falsy, just clears and returns `None`
  - `src.links.clear_area(db, owner, node_type, node_id) -> int` — removes the node's `in_area` edge(s), returns count

- [ ] **Step 1: Write the failing test**

```python
# tests/test_areas.py
"""Area node + single-primary membership via the in_area Link edge."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, Area, Link
from src import links as L


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_set_area_creates_single_edge():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaWork")
    edges = L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA)
    assert [(e.to_type, e.to_id) for e in edges] == [(L.NODE_AREA, "areaWork")]


def test_set_area_replaces_previous():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaWork")
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaPersonal")
    edges = L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA)
    assert [e.to_id for e in edges] == ["areaPersonal"]   # exactly one, the new one


def test_set_area_none_clears():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaWork")
    assert L.set_area(db, "alice", L.NODE_PERSON, "p1", None) is None
    assert L.links_from(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_IN_AREA) == []


def test_clear_area_removes_edge():
    db = _db()
    L.set_area(db, "alice", L.NODE_TASK, "t1", "areaWork")
    assert L.clear_area(db, "alice", L.NODE_TASK, "t1") == 1
    assert L.links_from(db, "alice", L.NODE_TASK, "t1", rel=L.REL_IN_AREA) == []


def test_area_membership_is_owner_isolated():
    db = _db()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", "areaWork")
    assert L.links_to(db, "bob", L.NODE_AREA, "areaWork", rel=L.REL_IN_AREA) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_areas.py -v`
Expected: FAIL — `cannot import name 'Area'` / `module 'src.links' has no attribute 'set_area'`.

- [ ] **Step 3a: Add the `Area` model to `core/database.py`** (right after the `Person` class)

```python
class Area(TimestampMixin, Base):
    """A life-area / context (Work, Personal, Krishna Movements...) — a first-class
    node. A node belongs to at most ONE area via a single `in_area` Link edge; the
    Area dashboard aggregates its members via reverse-Link queries. Owner-scoped."""
    __tablename__ = "areas"

    id         = Column(String, primary_key=True, index=True)
    owner      = Column(String, nullable=True, index=True)
    name       = Column(String, nullable=False, default="")
    color      = Column(String, nullable=True)   # stored hex (reuses UI palette)
    sort_order = Column(Integer, default=0)
    archived   = Column(Boolean, default=False)

    __table_args__ = (Index('ix_areas_owner_archived', 'owner', 'archived'),)
```

- [ ] **Step 3b: Add constants + helpers to `src/links.py`** (constants beside the other `NODE_*`/`REL_*`; helpers at the end of the file)

```python
NODE_AREA = "area"
REL_IN_AREA = "in_area"          # <node>    -> Area  (single primary area)
```

```python
def set_area(db, owner: Optional[str], node_type: str, node_id: str,
             area_id: Optional[str]):
    """Set a node's single primary Area. Deletes any existing in_area edge from
    the node first (enforces 'exactly one'), then adds node -> area. A falsy
    area_id just clears and returns None."""
    db.query(Link).filter(
        Link.owner == owner, Link.from_type == node_type,
        Link.from_id == node_id, Link.rel == REL_IN_AREA,
    ).delete(synchronize_session=False)
    db.commit()
    if not area_id:
        return None
    return add_link(db, owner, node_type, node_id, REL_IN_AREA, NODE_AREA, area_id)


def clear_area(db, owner: Optional[str], node_type: str, node_id: str) -> int:
    """Remove a node's in_area edge(s). Returns the number removed."""
    n = db.query(Link).filter(
        Link.owner == owner, Link.from_type == node_type,
        Link.from_id == node_id, Link.rel == REL_IN_AREA,
    ).delete(synchronize_session=False)
    db.commit()
    return n
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_areas.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add core/database.py src/links.py tests/test_areas.py
git commit -m "feat(hub): add Area node + single-primary set_area/clear_area helpers"   # + trailers
```

---

## Task 2: Area seeding + CRUD routes

**Files:**
- Create: `src/areas.py`
- Create: `routes/area_routes.py`
- Modify: `app.py` (register router + SPA routes)
- Test: `tests/test_areas.py` (append CRUD + seeding tests)

**Interfaces:**
- Consumes: `core.database.Area`, `src.auth_helpers.require_user`, `src.links.remove_links_for`/`NODE_AREA`
- Produces:
  - `src.areas.SEED_AREAS` (list of `(name, color)`), `src.areas.ensure_seeded_areas(db, owner) -> None`
  - `routes.area_routes.setup_area_routes() -> APIRouter` (prefix `/api/areas`)
  - `routes.area_routes.AreaCreate(name:str="", color:Optional[str])`, `AreaUpdate(name?, color?, sort_order?, archived?)`
  - `routes.area_routes._area_to_dict(a) -> dict` → `{id, name, color, sort_order, archived}`
  - endpoints: `GET /api/areas` (seeds then lists), `POST`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}`

- [ ] **Step 1: Write the failing test** (append to `tests/test_areas.py`)

```python
from types import SimpleNamespace
from fastapi import HTTPException
import routes.area_routes as area_routes


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _request(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _ep(router, path, method):
    full = f"/api/areas{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_list_seeds_three_areas_once(monkeypatch):
    monkeypatch.setattr(area_routes, "SessionLocal", _sf())
    router = area_routes.setup_area_routes()
    out = _ep(router, "", "GET")(_request("alice"))
    names = [a["name"] for a in out["areas"]]
    assert names == ["Work", "Personal", "Krishna Movements"]
    # idempotent: a second call does not duplicate
    out2 = _ep(router, "", "GET")(_request("alice"))
    assert len(out2["areas"]) == 3


def test_area_crud_and_owner_isolation(monkeypatch):
    monkeypatch.setattr(area_routes, "SessionLocal", _sf())
    router = area_routes.setup_area_routes()
    a = _ep(router, "", "POST")(_request("alice"), area_routes.AreaCreate(name="Side project", color="#a60717"))
    assert a["name"] == "Side project"
    got = _ep(router, "/{area_id}", "GET")(_request("alice"), a["id"])
    assert got["color"] == "#a60717"
    # bob is isolated (bob's list seeds bob's OWN three; alice's area not visible/fetchable)
    assert a["id"] not in [x["id"] for x in _ep(router, "", "GET")(_request("bob"))["areas"]]
    with pytest.raises(HTTPException) as ei:
        _ep(router, "/{area_id}", "GET")(_request("bob"), a["id"])
    assert ei.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_areas.py -k "seed or crud" -v`
Expected: FAIL — `No module named 'routes.area_routes'`.

- [ ] **Step 3a: Create `src/areas.py`**

```python
"""Area seeding for the management hub. The owner's first visit to /api/areas
gets three default life-areas; all are editable nodes thereafter."""
import uuid
from typing import Optional

from core.database import Area

# (name, stored hex) — distinct colors from the UI palette.
SEED_AREAS = [
    ("Work", "#0066ff"),
    ("Personal", "#1dbf8c"),
    ("Krishna Movements", "#ff9100"),
]


def ensure_seeded_areas(db, owner: Optional[str]) -> None:
    """Create the three default areas for an owner that has none. Idempotent."""
    if db.query(Area).filter(Area.owner == owner).count() > 0:
        return
    for i, (name, color) in enumerate(SEED_AREAS):
        db.add(Area(id=str(uuid.uuid4()), owner=owner, name=name, color=color, sort_order=i))
    db.commit()
```

- [ ] **Step 3b: Create `routes/area_routes.py`**

```python
# routes/area_routes.py
"""Areas API — first-class life-area nodes for the management hub. HTTP-thin."""
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, Area
from src.auth_helpers import require_user
from src.areas import ensure_seeded_areas

logger = logging.getLogger(__name__)


class AreaCreate(BaseModel):
    name: str = ""
    color: Optional[str] = None


class AreaUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None
    archived: Optional[bool] = None


def _area_to_dict(a: Area) -> Dict[str, Any]:
    return {"id": a.id, "name": a.name, "color": a.color,
            "sort_order": a.sort_order, "archived": bool(a.archived)}


def setup_area_routes():
    router = APIRouter(prefix="/api/areas", tags=["areas"])

    def _owner(request: Request) -> Optional[str]:
        return require_user(request) or None

    def _load(db, request: Request, area_id: str) -> Area:
        owner = _owner(request)
        a = db.query(Area).filter(Area.id == area_id).first()
        if not a or (owner is not None and a.owner != owner):
            raise HTTPException(status_code=404, detail="area not found")
        return a

    @router.get("")
    def list_areas(request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            ensure_seeded_areas(db, owner)
            q = db.query(Area).filter(Area.archived == False)  # noqa: E712
            if owner is not None:
                q = q.filter(Area.owner == owner)
            rows = q.order_by(Area.sort_order, Area.name).all()
            return {"areas": [_area_to_dict(a) for a in rows]}
        finally:
            db.close()

    @router.post("")
    def create_area(request: Request, body: AreaCreate):
        owner = _owner(request)
        db = SessionLocal()
        try:
            a = Area(id=str(uuid.uuid4()), owner=owner,
                     name=(body.name or "").strip(), color=body.color)
            db.add(a)
            db.commit()
            return _area_to_dict(a)
        finally:
            db.close()

    @router.get("/{area_id}")
    def get_area(request: Request, area_id: str):
        db = SessionLocal()
        try:
            return _area_to_dict(_load(db, request, area_id))
        finally:
            db.close()

    @router.put("/{area_id}")
    def update_area(request: Request, area_id: str, body: AreaUpdate):
        db = SessionLocal()
        try:
            a = _load(db, request, area_id)
            for field in ("name", "color", "sort_order", "archived"):
                val = getattr(body, field)
                if val is not None:
                    setattr(a, field, val)
            db.commit()
            return _area_to_dict(a)
        finally:
            db.close()

    @router.delete("/{area_id}")
    def delete_area(request: Request, area_id: str):
        from src.links import remove_links_for, NODE_AREA
        db = SessionLocal()
        try:
            a = _load(db, request, area_id)
            remove_links_for(db, a.owner, NODE_AREA, a.id)
            db.delete(a)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    return router
```

- [ ] **Step 3c: Register in `app.py`** — next to the meeting-notes router registration (search for `setup_meeting_notes_routes`):

```python
from routes.area_routes import setup_area_routes
app.include_router(setup_area_routes())
```

And add SPA deep-link routes next to `serve_people`:

```python
@app.get("/areas")
async def serve_areas(request: Request):
    return await serve_index(request)

@app.get("/areas/{area_id}")
async def serve_area(request: Request, area_id: str):
    return await serve_index(request)
```

- [ ] **Step 4: Run tests + import check**

Run: `./venv/bin/python -m pytest tests/test_areas.py -v && python -m compileall -q routes/area_routes.py src/areas.py app.py`
Expected: PASS (all area tests) + no compile errors.

- [ ] **Step 5: Commit**

```bash
git add src/areas.py routes/area_routes.py app.py tests/test_areas.py
git commit -m "feat(hub): Area CRUD routes + Work/Personal/KM seeding + app wiring"   # + trailers
```

---

## Task 3: Area dashboard aggregation endpoint

**Files:**
- Modify: `routes/area_routes.py` (add `GET /api/areas/{area_id}/page` before `return router`)
- Test: `tests/test_areas.py` (append aggregation test)

**Interfaces:**
- Consumes: `src.links.links_to`/`NODE_AREA`/`NODE_PERSON`/`NODE_TASK`/`NODE_NOTE`/`NODE_MEETING`/`REL_IN_AREA`, `core.database` (`Person`,`PlanItem`,`Note`,`CalendarEvent`)
- Produces: `GET /api/areas/{area_id}/page` → `{area, people, open_tasks, notes, meetings}` where each list holds the nodes linked `in_area` to this area, partitioned by `from_type`; `open_tasks` = tasks with `status="open"`, overdue-first.

- [ ] **Step 1: Write the failing test** (append to `tests/test_areas.py`)

```python
def test_area_page_aggregates_members_partitioned(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    area = _ep(router, "", "POST")(_request("alice"), area_routes.AreaCreate(name="Work"))

    db = SF()
    from core.database import Person, PlanItem, Note, CalendarEvent, CalendarCal, utcnow_naive
    db.add(Person(id="p1", owner="alice", name="Wiggert"))
    db.add(PlanItem(id="t1", owner="alice", title="Ship", status="open"))
    db.add(PlanItem(id="t2", owner="alice", title="Done one", status="done"))
    db.add(Note(id="n1", owner="alice", title="1:1 note"))
    db.add(CalendarCal(id="c1", owner="alice", name="Cal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Weekly 1:1",
                         dtstart=utcnow_naive(), dtend=utcnow_naive()))
    db.commit()
    for nt, nid in [(L.NODE_PERSON, "p1"), (L.NODE_TASK, "t1"), (L.NODE_TASK, "t2"),
                    (L.NODE_NOTE, "n1"), (L.NODE_MEETING, "m1")]:
        L.set_area(db, "alice", nt, nid, area["id"])
    db.close()

    page = _ep(router, "/{area_id}/page", "GET")(_request("alice"), area["id"])
    assert page["area"]["name"] == "Work"
    assert [p["name"] for p in page["people"]] == ["Wiggert"]
    assert [t["title"] for t in page["open_tasks"]] == ["Ship"]    # 'done' excluded
    assert [n["title"] for n in page["notes"]] == ["1:1 note"]
    assert [m["uid"] for m in page["meetings"]] == ["m1"]


def test_area_page_excludes_other_areas(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(area_routes, "SessionLocal", SF)
    router = area_routes.setup_area_routes()
    work = _ep(router, "", "POST")(_request("alice"), area_routes.AreaCreate(name="Work"))
    personal = _ep(router, "", "POST")(_request("alice"), area_routes.AreaCreate(name="Personal"))
    db = SF()
    from core.database import Person
    db.add(Person(id="p1", owner="alice", name="OnlyPersonal"))
    db.commit()
    L.set_area(db, "alice", L.NODE_PERSON, "p1", personal["id"])
    db.close()
    page = _ep(router, "/{area_id}/page", "GET")(_request("alice"), work["id"])
    assert page["people"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_areas.py -k page -v`
Expected: FAIL — route `/api/areas/{area_id}/page` not found.

- [ ] **Step 3: Add the endpoint** to `routes/area_routes.py` (inside `setup_area_routes`, before `return router`)

```python
    @router.get("/{area_id}/page")
    def area_page(request: Request, area_id: str):
        from src.links import (links_to, NODE_AREA, NODE_PERSON, NODE_TASK,
                               NODE_NOTE, NODE_MEETING, REL_IN_AREA)
        from core.database import Person, PlanItem, Note, CalendarEvent
        db = SessionLocal()
        try:
            area = _load(db, request, area_id)
            owner = area.owner
            by_type: Dict[str, list] = {}
            for e in links_to(db, owner, NODE_AREA, area_id, rel=REL_IN_AREA):
                by_type.setdefault(e.from_type, []).append(e.from_id)

            pids = by_type.get(NODE_PERSON, [])
            people = db.query(Person).filter(Person.id.in_(pids)).all() if pids else []

            tids = by_type.get(NODE_TASK, [])
            tasks = (db.query(PlanItem)
                     .filter(PlanItem.id.in_(tids), PlanItem.status == "open").all()) if tids else []
            tasks.sort(key=lambda t: (t.due_date is None, t.due_date or ""))

            nids = by_type.get(NODE_NOTE, [])
            notes = db.query(Note).filter(Note.id.in_(nids)).all() if nids else []

            mids = by_type.get(NODE_MEETING, [])
            meetings = db.query(CalendarEvent).filter(CalendarEvent.uid.in_(mids)).all() if mids else []

            return {
                "area": _area_to_dict(area),
                "people": [{"id": p.id, "name": p.name, "role": p.role} for p in people],
                "open_tasks": [{"id": t.id, "title": t.title, "due_date": t.due_date,
                                "priority": t.priority} for t in tasks],
                "notes": [{"id": n.id, "title": n.title} for n in notes],
                "meetings": [{"uid": m.uid, "summary": m.summary,
                              "dtstart": m.dtstart.isoformat() if m.dtstart else None} for m in meetings],
            }
        finally:
            db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_areas.py -v`
Expected: PASS (all, incl. the 2 new).

- [ ] **Step 5: Commit**

```bash
git add routes/area_routes.py tests/test_areas.py
git commit -m "feat(hub): Area dashboard aggregation (people/tasks/notes/meetings in area)"   # + trailers
```

---

## Task 4: Person Area assignment

**Files:**
- Modify: `routes/people_routes.py` (`PersonCreate`/`PersonUpdate` + create/update + dicts)
- Test: `tests/test_people_routes.py` (append area tests)

**Interfaces:**
- Consumes: `src.links.set_area`/`links_from`/`NODE_PERSON`/`REL_IN_AREA`
- Produces: `PersonCreate`/`PersonUpdate` gain `area_id: Optional[str] = None`; `_person_to_dict(p, area_id=None)` includes `"area_id"`; create/update stamp the area via `set_area` and echo `area_id`; `get_person`/`list_people` include each person's current `area_id`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_people_routes.py`)

```python
def test_person_create_with_area_and_reassign(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"),
                                people_routes.PersonCreate(name="Wiggert", area_id="areaWork"))
    assert p["area_id"] == "areaWork"
    # get echoes the area
    assert _ep(router, "/{person_id}", "GET")(_request("alice"), p["id"])["area_id"] == "areaWork"
    # reassign via update -> exactly one area, the new one
    upd = _ep(router, "/{person_id}", "PUT")(_request("alice"), p["id"],
                                            people_routes.PersonUpdate(area_id="areaPersonal"))
    assert upd["area_id"] == "areaPersonal"
    import src.links as L
    db = SF()
    edges = L.links_from(db, "alice", L.NODE_PERSON, p["id"], rel=L.REL_IN_AREA)
    assert [e.to_id for e in edges] == ["areaPersonal"]
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_people_routes.py -k area -v`
Expected: FAIL — `PersonCreate` has no `area_id` / `area_id` not in dict.

- [ ] **Step 3: Modify `routes/people_routes.py`**

Add `area_id` to the Pydantic models:

```python
class PersonCreate(BaseModel):
    name: str = ""
    email: Optional[str] = None
    role: Optional[str] = None
    contact_uid: Optional[str] = None
    area_id: Optional[str] = None


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    contact_uid: Optional[str] = None
    archived: Optional[bool] = None
    area_id: Optional[str] = None
```

Change `_person_to_dict` to accept the area:

```python
def _person_to_dict(p: Person, area_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "email": p.email, "role": p.role,
        "contact_uid": p.contact_uid, "archived": bool(p.archived),
        "area_id": area_id,
    }
```

Add a small helper inside `setup_people_routes` (next to `_load`) to look up a person's area, and a batch helper for the list:

```python
    def _area_of(db, owner, person_id):
        from src.links import links_from, NODE_PERSON, REL_IN_AREA
        edges = links_from(db, owner, NODE_PERSON, person_id, rel=REL_IN_AREA)
        return edges[0].to_id if edges else None

    def _area_map(db, owner, person_ids):
        from src.links import Link, NODE_PERSON, NODE_AREA, REL_IN_AREA
        if not person_ids:
            return {}
        rows = (db.query(Link)
                .filter(Link.owner == owner, Link.from_type == NODE_PERSON,
                        Link.from_id.in_(person_ids), Link.rel == REL_IN_AREA).all())
        return {r.from_id: r.to_id for r in rows}
```

In `list_people`, build the area map and pass it:

```python
            rows = q.order_by(Person.name).all()
            amap = _area_map(db, owner, [p.id for p in rows])
            return {"people": [_person_to_dict(p, amap.get(p.id)) for p in rows]}
```

In `create_person`, after `db.commit()` and before returning:

```python
            from src.links import set_area, NODE_PERSON
            if body.area_id is not None:
                set_area(db, owner, NODE_PERSON, p.id, body.area_id or None)
            return _person_to_dict(p, body.area_id or None)
```

In `get_person`:

```python
            p = _load(db, request, person_id)
            return _person_to_dict(p, _area_of(db, p.owner, p.id))
```

In `update_person`, after the field loop + `db.commit()`:

```python
            from src.links import set_area, NODE_PERSON
            if body.area_id is not None:
                set_area(db, p.owner, NODE_PERSON, p.id, body.area_id or None)
            return _person_to_dict(p, _area_of(db, p.owner, p.id))
```

(Note: `set_area` already commits.) Keep the existing `person_page` aggregation untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_people_routes.py -v`
Expected: PASS (all existing + the new area test).

- [ ] **Step 5: Commit**

```bash
git add routes/people_routes.py tests/test_people_routes.py
git commit -m "feat(hub): assign a Person's Area on create/update"   # + trailers
```

---

## Task 5: Meeting-note Area stamping

**Files:**
- Modify: `src/meeting_notes.py` (`save_meeting_note` + `promote_action_item` accept `area_id`)
- Modify: `routes/meeting_notes_routes.py` (`MeetingNoteSave` + `PromoteBody` pass `area_id`)
- Test: `tests/test_meeting_notes.py` (append area test)

**Interfaces:**
- Consumes: `src.links.set_area`/`NODE_NOTE`/`NODE_TASK`
- Produces: `save_meeting_note(..., area_id: Optional[str] = None)` stamps the note and every promoted task with the area; `promote_action_item(..., area_id: Optional[str] = None)` stamps the created task; `MeetingNoteSave`/`PromoteBody` gain `area_id`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_meeting_notes.py`)

```python
def test_save_with_area_stamps_note_and_tasks():
    db = _db()
    out = MN.save_meeting_note(
        db, "alice", title="1:1", content="",
        action_items=[{"text": "Send deck", "done": False}],
        person_id="p1", event_uid="m1", make_tasks=True, area_id="areaWork")
    note_id = out["note"]["id"]
    assert [e.to_id for e in L.links_from(db, "alice", L.NODE_NOTE, note_id, rel=L.REL_IN_AREA)] == ["areaWork"]
    task = db.query(PlanItem).filter(PlanItem.title == "Send deck").one()
    assert [e.to_id for e in L.links_from(db, "alice", L.NODE_TASK, task.id, rel=L.REL_IN_AREA)] == ["areaWork"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_meeting_notes.py -k area -v`
Expected: FAIL — `save_meeting_note() got an unexpected keyword argument 'area_id'`.

- [ ] **Step 3: Modify `src/meeting_notes.py`**

Change the `promote_action_item` signature and add the stamp after the task's links are written:

```python
def promote_action_item(db, owner: Optional[str], note_id: str, title: str,
                        *, person_id: Optional[str] = None,
                        due_date: Optional[str] = None,
                        area_id: Optional[str] = None) -> Dict[str, Any]:
```

After the existing `if person_id: L.add_link(... REL_ABOUT ...)` line (just before `return _task_dict(item)`):

```python
    if area_id:
        L.set_area(db, owner, L.NODE_TASK, item.id, area_id)
    return _task_dict(item)
```

Change the `save_meeting_note` signature to add `area_id`:

```python
def save_meeting_note(db, owner: Optional[str], *, title: str = "", content: str = "",
                      action_items: Optional[List[Dict[str, Any]]] = None,
                      person_id: Optional[str] = None, event_uid: Optional[str] = None,
                      make_tasks: bool = False, note_id: Optional[str] = None,
                      area_id: Optional[str] = None) -> Dict[str, Any]:
```

After the note's `note_of`/`about`/`attended_by` edges are written (just before the `tasks = []` line), stamp the note:

```python
    if area_id:
        L.set_area(db, owner, L.NODE_NOTE, note.id, area_id)
```

And pass `area_id` through to promotion in the make_tasks loop:

```python
                tasks.append(promote_action_item(db, owner, note.id, it["text"],
                                                 person_id=person_id, area_id=area_id))
```

- [ ] **Step 4: Modify `routes/meeting_notes_routes.py`** — add `area_id` to both bodies and pass it through:

```python
class MeetingNoteSave(BaseModel):
    title: str = ""
    content: str = ""
    action_items: List[Dict[str, Any]] = []
    person_id: Optional[str] = None
    event_uid: Optional[str] = None
    make_tasks: bool = False
    note_id: Optional[str] = None
    area_id: Optional[str] = None


class PromoteBody(BaseModel):
    title: str = ""
    person_id: Optional[str] = None
    due_date: Optional[str] = None
    area_id: Optional[str] = None
```

In `save(...)`, add `area_id=body.area_id` to the `MN.save_meeting_note(...)` call. In `promote(...)`, add `area_id=body.area_id` to the `MN.promote_action_item(...)` call.

- [ ] **Step 5: Run tests + import check, then commit**

Run: `./venv/bin/python -m pytest tests/test_meeting_notes.py -v && python -m compileall -q src/meeting_notes.py routes/meeting_notes_routes.py`
Expected: PASS (all meeting-note tests) + clean compile.

```bash
git add src/meeting_notes.py routes/meeting_notes_routes.py tests/test_meeting_notes.py
git commit -m "feat(hub): stamp meeting-note + promoted tasks with an Area"   # + trailers
```

---

## Task 6: Frontend — Areas list + dashboard

**Files:**
- Create: `static/js/areas.js`
- Modify: `static/index.html` (nav `tool-areas-btn`, load `areas.js` + `areaPicker.js`, favicon/title block), `static/app.js` (button + routing), `static/js/slashCommands.js` (`/areas`), `static/style.css` (`.area-*` block)

**Interfaces:**
- Consumes: `GET /api/areas`, `POST /api/areas`, `GET /api/areas/{id}/page`
- Produces: `window.openAreas()` (list panel) and `window.openArea(areaId)` (dashboard). `openArea` is the deep-link target for `/areas/{id}`.

- [ ] **Step 1: Create `static/js/areas.js`** — mirror `static/js/people.js` (read it first for the panel mount, `_esc`, Escape `e.preventDefault()`, fetch+render). Implement:
  - `openAreas()` — `GET /api/areas`; render the areas as cards (color dot + name); each opens `openArea(id)`. Inline "+ Area" form (name + a color choice from the existing palette) → `POST /api/areas` → refresh.
  - `openArea(id)` — `GET /api/areas/${id}/page`; header (color dot + name) + four collapsible sections rendering the payload: **People** (`people[]` name + role), **Open tasks** (`open_tasks[]` title + due, already overdue-first), **Notes** (`notes[]` title), **Meetings** (`meetings[]` summary + date). This render is the People-page layout keyed on the area — reuse its row/section markup and `_esc`.
  - Color dot = a small inline `<span>` with `style="background:<color>"` (the stored hex is data, not a CSS literal in the stylesheet). All untrusted strings via `_esc`. No emoji; inline monochrome SVG for section/row glyphs.

- [ ] **Step 2: Wire nav + route + slash + styles**
  - `static/index.html`: add nav button `id="tool-areas-btn"` (label "Areas", a layers/grid monochrome SVG) beside `tool-people-btn`; add `<script src="/static/js/areas.js"></script>` and `<script src="/static/js/areaPicker.js"></script>` near the other module scripts; add `/areas` to the favicon/title SHAPES block like `/people`.
  - `static/app.js`: wire `tool-areas-btn` → `openAreas()` (mirror the `tool-people-btn` wiring); in the path router add `/areas` → `openArea(id)` when an id is present in `window.location.pathname` else `openAreas()` (mirror the `/people` branch).
  - `static/js/slashCommands.js`: add a `/areas` command → `openAreas` (mirror the `/people` entry; only include a `rail-areas` fallback id if such an element exists, else just the primary).
  - `static/style.css`: add a `.area-*` block (`.area-card`, `.area-dot`, `.area-section`) reusing existing vars/classes. No new color literals (the dot color comes from inline `style`).

- [ ] **Step 3: Syntax check**

Run: `node --check static/js/areas.js && node --check static/app.js && node --check static/js/slashCommands.js`
Expected: no output.

- [ ] **Step 4: Live verification** (controller will do this; restart backend first so new routes load) — open `http://127.0.0.1:7860/areas` → three seeded areas render; click "Work" → dashboard with four (empty) sections. Defer the populated check to Task 7's full-loop verification.

- [ ] **Step 5: Commit**

```bash
git add static/js/areas.js static/index.html static/app.js static/js/slashCommands.js static/style.css
git commit -m "feat(hub): Areas list + Area dashboard UI"   # + trailers
```

---

## Task 7: Frontend — Area picker + wire into Person form & meeting-note composer

**Files:**
- Create: `static/js/areaPicker.js`
- Modify: `static/js/people.js` (picker in the person create/edit form), `static/js/meetingNote.js` (picker in the composer), `static/style.css` (`.area-picker` styles if needed)

**Interfaces:**
- Consumes: `GET /api/areas`; the `area_id` params added to `POST/PUT /api/people` (Task 4) and `POST /api/meeting-notes` + `/{id}/promote` (Task 5).
- Produces: `window.AreaPicker` — a small factory `AreaPicker.mount(containerEl, { selectedId })` that fetches `/api/areas`, renders a labelled `<select>` (color-dotted options + an "Unassigned" empty option), and exposes `.value` (the selected area id or `null`). Reused by both surfaces.

- [ ] **Step 1: Create `static/js/areaPicker.js`** — a self-contained reusable control:
  - `AreaPicker.mount(containerEl, opts = {})` → fetches `GET /api/areas`, injects a `<label>Area</label><select class="area-picker-select">` with an empty "— Unassigned —" option plus one option per area (text = name; a leading color dot via an inline-styled `<span>` is optional in a `<select>`, so prefix the option label with the area name only and rely on the dashboard for color). Pre-select `opts.selectedId`. Return an object `{ el, get value() { return select.value || null } }`. All names via `_esc` (copy the helper).
  - Keep it dependency-free and idempotent (safe to mount once per open).

- [ ] **Step 2: Wire into the Person form (`static/js/people.js`)**
  - In `openPeople()`'s create form: after the existing name/role/email inputs, add a container and `const picker = AreaPicker.mount(container, {})`; on "+ Person" submit include `area_id: picker.value` in the POST body.
  - In `openPerson(id)`: show the current area as a chip in the header (from the page payload — note `/api/people/{id}/page` does not currently return area; use `GET /api/people/{id}` which now returns `area_id`, or add an inline AreaPicker pre-set to the person's `area_id` with a "Save" that `PUT`s `{area_id}`). Minimal: render an `AreaPicker` pre-selected to the person's `area_id`; on change, `PUT /api/people/{id}` with `{area_id: picker.value}` and refresh.

- [ ] **Step 3: Wire into the meeting-note composer (`static/js/meetingNote.js`)**
  - Add an `AreaPicker` to the composer modal (near the Person/Meeting pickers), pre-selected from `opts.areaId` if provided (and, when a person is chosen, optionally default the picker to that person's area — fetch `GET /api/people/{personId}` and set the picker; keep this best-effort).
  - Include `area_id: areaPicker.value` in the `POST /api/meeting-notes` body, and in the AI-candidate `POST /{noteId}/promote` body (so promoted candidates inherit the note's area).

- [ ] **Step 4: Syntax check**

Run: `node --check static/js/areaPicker.js && node --check static/js/people.js && node --check static/js/meetingNote.js`
Expected: no output.

- [ ] **Step 5: Live verification (the full loop)** — controller-run, backend restarted:
  1. `/people` → edit Wiggert → set Area = **Work** → save.
  2. New meeting note for Wiggert → Area picker = **Work** → add an action item + prose → **Save + make tasks**; promote an AI candidate.
  3. `/areas` → open **Work** → People shows Wiggert, Open tasks show the promoted tasks, Notes shows the note, Meetings shows the linked meeting.
  4. Open **Personal** → none of them appear.
  Screenshot the populated Work dashboard.

- [ ] **Step 6: Commit**

```bash
git add static/js/areaPicker.js static/js/people.js static/js/meetingNote.js static/style.css
git commit -m "feat(hub): Area picker wired into Person form + meeting-note composer"   # + trailers
```

---

## Final verification

- [ ] Slice suite: `./venv/bin/python -m pytest tests/test_areas.py tests/test_people_routes.py tests/test_meeting_notes.py tests/test_links.py -v` — all green.
- [ ] `python -m compileall -q app.py core routes src` — no syntax errors.
- [ ] `node --check` on every changed JS file.
- [ ] The live loop (Task 7 Step 5) verified end-to-end with a screenshot of the populated Work dashboard.
- [ ] Owner-isolation P0s present and passing for `Area` (CRUD + dashboard).

## What this unlocks next (out of scope — see spec §7)

Per-Area filtering in the Planner/Today surface; Company/Team nodes; Projects-under-Areas (PlanProject gains an `in_area` edge, surfaces on the dashboard); Goals (period-scoped, per Area); Sunsama-style channel routing keyed on Area.
