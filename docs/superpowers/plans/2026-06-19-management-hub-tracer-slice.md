# Management Hub — Tracer Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the minimal connective spine of the management hub — a polymorphic `Link` table and a first-class `Person` node — and prove it with one end-to-end loop: meeting → note → linked action-item tasks → resurface on the Person page.

**Architecture:** One join table (`Link`: `from_type,from_id,rel,to_type,to_id`) *is* the graph; backlinks are reverse queries on it. `Person` becomes a real owner-scoped node. A meeting-note composer writes Note + edges and promotes action items into `PlanItem` tasks that carry the same edges (plus the existing `source_*` soft fields as a denormalized cache). The Person page is three reverse-`Link` queries. One degradable background AI call suggests extra action items; Python owns links and dates.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), pytest (`asyncio_mode=auto`), vanilla JS in `static/`. Local Ollama "utility" lane for AI via `src/endpoint_resolver.resolve_endpoint("utility")`.

**Spec:** `docs/superpowers/specs/2026-06-19-management-hub-tracer-slice-design.md`

## Global Constraints

- **Paths/config via `src/constants.py`** — never hardcode writable paths or `http://localhost:7000`; use `internal_api_base()` for loopback. (Not expected to be needed in this slice, but holds.)
- **Owner-scoping is a security boundary.** Every query on `Link`, `Person`, `Note`, `PlanItem` filters by `owner`. New owner-scoped surfaces (`Link`, `Person`) get P0 isolation tests.
- **Models:** subclass `TimestampMixin, Base`; `id = Column(String, primary_key=True, index=True)`; `owner = Column(String, nullable=True, index=True)`; composite indexes go in `__table_args__` with a unique non-colliding name (an `owner=...index=True` column already auto-creates `ix_<table>_owner`).
- **New tables auto-create** via `Base.metadata.create_all` at startup — no migration script.
- **Routes are HTTP-thin:** a `setup_<feature>_routes(...)` factory returns an `APIRouter`; business logic lives in `src/*`. `_owner(request) = require_user(request) or None`, then `if user is not None and row.owner != user: raise HTTPException(404)`.
- **AI golden rule:** the LLM does *language*; Python does *math/dates/placement*. Every AI call is one-shot, output passed through a `coerce_*` validator, failure degrades to the raw item, never blocks the response (FastAPI `BackgroundTasks`).
- **Visual style (any `static/` change):** reuse CSS vars (`--red`,`--fg`,`--bg`,`--card`,`--border`) and existing button/input/card classes; **no Unicode emoji** — inline monochrome SVG only; Fira Code; dark default. Verify JS with `node --check`.
- **Tests** mirror `tests/test_planner_*.py`: in-memory SQLite (`StaticPool`), `monkeypatch.setattr(<module>, "SessionLocal", SF)`, `_request(user) = SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))`, look up endpoints off `router.routes`, call them directly.
- **Commits:** Conventional Commits. End commit messages with the two trailers (`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and the `Claude-Session:` line).
- **Node type / rel constants** (defined once in `src/links.py`, imported everywhere — never string literals at call sites):
  `NODE_NOTE="note"`, `NODE_MEETING="meeting"`, `NODE_PERSON="person"`, `NODE_TASK="task"`; `REL_NOTE_OF="note_of"`, `REL_ABOUT="about"`, `REL_FROM_NOTE="from_note"`, `REL_ATTENDED_BY="attended_by"`.

---

## File Structure

- **Create** `src/links.py` — `Link` helpers (`add_link`/`links_from`/`links_to`/`remove_links_for`) + node/rel constants. The one place link reads/writes live.
- **Create** `routes/people_routes.py` — Person CRUD + `/api/people/{id}/page` aggregation.
- **Create** `src/meeting_notes.py` — `save_meeting_note` (Note + edges + promote) and `promote_action_item` + the background AI enrichment.
- **Create** `routes/meeting_notes_routes.py` — thin HTTP wrapper: meeting picker, save, get (poll), promote.
- **Create** `static/js/people.js` — people list + person detail panel.
- **Create** `static/js/meetingNote.js` — meeting-note composer modal + promotion/AI chips.
- **Modify** `core/database.py` — add `Link` + `Person` models (+ ensure `UniqueConstraint` import).
- **Modify** `src/planner_ai.py` — add `extract_action_items` + `coerce_action_items`.
- **Modify** `app.py` — register the two routers; add `/people`, `/people/{person_id}` SPA routes.
- **Modify** `static/index.html` — `tool-people-btn` nav + favicon/title block; load the two new JS modules.
- **Modify** `static/app.js` — wire the nav button + `/people` route.
- **Modify** `static/js/slashCommands.js` — `/people` and `/meeting-note` commands.
- **Modify** `static/style.css` — `.person-*` and `.mnote-*` blocks.
- **Create tests** `tests/test_links.py`, `tests/test_people_routes.py`, `tests/test_meeting_notes.py`, `tests/test_extract_action_items.py`.

---

## Task 1: Link model + `src/links.py` helpers

**Files:**
- Modify: `core/database.py` (add `Link` model near `PlanItem`; ensure `UniqueConstraint` is imported)
- Create: `src/links.py`
- Test: `tests/test_links.py`

**Interfaces:**
- Produces:
  - `core.database.Link` (cols: `id, owner, from_type, from_id, rel, to_type, to_id` + timestamps)
  - `src.links.add_link(db, owner, from_type, from_id, rel, to_type, to_id) -> Link` — idempotent (returns existing edge if present)
  - `src.links.links_from(db, owner, from_type, from_id, rel=None) -> list[Link]`
  - `src.links.links_to(db, owner, to_type, to_id, rel=None) -> list[Link]`
  - `src.links.remove_links_for(db, owner, node_type, node_id) -> int` — deletes every edge touching a node (either end)
  - constants `NODE_NOTE/NODE_MEETING/NODE_PERSON/NODE_TASK`, `REL_NOTE_OF/REL_ABOUT/REL_FROM_NOTE/REL_ATTENDED_BY`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_links.py
"""The Link join table IS the graph: idempotent edges, reverse-query backlinks,
and strict owner isolation (a new owner-scoped security boundary)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, Link
from src import links as L


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_add_link_is_idempotent():
    db = _db()
    a = L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    b = L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert a.id == b.id
    assert db.query(Link).count() == 1


def test_links_from_and_to_are_reverse_queries():
    db = _db()
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    L.add_link(db, "alice", L.NODE_TASK, "t2", L.REL_ABOUT, L.NODE_PERSON, "p1")
    # forward: edges out of t1
    assert {e.to_id for e in L.links_from(db, "alice", L.NODE_TASK, "t1")} == {"p1"}
    # reverse: everything pointing AT person p1
    assert {e.from_id for e in L.links_to(db, "alice", L.NODE_PERSON, "p1", rel=L.REL_ABOUT)} == {"t1", "t2"}


def test_links_are_owner_isolated():
    db = _db()
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_ABOUT, L.NODE_PERSON, "p1")
    assert L.links_to(db, "bob", L.NODE_PERSON, "p1") == []


def test_remove_links_for_clears_both_directions():
    db = _db()
    L.add_link(db, "alice", L.NODE_NOTE, "n1", L.REL_NOTE_OF, L.NODE_MEETING, "m1")
    L.add_link(db, "alice", L.NODE_TASK, "t1", L.REL_FROM_NOTE, L.NODE_NOTE, "n1")
    removed = L.remove_links_for(db, "alice", L.NODE_NOTE, "n1")
    assert removed == 2
    assert db.query(Link).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_links.py -v`
Expected: FAIL — `ImportError: cannot import name 'Link'` / `No module named 'src.links'`.

- [ ] **Step 3a: Add the `Link` model to `core/database.py`**

First confirm the imports at the top of `core/database.py` include `UniqueConstraint` (add it to the existing `from sqlalchemy import (...)` line if missing — `Index` is already imported). Then add, right after the `PlanItem` class:

```python
class Link(TimestampMixin, Base):
    """A typed edge between two nodes — the whole graph spine.

    `(from_type, from_id, rel, to_type, to_id)` is a directed, typed edge.
    Backlinks are just the reverse query (`links_to`). Polymorphic by design so
    every node type (note/meeting/person/task/email/doc/...) links the same way
    with no new schema. Owner-scoped: a new isolation boundary.
    """
    __tablename__ = "links"

    id        = Column(String, primary_key=True, index=True)
    owner     = Column(String, nullable=True, index=True)
    from_type = Column(String, nullable=False)
    from_id   = Column(String, nullable=False)
    rel       = Column(String, nullable=False)
    to_type   = Column(String, nullable=False)
    to_id     = Column(String, nullable=False)

    __table_args__ = (
        Index('ix_links_from', 'owner', 'from_type', 'from_id'),
        Index('ix_links_to', 'owner', 'to_type', 'to_id'),
        UniqueConstraint('owner', 'from_type', 'from_id', 'rel', 'to_type', 'to_id',
                         name='uq_links_edge'),
    )
```

- [ ] **Step 3b: Create `src/links.py`**

```python
"""Helpers over the Link join table — the management-hub graph spine.

Every link read/write goes through here so node-type/rel strings live in ONE
place and owner-scoping is never forgotten. The table is polymorphic: nodes are
referenced by (type, id) pairs, not FKs, so features stay decoupled.
"""
import uuid
from typing import List, Optional

from core.database import Link

NODE_NOTE = "note"
NODE_MEETING = "meeting"
NODE_PERSON = "person"
NODE_TASK = "task"

REL_NOTE_OF = "note_of"          # Note      -> Meeting
REL_ABOUT = "about"             # Note/Task -> Person
REL_FROM_NOTE = "from_note"      # Task      -> Note
REL_ATTENDED_BY = "attended_by"  # Meeting   -> Person


def add_link(db, owner: Optional[str], from_type: str, from_id: str,
             rel: str, to_type: str, to_id: str) -> Link:
    """Create the edge if it doesn't exist; return the existing one if it does."""
    existing = (
        db.query(Link)
        .filter(Link.owner == owner, Link.from_type == from_type, Link.from_id == from_id,
                Link.rel == rel, Link.to_type == to_type, Link.to_id == to_id)
        .first()
    )
    if existing:
        return existing
    edge = Link(id=str(uuid.uuid4()), owner=owner, from_type=from_type, from_id=from_id,
                rel=rel, to_type=to_type, to_id=to_id)
    db.add(edge)
    db.commit()
    return edge


def links_from(db, owner: Optional[str], from_type: str, from_id: str,
               rel: Optional[str] = None) -> List[Link]:
    q = db.query(Link).filter(Link.owner == owner, Link.from_type == from_type, Link.from_id == from_id)
    if rel is not None:
        q = q.filter(Link.rel == rel)
    return q.all()


def links_to(db, owner: Optional[str], to_type: str, to_id: str,
             rel: Optional[str] = None) -> List[Link]:
    q = db.query(Link).filter(Link.owner == owner, Link.to_type == to_type, Link.to_id == to_id)
    if rel is not None:
        q = q.filter(Link.rel == rel)
    return q.all()


def remove_links_for(db, owner: Optional[str], node_type: str, node_id: str) -> int:
    """Delete every edge touching a node at either end (used when a node is deleted)."""
    n = (
        db.query(Link)
        .filter(Link.owner == owner)
        .filter(((Link.from_type == node_type) & (Link.from_id == node_id))
                | ((Link.to_type == node_type) & (Link.to_id == node_id)))
        .delete(synchronize_session=False)
    )
    db.commit()
    return n
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_links.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add core/database.py src/links.py tests/test_links.py
git commit -m "feat(hub): add Link graph spine + helpers"   # + trailers
```

---

## Task 2: Person model + CRUD routes

**Files:**
- Modify: `core/database.py` (add `Person` model after `Link`)
- Create: `routes/people_routes.py`
- Test: `tests/test_people_routes.py`

**Interfaces:**
- Consumes: `core.database.SessionLocal`, `src.auth_helpers.require_user`
- Produces:
  - `core.database.Person` (cols `id, owner, name, contact_uid, email, role, archived` + timestamps)
  - `routes.people_routes.setup_people_routes() -> APIRouter` (prefix `/api/people`)
  - `routes.people_routes.PersonCreate(name:str="", email:Optional[str], role:Optional[str], contact_uid:Optional[str])`
  - `routes.people_routes.PersonUpdate(name?, email?, role?, contact_uid?, archived?)`
  - `routes.people_routes._person_to_dict(p) -> dict`
  - endpoints: `GET /api/people`, `POST /api/people`, `GET /api/people/{id}`, `PUT /api/people/{id}`, `DELETE /api/people/{id}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_people_routes.py
"""Person is a first-class owner-scoped node: CRUD + strict owner isolation."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace
from fastapi import HTTPException

from core.database import Base
import routes.people_routes as people_routes


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _request(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _ep(router, path, method):
    full = f"/api/people{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_create_and_get_person(monkeypatch):
    monkeypatch.setattr(people_routes, "SessionLocal", _sf())
    router = people_routes.setup_people_routes()
    create = _ep(router, "", "POST")
    out = create(_request("alice"), people_routes.PersonCreate(name="Wiggert", role="Direct report"))
    assert out["name"] == "Wiggert"
    got = _ep(router, "/{person_id}", "GET")(_request("alice"), out["id"])
    assert got["role"] == "Direct report"


def test_people_are_owner_isolated(monkeypatch):
    monkeypatch.setattr(people_routes, "SessionLocal", _sf())
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="Wiggert"))
    # bob cannot see or fetch alice's person
    assert _ep(router, "", "GET")(_request("bob"))["people"] == []
    with pytest.raises(HTTPException) as ei:
        _ep(router, "/{person_id}", "GET")(_request("bob"), p["id"])
    assert ei.value.status_code == 404


def test_update_person(monkeypatch):
    monkeypatch.setattr(people_routes, "SessionLocal", _sf())
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="Wig"))
    upd = _ep(router, "/{person_id}", "PUT")(_request("alice"), p["id"],
                                             people_routes.PersonUpdate(email="w@x.io"))
    assert upd["email"] == "w@x.io"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_people_routes.py -v`
Expected: FAIL — `No module named 'routes.people_routes'`.

- [ ] **Step 3a: Add the `Person` model to `core/database.py`** (after `Link`)

```python
class Person(TimestampMixin, Base):
    """A person the user tracks (report, contact). First-class node in the hub
    graph — meetings/notes/tasks link to it via the Link table. Single-user:
    people do NOT log in; this is info the owner keeps ABOUT them."""
    __tablename__ = "people"

    id          = Column(String, primary_key=True, index=True)
    owner       = Column(String, nullable=True, index=True)
    name        = Column(String, nullable=False, default="")
    contact_uid = Column(String, nullable=True)   # optional iCloud CardDAV contact ref
    email       = Column(String, nullable=True)
    role        = Column(String, nullable=True)
    archived    = Column(Boolean, default=False)

    __table_args__ = (Index('ix_people_owner_archived', 'owner', 'archived'),)
```

- [ ] **Step 3b: Create `routes/people_routes.py`**

```python
# routes/people_routes.py
"""People API — first-class Person nodes in the management hub.

HTTP-thin; owner-scoping lives here, link/aggregation logic in src/. People are
single-user entities the owner tracks (no logins)."""
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, Person
from src.auth_helpers import require_user

logger = logging.getLogger(__name__)


class PersonCreate(BaseModel):
    name: str = ""
    email: Optional[str] = None
    role: Optional[str] = None
    contact_uid: Optional[str] = None


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    contact_uid: Optional[str] = None
    archived: Optional[bool] = None


def _person_to_dict(p: Person) -> Dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "email": p.email, "role": p.role,
        "contact_uid": p.contact_uid, "archived": bool(p.archived),
    }


def setup_people_routes():
    router = APIRouter(prefix="/api/people", tags=["people"])

    def _owner(request: Request) -> Optional[str]:
        return require_user(request) or None

    def _load(db, request: Request, person_id: str) -> Person:
        owner = _owner(request)
        p = db.query(Person).filter(Person.id == person_id).first()
        if not p or (owner is not None and p.owner != owner):
            raise HTTPException(status_code=404, detail="person not found")
        return p

    @router.get("")
    def list_people(request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            q = db.query(Person).filter(Person.archived == False)  # noqa: E712
            if owner is not None:
                q = q.filter(Person.owner == owner)
            rows = q.order_by(Person.name).all()
            return {"people": [_person_to_dict(p) for p in rows]}
        finally:
            db.close()

    @router.post("")
    def create_person(request: Request, body: PersonCreate):
        owner = _owner(request)
        db = SessionLocal()
        try:
            p = Person(id=str(uuid.uuid4()), owner=owner, name=(body.name or "").strip(),
                       email=body.email, role=body.role, contact_uid=body.contact_uid)
            db.add(p)
            db.commit()
            return _person_to_dict(p)
        finally:
            db.close()

    @router.get("/{person_id}")
    def get_person(request: Request, person_id: str):
        db = SessionLocal()
        try:
            return _person_to_dict(_load(db, request, person_id))
        finally:
            db.close()

    @router.put("/{person_id}")
    def update_person(request: Request, person_id: str, body: PersonUpdate):
        db = SessionLocal()
        try:
            p = _load(db, request, person_id)
            for field in ("name", "email", "role", "contact_uid", "archived"):
                val = getattr(body, field)
                if val is not None:
                    setattr(p, field, val)
            db.commit()
            return _person_to_dict(p)
        finally:
            db.close()

    @router.delete("/{person_id}")
    def delete_person(request: Request, person_id: str):
        from src.links import remove_links_for, NODE_PERSON
        db = SessionLocal()
        try:
            p = _load(db, request, person_id)
            remove_links_for(db, p.owner, NODE_PERSON, p.id)
            db.delete(p)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_people_routes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/database.py routes/people_routes.py tests/test_people_routes.py
git commit -m "feat(hub): add first-class Person node + CRUD routes"   # + trailers
```

---

## Task 3: Meeting-note save + action-item promotion (`src/meeting_notes.py`)

**Files:**
- Create: `src/meeting_notes.py`
- Test: `tests/test_meeting_notes.py`

**Interfaces:**
- Consumes: `core.database` (`Note`, `PlanItem`), `src.links` helpers + constants
- Produces:
  - `src.meeting_notes.save_meeting_note(db, owner, *, title, content, action_items, person_id, event_uid, make_tasks, note_id=None) -> dict` — returns `{"note": <note dict>, "tasks": [<task dict>...]}`. Creates (or updates if `note_id`) the Note, writes `note_of`/`about`/`attended_by` edges, and (if `make_tasks`) promotes each unchecked action item.
  - `src.meeting_notes.promote_action_item(db, owner, note_id, title, *, person_id=None, due_date=None) -> dict` — idempotent on `(note, title)`; creates a `PlanItem` with `from_note`+`about` edges and the `source_*` soft-field cache; returns the task dict.
  - `src.meeting_notes._task_dict(item) -> dict`, `src.meeting_notes._note_dict(note) -> dict`
- `action_items` shape: `list[{"text": str, "done": bool}]` (matches `Note.items` JSON).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_meeting_notes.py
"""Meeting-note save wires the graph edges and promotes action items into linked
tasks (with the soft-field cache). Promotion is idempotent."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, PlanItem, Note
from src import links as L
from src import meeting_notes as MN


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_save_writes_note_meeting_and_person_edges():
    db = _db()
    out = MN.save_meeting_note(
        db, "alice", title="1:1 Wiggert", content="Discussed Q3.",
        action_items=[{"text": "Send deck", "done": False}],
        person_id="p1", event_uid="m1", make_tasks=False)
    note_id = out["note"]["id"]
    # Note -> Meeting and Note -> Person edges exist
    assert {(e.rel, e.to_id) for e in L.links_from(db, "alice", L.NODE_NOTE, note_id)} == {
        (L.REL_NOTE_OF, "m1"), (L.REL_ABOUT, "p1")}
    # Meeting -> Person (so the person page shows the meeting)
    assert {e.to_id for e in L.links_from(db, "alice", L.NODE_MEETING, "m1", rel=L.REL_ATTENDED_BY)} == {"p1"}
    assert out["tasks"] == []  # make_tasks=False


def test_make_tasks_promotes_unchecked_items_with_links_and_softfields():
    db = _db()
    out = MN.save_meeting_note(
        db, "alice", title="1:1", content="",
        action_items=[{"text": "Send deck", "done": False},
                      {"text": "already done", "done": True}],
        person_id="p1", event_uid="m1", make_tasks=True)
    assert len(out["tasks"]) == 1                      # only the unchecked one
    task = db.query(PlanItem).filter(PlanItem.title == "Send deck").one()
    assert task.owner == "alice"
    assert task.source_note_id == out["note"]["id"]    # soft-field cache
    assert task.source_event_id == "m1"
    assert task.person_id == "p1"
    # graph edges Task -> Note and Task -> Person
    assert {(e.rel, e.to_id) for e in L.links_from(db, "alice", L.NODE_TASK, task.id)} == {
        (L.REL_FROM_NOTE, out["note"]["id"]), (L.REL_ABOUT, "p1")}


def test_promotion_is_idempotent():
    db = _db()
    out = MN.save_meeting_note(db, "alice", title="1:1", content="",
                               action_items=[{"text": "Send deck", "done": False}],
                               person_id="p1", event_uid="m1", make_tasks=True)
    note_id = out["note"]["id"]
    # promote the same item again -> no duplicate task
    MN.promote_action_item(db, "alice", note_id, "Send deck", person_id="p1")
    assert db.query(PlanItem).filter(PlanItem.title == "Send deck").count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_meeting_notes.py -v`
Expected: FAIL — `No module named 'src.meeting_notes'`.

- [ ] **Step 3: Create `src/meeting_notes.py`**

```python
"""Meeting-note save + action-item promotion — the hub's connective glue.

A meeting note is a Note linked to a Meeting (CalendarEvent.uid) and a Person.
Its action items can be promoted into PlanItem tasks that carry the same graph
edges PLUS the existing source_* soft fields (a denormalized cache, so existing
planner code keeps working). The Link table is canonical; the soft fields mirror
it for the single most common back-reference.
"""
import json
import uuid
from typing import Any, Dict, List, Optional

from core.database import Note, PlanItem
from src import links as L


def _note_dict(note: Note) -> Dict[str, Any]:
    try:
        items = json.loads(note.items) if note.items else []
    except (ValueError, TypeError):
        items = []
    suggested = []
    try:
        cls = json.loads(note.ai_classification) if note.ai_classification else {}
        suggested = cls.get("suggested_action_items", []) if isinstance(cls, dict) else []
    except (ValueError, TypeError):
        pass
    return {
        "id": note.id, "title": note.title, "content": note.content,
        "items": items, "suggested_action_items": suggested,
        "ai_enriched": note.ai_content_hash is not None,
    }


def _task_dict(item: PlanItem) -> Dict[str, Any]:
    return {
        "id": item.id, "title": item.title, "status": item.status,
        "due_date": item.due_date, "priority": item.priority,
        "source_note_id": item.source_note_id, "source_event_id": item.source_event_id,
        "person_id": item.person_id,
    }


def _max_ordinal(db, owner: Optional[str]) -> int:
    from sqlalchemy import func
    val = db.query(func.max(PlanItem.ordinal)).filter(PlanItem.owner == owner).scalar()
    return (val or 0)


def promote_action_item(db, owner: Optional[str], note_id: str, title: str,
                        *, person_id: Optional[str] = None,
                        due_date: Optional[str] = None) -> Dict[str, Any]:
    """Create a PlanItem from an action item, idempotent on (note, title)."""
    title = (title or "").strip()
    # idempotency: a task already promoted from this note with this title?
    existing_ids = {e.from_id for e in L.links_to(db, owner, L.NODE_NOTE, note_id, rel=L.REL_FROM_NOTE)}
    if existing_ids:
        dup = (db.query(PlanItem)
               .filter(PlanItem.id.in_(existing_ids), PlanItem.title == title).first())
        if dup:
            return _task_dict(dup)

    # look up the originating event uid off the note's note_of edge (for the soft cache)
    event_uid = None
    note_edges = L.links_from(db, owner, L.NODE_NOTE, note_id, rel=L.REL_NOTE_OF)
    if note_edges:
        event_uid = note_edges[0].to_id

    item = PlanItem(id=str(uuid.uuid4()), owner=owner, title=title, status="open",
                    due_date=due_date, ordinal=_max_ordinal(db, owner) + 1024,
                    source="meeting_note", source_note_id=note_id,
                    source_event_id=event_uid, person_id=person_id)
    db.add(item)
    db.commit()
    L.add_link(db, owner, L.NODE_TASK, item.id, L.REL_FROM_NOTE, L.NODE_NOTE, note_id)
    if person_id:
        L.add_link(db, owner, L.NODE_TASK, item.id, L.REL_ABOUT, L.NODE_PERSON, person_id)
    return _task_dict(item)


def save_meeting_note(db, owner: Optional[str], *, title: str = "", content: str = "",
                      action_items: Optional[List[Dict[str, Any]]] = None,
                      person_id: Optional[str] = None, event_uid: Optional[str] = None,
                      make_tasks: bool = False, note_id: Optional[str] = None) -> Dict[str, Any]:
    action_items = action_items or []
    if note_id:
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note or (owner is not None and note.owner != owner):
            raise ValueError("note not found")
        note.title, note.content = title, content
        note.items = json.dumps(action_items)
    else:
        note = Note(id=str(uuid.uuid4()), owner=owner, title=title, content=content,
                    items=json.dumps(action_items), note_type="note", source="user")
        db.add(note)
    db.commit()

    if event_uid:
        L.add_link(db, owner, L.NODE_NOTE, note.id, L.REL_NOTE_OF, L.NODE_MEETING, event_uid)
    if person_id:
        L.add_link(db, owner, L.NODE_NOTE, note.id, L.REL_ABOUT, L.NODE_PERSON, person_id)
    if event_uid and person_id:
        L.add_link(db, owner, L.NODE_MEETING, event_uid, L.REL_ATTENDED_BY, L.NODE_PERSON, person_id)

    tasks = []
    if make_tasks:
        for it in action_items:
            if not it.get("done") and (it.get("text") or "").strip():
                tasks.append(promote_action_item(db, owner, note.id, it["text"], person_id=person_id))
    return {"note": _note_dict(note), "tasks": tasks}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_meeting_notes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/meeting_notes.py tests/test_meeting_notes.py
git commit -m "feat(hub): meeting-note save writes edges + promotes linked tasks"   # + trailers
```

---

## Task 4: Person-page aggregation endpoint

**Files:**
- Modify: `routes/people_routes.py` (add `GET /api/people/{person_id}/page`)
- Test: `tests/test_people_routes.py` (add aggregation + resurfacing tests)

**Interfaces:**
- Consumes: `src.links.links_to`, `core.database` (`PlanItem`, `Note`, `CalendarEvent`)
- Produces: `GET /api/people/{person_id}/page` → `{"person": {...}, "open_tasks": [...], "meetings": [...], "notes": [...]}` where `open_tasks` = tasks linked `about` this person with `status="open"`, overdue-first.

- [ ] **Step 1: Write the failing test** (append to `tests/test_people_routes.py`)

```python
def test_person_page_aggregates_open_tasks_meetings_notes(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    import src.meeting_notes as MN
    router = people_routes.setup_people_routes()
    p = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="Wiggert"))

    db = SF()
    # seed a calendar event the note will attach to
    from core.database import CalendarCal, CalendarEvent, utcnow_naive
    db.add(CalendarCal(id="c1", owner="alice", name="Personal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Weekly 1:1",
                         dtstart=utcnow_naive(), dtend=utcnow_naive()))
    db.commit()
    MN.save_meeting_note(db, "alice", title="1:1", content="notes",
                         action_items=[{"text": "Send deck", "done": False}],
                         person_id=p["id"], event_uid="m1", make_tasks=True)
    db.close()

    page = _ep(router, "/{person_id}/page", "GET")(_request("alice"), p["id"])
    assert page["person"]["id"] == p["id"]
    assert [t["title"] for t in page["open_tasks"]] == ["Send deck"]
    assert [m["uid"] for m in page["meetings"]] == ["m1"]
    assert len(page["notes"]) == 1


def test_person_page_excludes_other_persons_and_owners(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(people_routes, "SessionLocal", SF)
    import src.meeting_notes as MN
    router = people_routes.setup_people_routes()
    p1 = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="W"))
    p2 = _ep(router, "", "POST")(_request("alice"), people_routes.PersonCreate(name="X"))
    db = SF()
    MN.save_meeting_note(db, "alice", title="n", content="", person_id=p2["id"],
                         action_items=[{"text": "X task", "done": False}],
                         event_uid=None, make_tasks=True)
    db.close()
    page = _ep(router, "/{person_id}/page", "GET")(_request("alice"), p1["id"])
    assert page["open_tasks"] == []   # p1 has none; p2's task must not leak
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_people_routes.py -k page -v`
Expected: FAIL — route `/api/people/{person_id}/page` not found.

- [ ] **Step 3: Add the aggregation endpoint** to `routes/people_routes.py` (inside `setup_people_routes`, before `return router`)

```python
    @router.get("/{person_id}/page")
    def person_page(request: Request, person_id: str):
        from src.links import links_to, NODE_PERSON, NODE_TASK, NODE_NOTE, NODE_MEETING, REL_ABOUT, REL_NOTE_OF, REL_ATTENDED_BY
        from core.database import PlanItem, Note, CalendarEvent
        db = SessionLocal()
        try:
            person = _load(db, request, person_id)
            owner = person.owner

            task_ids = [e.from_id for e in links_to(db, owner, NODE_PERSON, person_id, rel=REL_ABOUT)
                        if e.from_type == NODE_TASK]
            tasks = (db.query(PlanItem)
                     .filter(PlanItem.id.in_(task_ids), PlanItem.status == "open").all()) if task_ids else []
            # overdue-first: items with a due_date sort before those without, ascending
            tasks.sort(key=lambda t: (t.due_date is None, t.due_date or ""))

            note_ids = [e.from_id for e in links_to(db, owner, NODE_PERSON, person_id, rel=REL_ABOUT)
                        if e.from_type == NODE_NOTE]
            notes = db.query(Note).filter(Note.id.in_(note_ids)).all() if note_ids else []

            mtg_ids = [e.from_id for e in links_to(db, owner, NODE_PERSON, person_id, rel=REL_ATTENDED_BY)
                       if e.from_type == NODE_MEETING]
            meetings = db.query(CalendarEvent).filter(CalendarEvent.uid.in_(mtg_ids)).all() if mtg_ids else []

            return {
                "person": _person_to_dict(person),
                "open_tasks": [{"id": t.id, "title": t.title, "due_date": t.due_date,
                                "priority": t.priority, "source_note_id": t.source_note_id} for t in tasks],
                "meetings": [{"uid": m.uid, "summary": m.summary,
                              "dtstart": m.dtstart.isoformat() if m.dtstart else None} for m in meetings],
                "notes": [{"id": n.id, "title": n.title} for n in notes],
            }
        finally:
            db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_people_routes.py -v`
Expected: PASS (all, incl. the 2 new).

- [ ] **Step 5: Commit**

```bash
git add routes/people_routes.py tests/test_people_routes.py
git commit -m "feat(hub): person-page aggregation (open tasks/meetings/notes resurface)"   # + trailers
```

---

## Task 5: AI action-item extraction (`src/planner_ai.py`)

**Files:**
- Modify: `src/planner_ai.py` (add `extract_action_items` + `coerce_action_items`)
- Test: `tests/test_extract_action_items.py`

**Interfaces:**
- Consumes: `src.endpoint_resolver.resolve_endpoint`, `src.llm_core.llm_call_async`, `src.planner_ai._extract_json`
- Produces:
  - `src.planner_ai.coerce_action_items(raw, person_name, today, items_already) -> list[dict]` — each `{"title": str, "owner": "me"|"<person_name>"|None, "due_date": "YYYY-MM-DD"|None}`; clamps unknown owner→None, resolves a `due_hint` phrase to a date via `dateutil` + server tz, drops empties and titles already present in `items_already`.
  - `src.planner_ai.extract_action_items(content, person_name, owner=None) -> list[dict]` — one-shot model call returning the raw list (caller coerces). Raises on transport/config failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extract_action_items.py
"""AI extracts candidate action items; Python clamps owner + resolves dates and
degrades safely. The model is never trusted for correctness."""
from src import planner_ai


def test_coerce_clamps_owner_and_resolves_date():
    raw = [
        {"title": "Send Q3 deck", "owner": "Wiggert", "due_hint": "2026-06-23"},
        {"title": "Ping HR", "owner": "someone_else", "due_hint": None},
        {"title": "", "owner": "me", "due_hint": None},   # empty -> dropped
    ]
    out = planner_ai.coerce_action_items(raw, person_name="Wiggert", today="2026-06-19", items_already=[])
    assert out == [
        {"title": "Send Q3 deck", "owner": "Wiggert", "due_date": "2026-06-23"},
        {"title": "Ping HR", "owner": None, "due_date": None},
    ]


def test_coerce_drops_titles_already_present():
    raw = [{"title": "Send deck", "owner": "me", "due_hint": None}]
    out = planner_ai.coerce_action_items(raw, "Wiggert", "2026-06-19", items_already=["send deck"])
    assert out == []


def test_coerce_handles_non_list():
    assert planner_ai.coerce_action_items("garbage", "W", "2026-06-19", []) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_extract_action_items.py -v`
Expected: FAIL — `module 'src.planner_ai' has no attribute 'coerce_action_items'`.

- [ ] **Step 3: Add to `src/planner_ai.py`**

```python
def coerce_action_items(raw, person_name, today, items_already):
    """Clamp/resolve raw model action items into safe candidates.

    owner is clamped to {"me", person_name} else None; due_hint -> YYYY-MM-DD via
    dateutil (relative to `today`); empties and titles already in items_already
    are dropped. Never trusts the model for correctness."""
    from dateutil import parser as _dtparser
    from datetime import datetime

    if not isinstance(raw, list):
        return []
    seen = {str(t).strip().lower() for t in (items_already or [])}
    base = datetime.strptime(today, "%Y-%m-%d")
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())

        owner = entry.get("owner")
        if owner not in ("me", person_name):
            owner = None

        due_date = None
        hint = entry.get("due_hint")
        if isinstance(hint, str) and hint.strip():
            try:
                due_date = _dtparser.parse(hint, default=base, fuzzy=True).strftime("%Y-%m-%d")
            except (ValueError, OverflowError):
                due_date = None

        out.append({"title": title, "owner": owner, "due_date": due_date})
    return out


async def extract_action_items(content, person_name, owner=None):
    """One-shot extraction of action items from meeting-note prose. Returns the
    raw list (caller coerces). Raises on transport/config failure so the caller
    can degrade to the user's hand-typed items."""
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async

    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        raise RuntimeError("no model endpoint configured for action-item extraction")

    today = datetime.now().strftime("%Y-%m-%d")
    system = (
        "You extract concrete action items from meeting notes. Today is "
        f"{today}. The meeting is with a person named \"{person_name}\". Respond "
        "with ONLY a JSON array; each element has keys:\n"
        '  "title": short imperative action (string),\n'
        f'  "owner": "me" or "{person_name}" or null (who will do it),\n'
        '  "due_hint": a date phrase verbatim from the notes (e.g. "by Friday") or null.\n'
        "Do NOT invent items not implied by the notes. Return [] if none."
    )
    messages = [
        {"role": "system", "content": system + " /no_think"},
        {"role": "user", "content": content or ""},
    ]
    resp = await llm_call_async(url, model, messages, temperature=0.0, max_tokens=400,
                                headers=headers, prompt_type="meeting_action_items")
    return _extract_json_list(resp)
```

Also add a list-aware JSON extractor next to `_extract_json` in `src/planner_ai.py`:

```python
def _extract_json_list(text):
    """Pull the first JSON array out of a model response (tolerates fences/prose)."""
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        bracket = re.search(r"\[.*\]", text, re.DOTALL)
        candidate = bracket.group(0) if bracket else None
    if not candidate:
        return []
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, list) else []
    except json.JSONDecodeError:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_extract_action_items.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/planner_ai.py tests/test_extract_action_items.py
git commit -m "feat(hub): AI action-item extraction (coerced owner + dates)"   # + trailers
```

---

## Task 6: Meeting-notes HTTP routes + background enrichment + app wiring

**Files:**
- Create: `routes/meeting_notes_routes.py`
- Modify: `src/meeting_notes.py` (add `enrich_meeting_note` background fn)
- Modify: `app.py` (register `setup_people_routes` + `setup_meeting_notes_routes`; add `/people`, `/people/{person_id}` SPA routes)
- Test: `tests/test_meeting_notes.py` (add enrichment test)

**Interfaces:**
- Consumes: `src.meeting_notes.save_meeting_note`/`promote_action_item`, `src.planner_ai.extract_action_items`/`coerce_action_items`
- Produces:
  - `src.meeting_notes.enrich_meeting_note(note_id, owner) -> None` — background: extract + coerce, write candidates into `Note.ai_classification` JSON (`{"suggested_action_items":[...]}`) and stamp `ai_content_hash`.
  - `routes.meeting_notes_routes.setup_meeting_notes_routes() -> APIRouter` (prefix `/api/meeting-notes`): `GET /meetings?q=`, `POST ""`, `GET /{note_id}`, `POST /{note_id}/promote`.
  - `MeetingNoteSave` body: `title, content, action_items, person_id, event_uid, make_tasks, note_id`.
  - `PromoteBody`: `title, person_id, due_date`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_meeting_notes.py`)

```python
async def test_enrich_writes_suggested_items(monkeypatch):
    db = _db()
    out = MN.save_meeting_note(db, "alice", title="1:1", content="Wiggert to send the deck by Friday.",
                               action_items=[], person_id="p1", event_uid=None, make_tasks=False)
    note_id = out["note"]["id"]

    import src.planner_ai as pai
    async def fake_extract(content, person_name, owner=None):
        return [{"title": "Send the deck", "owner": person_name, "due_hint": "2026-06-26"}]
    monkeypatch.setattr(pai, "extract_action_items", fake_extract)
    # use the SAME engine/session for the background fn
    import src.meeting_notes as MNmod
    monkeypatch.setattr(MNmod, "SessionLocal", lambda: db, raising=False)

    await MN.enrich_meeting_note(note_id, "alice")
    refreshed = MN._note_dict(db.query(Note).filter(Note.id == note_id).one())
    assert refreshed["ai_enriched"] is True
    assert [s["title"] for s in refreshed["suggested_action_items"]] == ["Send the deck"]
```

Note: `enrich_meeting_note` opens its own session via a module-level `SessionLocal` (added in Step 3) so the route can fire it as a background task; the test monkeypatches it to reuse the in-memory `db`.

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_meeting_notes.py -k enrich -v`
Expected: FAIL — `module 'src.meeting_notes' has no attribute 'enrich_meeting_note'`.

- [ ] **Step 3a: Add to `src/meeting_notes.py`** — a module-level import and the background fn:

At the top of `src/meeting_notes.py`, add to the imports:

```python
import hashlib
import logging
from core.database import SessionLocal

logger = logging.getLogger(__name__)
```

Then add:

```python
async def enrich_meeting_note(note_id: str, owner: Optional[str]) -> None:
    """Background: extract candidate action items via AI, store them on the Note
    for the UI to confirm. Best-effort; always stamps ai_content_hash so the
    client poll terminates."""
    from src import planner_ai
    from datetime import datetime
    db = SessionLocal()
    try:
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note or (owner is not None and note.owner != owner):
            return
        person_name = ""
        for e in L.links_from(db, owner, L.NODE_NOTE, note_id, rel=L.REL_ABOUT):
            from core.database import Person
            p = db.query(Person).filter(Person.id == e.to_id).first()
            if p:
                person_name = p.name
                break
        try:
            existing = [it.get("text", "") for it in (json.loads(note.items) if note.items else [])]
            raw = await planner_ai.extract_action_items(note.content or "", person_name, owner=owner)
            suggested = planner_ai.coerce_action_items(
                raw, person_name, datetime.now().strftime("%Y-%m-%d"), existing)
            note.ai_classification = json.dumps({"suggested_action_items": suggested})
        except Exception:
            logger.exception("meeting-note enrichment failed; keeping note as-is")
        note.ai_content_hash = hashlib.sha256((note.content or "").encode("utf-8")).hexdigest()
        db.commit()
    finally:
        db.close()
```

- [ ] **Step 3b: Create `routes/meeting_notes_routes.py`**

```python
# routes/meeting_notes_routes.py
"""Meeting-notes API — the hub composer surface. HTTP-thin over src/meeting_notes."""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, CalendarCal, CalendarEvent
from src.auth_helpers import require_user
from src import meeting_notes as MN

logger = logging.getLogger(__name__)


class MeetingNoteSave(BaseModel):
    title: str = ""
    content: str = ""
    action_items: List[Dict[str, Any]] = []
    person_id: Optional[str] = None
    event_uid: Optional[str] = None
    make_tasks: bool = False
    note_id: Optional[str] = None


class PromoteBody(BaseModel):
    title: str = ""
    person_id: Optional[str] = None
    due_date: Optional[str] = None


def setup_meeting_notes_routes():
    router = APIRouter(prefix="/api/meeting-notes", tags=["meeting-notes"])

    def _owner(request: Request) -> Optional[str]:
        return require_user(request) or None

    @router.get("/meetings")
    def list_meetings(request: Request, q: str = ""):
        """Owner-scoped recent calendar events for the meeting picker."""
        owner = _owner(request)
        db = SessionLocal()
        try:
            cal_ids = [c.id for c in db.query(CalendarCal).filter(
                (CalendarCal.owner == owner) if owner is not None else True).all()]
            query = db.query(CalendarEvent).filter(CalendarEvent.calendar_id.in_(cal_ids))
            if q:
                query = query.filter(CalendarEvent.summary.ilike(f"%{q}%"))
            rows = query.order_by(CalendarEvent.dtstart.desc()).limit(30).all()
            return {"meetings": [{"uid": e.uid, "summary": e.summary,
                                  "dtstart": e.dtstart.isoformat() if e.dtstart else None} for e in rows]}
        finally:
            db.close()

    @router.post("")
    def save(request: Request, body: MeetingNoteSave, background: BackgroundTasks):
        owner = _owner(request)
        db = SessionLocal()
        try:
            result = MN.save_meeting_note(
                db, owner, title=body.title, content=body.content,
                action_items=body.action_items, person_id=body.person_id,
                event_uid=body.event_uid, make_tasks=body.make_tasks, note_id=body.note_id)
        finally:
            db.close()
        background.add_task(MN.enrich_meeting_note, result["note"]["id"], owner)
        return result

    @router.get("/{note_id}")
    def get_note(request: Request, note_id: str):
        from core.database import Note
        owner = _owner(request)
        db = SessionLocal()
        try:
            note = db.query(Note).filter(Note.id == note_id).first()
            if not note or (owner is not None and note.owner != owner):
                raise HTTPException(status_code=404, detail="note not found")
            return MN._note_dict(note)
        finally:
            db.close()

    @router.post("/{note_id}/promote")
    def promote(request: Request, note_id: str, body: PromoteBody):
        owner = _owner(request)
        db = SessionLocal()
        try:
            return MN.promote_action_item(db, owner, note_id, body.title,
                                          person_id=body.person_id, due_date=body.due_date)
        finally:
            db.close()

    return router
```

- [ ] **Step 3c: Register in `app.py`** — next to the planner registration (after line ~765).

NOTE: the people router (`setup_people_routes()`) is ALREADY registered in `app.py` (added in Task 2). Do NOT add it again. Only add the meeting-notes router:

```python
from routes.meeting_notes_routes import setup_meeting_notes_routes
app.include_router(setup_meeting_notes_routes())
```

And add SPA deep-link routes next to `serve_planner`:

```python
@app.get("/people")
async def serve_people(request: Request):
    return await serve_index(request)

@app.get("/people/{person_id}")
async def serve_person(request: Request, person_id: str):
    return await serve_index(request)
```

- [ ] **Step 4: Run tests + import check**

Run: `./venv/bin/python -m pytest tests/test_meeting_notes.py -v && python -m compileall -q routes/meeting_notes_routes.py routes/people_routes.py src/meeting_notes.py app.py`
Expected: PASS (all meeting-note tests) + no compile errors.

- [ ] **Step 5: Commit**

```bash
git add src/meeting_notes.py routes/meeting_notes_routes.py app.py tests/test_meeting_notes.py
git commit -m "feat(hub): meeting-notes routes + background AI enrichment + app wiring"   # + trailers
```

---

## Task 7: Frontend — People page (`static/js/people.js`)

**Files:**
- Create: `static/js/people.js`
- Modify: `static/index.html` (nav button `tool-people-btn`, load `people.js` + `meetingNote.js`, favicon/title block)
- Modify: `static/app.js` (button wiring + `/people` route open)
- Modify: `static/js/slashCommands.js` (`/people` command)
- Modify: `static/style.css` (`.person-*` block)

**Interfaces:**
- Consumes: `GET /api/people`, `GET /api/people/{id}/page`, `POST /api/people`
- Produces: `window.openPeople()` (list panel) and `window.openPerson(personId)` (detail panel). `openPerson` is the deep-link target for `/people/{id}` and for backlinks from tasks/notes.

- [ ] **Step 1: Create `static/js/people.js`** — follow the injected-panel pattern in `static/js/planner.js` (read it first for the exact panel/escape helpers used: `_esc`, panel mount, close handling). Implement:
  - `openPeople()` — fetch `/api/people`, render a card list of people (name + role); each row opens `openPerson(id)`; a "+ Person" inline form (name/role/email) POSTs to `/api/people`.
  - `openPerson(id)` — fetch `/api/people/${id}/page`, render header (name/role/email) + three collapsible sections: **Open items** (title + "from <note>" + due, overdue-first already sorted server-side), **Meetings** (summary + date), **Notes** (title). A "+ Meeting note" button calls `window.openMeetingNote({personId:id, personName:name})` (defined in Task 8).
  - All untrusted strings through an `_esc` helper (copy the one in `planner.js`). Inline monochrome SVG for the section/task glyphs — **no emoji**. Reuse `.card`, button, and input classes; colors via `--fg`/`--border`/`--red`.

- [ ] **Step 2: Wire nav + route + slash command + styles**
  - `static/index.html`: add a nav button `id="tool-people-btn"` beside `tool-planner-btn` (copy that button's markup + swap the inline SVG to a two-person glyph and the label to "People"); add `<script src="/static/js/people.js"></script>` and `<script src="/static/js/meetingNote.js"></script>` near the other module scripts; add a `/people` entry to the favicon/title inline `SHAPES`/title switch (match how `/planner` is handled).
  - `static/app.js`: import/wire `tool-people-btn` → `openPeople()`; in the path-router that opens modals by `window.location.pathname`, add `('/people' or /^\/people\//)` → `openPerson(id)` when an id is present else `openPeople()` (mirror the `/planner` branch).
  - `static/js/slashCommands.js`: add a `/people` command and a `_cmdOpen` target entry → `openPeople` (mirror the `/planner` entry).
  - `static/style.css`: add a `.person-*` block (header, `.person-section`, `.person-task-row`) reusing existing vars/classes. No new color literals — use `--card`,`--border`,`--fg`,`--red`.

- [ ] **Step 3: Syntax check**

Run: `node --check static/js/people.js && node --check static/app.js && node --check static/js/slashCommands.js`
Expected: no output (all valid).

- [ ] **Step 4: Live verification** (backend must be running on :7860; restart it so new routes/JS load)

1. Open `http://127.0.0.1:7860/people` → People panel renders, empty state OK.
2. Add a person "Wiggert" → appears in the list.
3. Click Wiggert → person page with three (empty) sections renders.
Take a screenshot of the person page.

- [ ] **Step 5: Commit**

```bash
git add static/js/people.js static/index.html static/app.js static/js/slashCommands.js static/style.css
git commit -m "feat(hub): People page UI (list + person detail with backlinks)"   # + trailers
```

---

## Task 8: Frontend — Meeting-note composer (`static/js/meetingNote.js`)

**Files:**
- Create: `static/js/meetingNote.js`
- Modify: `static/style.css` (`.mnote-*` block)
- Modify: `static/js/slashCommands.js` (`/meeting-note` command)

**Interfaces:**
- Consumes: `GET /api/meeting-notes/meetings?q=`, `GET /api/people`, `POST /api/meeting-notes`, `GET /api/meeting-notes/{note_id}` (poll), `POST /api/meeting-notes/{note_id}/promote`
- Produces: `window.openMeetingNote(opts?)` where `opts = {personId?, personName?, eventUid?, eventSummary?}` — opens the composer modal, pre-filling any provided person/meeting.

- [ ] **Step 1: Create `static/js/meetingNote.js`** — a modal (follow the modal/escape/`_esc` conventions in `planner.js`/`notes.js`). Fields:
  - **Person picker**: a text input that queries `/api/people` and offers matches + a "create new" affordance; selecting sets `personId`/`personName`. Pre-filled from `opts.personId`.
  - **Meeting picker**: a text input that queries `/api/meeting-notes/meetings?q=` and lists recent events (summary + date); selecting sets `eventUid`. Pre-filled from `opts.eventUid`.
  - **Title** input, **Notes** textarea (`content`).
  - **Action items**: a checklist editor (reuse the checklist UI shape from `notes.js`; array of `{text, done}`). A `[+ add]` row.
  - Buttons: **Save note** (`make_tasks:false`) and **Save + make tasks** (`make_tasks:true`), both POST `/api/meeting-notes`.
  - After save: render each promoted item with a "→ task" chip (monochrome SVG, links nowhere yet beyond a tooltip — keep minimal). Then **poll** `GET /api/meeting-notes/{note_id}` until `ai_enriched` is true; render `suggested_action_items` as **dim candidate chips** each with **add** (POST `/{note_id}/promote` with `{title, person_id, due_date}`) and **dismiss** (client-only hide). Hand-typed items are already promoted on save with no AI in the path.
  - Escape closes; `e.preventDefault()` on the Escape keydown (the wrapped-WebView NSBeep lesson). All untrusted strings via `_esc`. No emoji.

- [ ] **Step 2: Wire slash command + styles**
  - `static/js/slashCommands.js`: add a `/meeting-note` command → `openMeetingNote()`.
  - `static/style.css`: add a `.mnote-*` block (modal, `.mnote-picker`, `.mnote-item`, `.mnote-candidate` dim style for AI chips) reusing vars/classes.

- [ ] **Step 3: Syntax check**

Run: `node --check static/js/meetingNote.js && node --check static/js/slashCommands.js`
Expected: no output.

- [ ] **Step 4: Live verification (the full tracer loop)** — backend running, Ollama up, an iCloud calendar synced (366 events present):

1. From Wiggert's person page → **+ Meeting note**. Composer opens with Wiggert pre-selected.
2. Meeting picker → pick a real "Weekly 1:1" event.
3. Title "1:1 — Wiggert", notes prose mentioning an action ("Wiggert to send the Q3 deck by Friday"). Add a hand-typed action item "Review perf doc".
4. **Save + make tasks** → both promote; "→ task" chips appear.
5. Within a few seconds an **AI candidate chip** ("Send the Q3 deck", owner Wiggert, due resolved) appears → click **add**.
6. Go back to Wiggert's person page → **Open items** shows the promoted tasks; **Meetings** shows the 1:1; **Notes** shows the note.
7. Open a *new* meeting note for Wiggert → the still-incomplete tasks are visible on her page (resurfacing).

Take a screenshot of the Person page with resurfaced tasks.

- [ ] **Step 5: Commit**

```bash
git add static/js/meetingNote.js static/style.css static/js/slashCommands.js
git commit -m "feat(hub): meeting-note composer (pickers, promotion, AI candidate chips)"   # + trailers
```

---

## Final verification

- [ ] Full backend suite for the slice: `./venv/bin/python -m pytest tests/test_links.py tests/test_people_routes.py tests/test_meeting_notes.py tests/test_extract_action_items.py -v` — all green.
- [ ] `python -m compileall -q app.py core routes src` — no syntax errors.
- [ ] `node --check` on every changed JS file.
- [ ] The live tracer loop (Task 8 Step 4) verified end-to-end against the real iCloud calendar, with a screenshot of the Person page showing resurfaced tasks.
- [ ] Owner-isolation P0s present and passing for `Link` and `Person`.

## What this unlocks next (out of scope — see spec §7)

Daily "Today" surface (Sunsama/Routine), capture chips (`+person`/`#area`), Email/Document nodes via the same `Link` table, Goals/Habits, Projects/Areas. Each is a new *view*, not a rebuild.
