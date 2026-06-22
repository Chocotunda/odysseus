# Odysseus Client API Readiness (Slice 1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Odysseus REST API ready for the native Tide client's incremental, offline-capable sync over the task graph — people/areas token scopes, PATCH + server-computed reorder for `PlanItem`, and a `?since=`/soft-delete delta contract.

**Architecture:** Additive changes behind the existing hub seams. New columns/migrations live in `core/hub_models.py` (kept out of upstream's hot `core/database.py`); new routes extend `routes/planner_routes.py`; people/area routes gain the same token-scope `_owner(request, allowed)` gate the planner already uses. The delta endpoint uses the existing `updated_at` watermark plus a new `deleted_at` tombstone column; reorder uses a float `ordinal` mirroring TideCore's `Ordinal.between` midpoint formula exactly.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), Pydantic v2, pytest (`asyncio_mode = auto`).

## Global Constraints

- Python 3.11+. No new dependencies.
- **Owner-scoping is a security boundary.** Every new query is exact-owner-only (no `include_shared`); a missing/null-owner/cross-owner row 404s (never 403 — don't leak existence). The `_owner = require_user(request) or None` + `if user is not None and row.owner != user` pattern is the deliberate codebase convention — do not "fix" it.
- **API-token scope gates:** browser/cookie sessions bypass scope checks (they auth by cookie); only `request.state.api_token` callers are gated. Writes require the `*:write` scope; reads accept either `*:read` or `*:write`.
- New columns on an **existing** table require a guarded, idempotent `_migrate_*` helper wired into `run_hub_migrations()` — `create_all()` does NOT alter existing tables.
- Paths/config via `src/constants.py`, never literals. No Unicode emoji anywhere (code or UI).
- Conventional Commits (`type(scope): summary`); commit after each task. End commit messages with the two trailer lines used in this repo:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01PGDyWgh782Qs364coEss8k`.
- Test pattern (mirror `tests/test_planner_owner_scope.py`): real in-memory SQLite (`create_engine("sqlite://", StaticPool)`) + `monkeypatch.setattr(<module>, "SessionLocal", SessionFactory)`; endpoints fetched via a `_endpoint(router, path, method)` helper and called directly with `SimpleNamespace` requests.
- Run focused tests with `./venv/bin/python -m pytest <path> -q`. `./venv/bin/python -m compileall -q routes core src` must stay clean.

---

## File Structure

- `routes/api_token_routes.py` — MODIFY: add `people:*`/`areas:*` to `ALLOWED_SCOPES`, `ensure_before` pairs, and a `tide` profile.
- `routes/people_routes.py` — MODIFY: replace plain `_owner`/`_load` with the scope-aware variants; add `PEOPLE_{READ,WRITE}_SCOPES`.
- `routes/area_routes.py` — MODIFY: same treatment; add `AREA_{READ,WRITE}_SCOPES`.
- `core/hub_models.py` — MODIFY: `PlanItem.ordinal` → `Float`; add `PlanItem.deleted_at`; add `_migrate_add_plan_item_deleted_at_column()` and call it from `run_hub_migrations()`.
- `routes/planner_routes.py` — MODIFY: `ORDINAL_GAP` → `1024.0`; `_item_to_dict` gains `deleted`/`deleted_at`; add `_ordinal_between`, exclude tombstones at live-read sites; add `PlanItemPatch`/`ReorderBody`; add routes PATCH `/items/{id}`, POST `/items/{id}/reorder`, DELETE `/items/{id}`, GET `/items/changes`.
- `src/today.py` — MODIFY: exclude tombstones in `day_view`.
- `routes/today_routes.py` — MODIFY: exclude tombstones in `_load_task`.
- `src/meeting_notes.py` — MODIFY: exclude tombstones in `_max_ordinal` and the dup-check query.
- Tests (CREATE): `tests/test_planner_reorder.py`, `tests/test_planner_patch.py`, `tests/test_planner_softdelete.py`, `tests/test_planner_changes.py`, `tests/test_people_owner_scope.py`, `tests/test_areas_owner_scope.py`, `tests/test_hub_deleted_at_migration.py`.

---

## Task 1: People & Area token scopes

**Files:**
- Modify: `routes/api_token_routes.py:14-36` (ALLOWED_SCOPES, ensure_before, TOKEN_PROFILES)
- Modify: `routes/people_routes.py:45-141` (scope-aware `_owner`/`_load` + call sites)
- Modify: `routes/area_routes.py:35-110` (scope-aware `_owner`/`_load` + call sites)
- Test: `tests/test_people_owner_scope.py`, `tests/test_areas_owner_scope.py`

**Interfaces:**
- Produces: `people_routes.PEOPLE_READ_SCOPES = {"people:read","people:write"}`, `PEOPLE_WRITE_SCOPES = {"people:write"}`; `area_routes.AREA_READ_SCOPES`, `AREA_WRITE_SCOPES`. `_owner(request, allowed) -> Optional[str]` and `_load(db, request, id, allowed)` in both modules (same shape as `planner_routes._owner`).
- Consumes: nothing from later tasks.

- [ ] **Step 1: Write the failing test** — `tests/test_people_owner_scope.py`

```python
"""People routes must honor API-token scopes (people:read / people:write),
mirroring the planner's token gate. Browser sessions are unaffected."""
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from core.database import Base
from core.hub_models import Person
import routes.people_routes as people_routes


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _token_request(owner, scopes):
    return SimpleNamespace(state=SimpleNamespace(
        current_user="api", api_token=True, api_token_scopes=list(scopes), api_token_owner=owner))


def _endpoint(router, path, method):
    full = f"/api/people{path}"
    for route in router.routes:
        if route.path == full and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def _seed_person(SessionFactory, owner, name="Ada"):
    db = SessionFactory()
    try:
        p = Person(id=str(uuid.uuid4()), owner=owner, name=name)
        db.add(p); db.commit()
        return p.id
    finally:
        db.close()


def test_read_scope_token_can_list(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(people_routes, "SessionLocal", SessionFactory)
    pid = _seed_person(SessionFactory, "alice")
    router = people_routes.setup_people_routes()
    list_people = _endpoint(router, "", "GET")
    out = list_people(_token_request("alice", ["people:read"]))
    ids = {p["id"] for p in out["people"]}
    assert pid in ids


def test_write_endpoint_rejects_read_only_token(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(people_routes, "SessionLocal", SessionFactory)
    router = people_routes.setup_people_routes()
    create_person = _endpoint(router, "", "POST")
    body = people_routes.PersonCreate(name="Bob")
    with pytest.raises(HTTPException) as exc:
        create_person(_token_request("alice", ["people:read"]), body=body)
    assert exc.value.status_code == 403


def test_missing_people_scope_rejected(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(people_routes, "SessionLocal", SessionFactory)
    router = people_routes.setup_people_routes()
    list_people = _endpoint(router, "", "GET")
    with pytest.raises(HTTPException) as exc:
        list_people(_token_request("alice", ["todos:read"]))
    assert exc.value.status_code == 403
```

(Confirm the real Pydantic model name for create — it is `PersonCreate` in `people_routes.py`; if the field is not `name`, adjust the test to the actual required field. Read the model before finalizing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_people_owner_scope.py -q`
Expected: FAIL (token branch not implemented — `create_person` succeeds instead of 403, or `_owner` has wrong signature).

- [ ] **Step 3: Add the scopes in `routes/api_token_routes.py`**

In `ALLOWED_SCOPES` add the four entries:

```python
    "people:read",
    "people:write",
    "areas:read",
    "areas:write",
```

In `_normalize_scopes`, after the existing `ensure_before(...)` calls add:

```python
    ensure_before("people:write", "people:read")
    ensure_before("areas:write", "areas:read")
```

In `TOKEN_PROFILES` add:

```python
    "tide": ["todos:read", "todos:write", "people:read", "people:write", "areas:read", "areas:write"],
```

- [ ] **Step 4: Make `people_routes.py` scope-aware**

At module top (near the imports), add:

```python
PEOPLE_READ_SCOPES = {"people:read", "people:write"}
PEOPLE_WRITE_SCOPES = {"people:write"}
```

Replace the `_owner`/`_load` definitions inside `setup_people_routes` with:

```python
    def _owner(request: Request, allowed: set) -> Optional[str]:
        # Token callers must carry one of `allowed`; browser sessions bypass.
        if getattr(request.state, "api_token", False):
            scopes = set(getattr(request.state, "api_token_scopes", []) or [])
            if not scopes.intersection(allowed):
                raise HTTPException(403, f"API token missing required scope: {' or '.join(sorted(allowed))}")
            owner = getattr(request.state, "api_token_owner", None)
            if not owner:
                raise HTTPException(403, "API token has no owner")
            return owner
        return require_user(request) or None

    def _load(db, request: Request, person_id: str, allowed: set) -> Person:
        owner = _owner(request, allowed)
        p = db.query(Person).filter(Person.id == person_id).first()
        if not p or (owner is not None and p.owner != owner):
            raise HTTPException(status_code=404, detail="person not found")
        return p
```

Then update every call site in this module per the mapping (read endpoints pass READ, mutating endpoints pass WRITE):

| Endpoint | Call change |
|---|---|
| `GET ""` (list) | `_owner(request, PEOPLE_READ_SCOPES)` |
| `POST ""` (create) | `_owner(request, PEOPLE_WRITE_SCOPES)` |
| `GET /{id}` | `_load(db, request, person_id, PEOPLE_READ_SCOPES)` |
| `PUT /{id}` | `_load(db, request, person_id, PEOPLE_WRITE_SCOPES)` |
| `DELETE /{id}` | `_load(db, request, person_id, PEOPLE_WRITE_SCOPES)` |
| `GET /{id}/page` | `_owner(request, PEOPLE_READ_SCOPES)` (and any `_load` it uses → READ) |

Grep `_owner(request)` and `_load(` in the file to ensure no bare call remains.

- [ ] **Step 5: Run people tests**

Run: `./venv/bin/python -m pytest tests/test_people_owner_scope.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Mirror for `area_routes.py`**

Add constants:

```python
AREA_READ_SCOPES = {"areas:read", "areas:write"}
AREA_WRITE_SCOPES = {"areas:write"}
```

Apply the identical `_owner(request, allowed)` / `_load(db, request, area_id, allowed)` rewrite, with `Area`/`"area not found"`, and the same read/write mapping across list/create/get/put/delete/page. Write `tests/test_areas_owner_scope.py` mirroring the people test (prefix `/api/areas`, model `AreaCreate`, response key `"areas"`, seed `Area(id=..., owner=..., name="Work")`). Note: `list_areas` calls `ensure_seeded_areas(db, owner)` — that is fine with a token owner.

- [ ] **Step 7: Run area tests + compile**

Run: `./venv/bin/python -m pytest tests/test_areas_owner_scope.py tests/test_people_owner_scope.py -q && ./venv/bin/python -m compileall -q routes`
Expected: PASS; compile clean.

- [ ] **Step 8: Commit**

```bash
git add routes/api_token_routes.py routes/people_routes.py routes/area_routes.py tests/test_people_owner_scope.py tests/test_areas_owner_scope.py
git commit -m "feat(hub): add people/areas token scopes for the Tide client"
```

---

## Task 2: Float ordinal + server-computed reorder

**Files:**
- Modify: `core/hub_models.py` (`PlanItem.ordinal` → `Float`)
- Modify: `routes/planner_routes.py` (`ORDINAL_GAP` → `1024.0`, `_ordinal_between`, `ReorderBody`, reorder route)
- Test: `tests/test_planner_reorder.py`

**Interfaces:**
- Produces: `planner_routes._ordinal_between(a: Optional[float], b: Optional[float]) -> float`; `ReorderBody(before_id: Optional[str], after_id: Optional[str])`; route `POST /api/planner/items/{item_id}/reorder`.
- Consumes: `_get_owned`, `_item_to_dict` (Task 0/existing).

- [ ] **Step 1: Write the failing test** — `tests/test_planner_reorder.py`

```python
import uuid
import pytest
from fastapi import HTTPException
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


def _seed(SF, owner, ordinal, title="t"):
    db = SF()
    try:
        it = PlanItem(id=str(uuid.uuid4()), owner=owner, title=title, ordinal=ordinal)
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


def test_between_formula():
    f = planner_routes._ordinal_between
    assert f(None, None) == 0
    assert f(None, 1024.0) == 1024.0 - 1024.0
    assert f(2048.0, None) == 2048.0 + 1024.0
    assert f(1024.0, 2048.0) == 1536.0


def test_reorder_into_middle_sets_float_midpoint(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    a = _seed(SF, "alice", 1024.0, "a")
    b = _seed(SF, "alice", 2048.0, "b")
    mover = _seed(SF, "alice", 4096.0, "mover")
    router = planner_routes.setup_planner_routes()
    reorder = _endpoint(router, "/items/{item_id}/reorder", "POST")
    out = reorder(_req("alice"), item_id=mover, body=planner_routes.ReorderBody(before_id=a, after_id=b))
    assert out["ordinal"] == 1536.0


def test_reorder_unknown_neighbor_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    mover = _seed(SF, "alice", 1024.0)
    router = planner_routes.setup_planner_routes()
    reorder = _endpoint(router, "/items/{item_id}/reorder", "POST")
    with pytest.raises(HTTPException) as exc:
        reorder(_req("alice"), item_id=mover, body=planner_routes.ReorderBody(before_id="nope", after_id=None))
    assert exc.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_planner_reorder.py -q`
Expected: FAIL (`_ordinal_between` / `ReorderBody` / reorder route not defined).

- [ ] **Step 3: Change `ordinal` to Float in `core/hub_models.py`**

In the `PlanItem` model change:

```python
    ordinal       = Column(Float, default=0, index=True)
```

Ensure `Float` is imported from `sqlalchemy` at the top of `core/hub_models.py` (add to the existing `from sqlalchemy import (...)` import if absent). No SQLite migration is needed — SQLite preserves REAL values in an integer-affinity column and compares numerically.

- [ ] **Step 4: Add the helper, model, and route in `routes/planner_routes.py`**

Change the constant: `ORDINAL_GAP = 1024.0`.

Add near the other Pydantic models:

```python
class ReorderBody(BaseModel):
    before_id: Optional[str] = None   # item directly above the drop point (None = top)
    after_id: Optional[str] = None    # item directly below the drop point (None = bottom)
```

Add a module-level helper (mirrors TideCore `Ordinal.between` exactly):

```python
def _ordinal_between(a: Optional[float], b: Optional[float]) -> float:
    # TODO: rebalance a bucket if a midpoint gap collapses (~50 same-gap inserts
    # before Double precision exhausts) — matches TideCore's Ordinal TODO.
    if a is None and b is None:
        return 0.0
    if a is None:
        return b - ORDINAL_GAP
    if b is None:
        return a + ORDINAL_GAP
    return (a + b) / 2
```

Add the route inside `setup_planner_routes` (after `plan_item`):

```python
    # --- REORDER (drag): place item between two neighbours (either may be None) ---
    @router.post("/items/{item_id}/reorder")
    def reorder_item(request: Request, item_id: str, body: ReorderBody):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = _get_owned(db, item_id, user)
            before = _get_owned(db, body.before_id, user) if body.before_id else None
            after = _get_owned(db, body.after_id, user) if body.after_id else None
            item.ordinal = _ordinal_between(
                before.ordinal if before else None,
                after.ordinal if after else None,
            )
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()
```

- [ ] **Step 5: Run reorder tests**

Run: `./venv/bin/python -m pytest tests/test_planner_reorder.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Regression — existing planner tests still green**

Run: `./venv/bin/python -m pytest tests/test_planner_crud.py tests/test_planner_capture.py tests/test_planner_owner_scope.py -q`
Expected: PASS (the Float ordinal must not break integer-gap allocation; `_next_ordinal` returns `(max or 0) + 1024.0`, still numeric).

- [ ] **Step 7: Commit**

```bash
git add core/hub_models.py routes/planner_routes.py tests/test_planner_reorder.py
git commit -m "feat(hub): float ordinal + server-computed reorder for PlanItem"
```

---

## Task 3: PATCH partial update

**Files:**
- Modify: `routes/planner_routes.py` (`PlanItemPatch`, PATCH route, enum clamp helper)
- Test: `tests/test_planner_patch.py`

**Interfaces:**
- Produces: `PlanItemPatch` (all fields Optional); route `PATCH /api/planner/items/{item_id}`.
- Consumes: `_get_owned`, `_item_to_dict`.

- [ ] **Step 1: Write the failing test** — `tests/test_planner_patch.py`

```python
import uuid
import pytest
from fastapi import HTTPException
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


def _seed(SF, owner, **kw):
    db = SF()
    try:
        it = PlanItem(id=str(uuid.uuid4()), owner=owner, title=kw.pop("title", "t"), **kw)
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


def test_patch_applies_only_sent_fields(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice", title="orig", notes="keep", planned_day="2026-06-22")
    router = planner_routes.setup_planner_routes()
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    out = patch(_req("alice"), item_id=iid, body=planner_routes.PlanItemPatch(title="new"))
    assert out["title"] == "new"
    assert out["notes"] == "keep"          # untouched
    assert out["planned_day"] == "2026-06-22"


def test_patch_explicit_null_clears_field(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice", planned_day="2026-06-22")
    router = planner_routes.setup_planner_routes()
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    body = planner_routes.PlanItemPatch.model_validate({"planned_day": None})
    out = patch(_req("alice"), item_id=iid, body=body)
    assert out["planned_day"] is None


def test_patch_clamps_bad_priority(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice")
    router = planner_routes.setup_planner_routes()
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    out = patch(_req("alice"), item_id=iid, body=planner_routes.PlanItemPatch(priority="bogus"))
    assert out["priority"] == "normal"


def test_patch_cross_owner_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "bob")
    router = planner_routes.setup_planner_routes()
    patch = _endpoint(router, "/items/{item_id}", "PATCH")
    with pytest.raises(HTTPException) as exc:
        patch(_req("alice"), item_id=iid, body=planner_routes.PlanItemPatch(title="x"))
    assert exc.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_planner_patch.py -q`
Expected: FAIL (`PlanItemPatch` / PATCH route not defined).

- [ ] **Step 3: Add `PlanItemPatch`, clamp constants, and the PATCH route**

Add near the Pydantic models:

```python
_VALID_PRIORITY = {"none", "normal", "important", "urgent"}
_VALID_STATUS = {"open", "in_progress", "done", "cancelled"}


class PlanItemPatch(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    planned_day: Optional[str] = None
    due_date: Optional[str] = None
    planned_start: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    estimate_minutes: Optional[int] = None
    project_id: Optional[str] = None
    ordinal: Optional[float] = None
    source_note_id: Optional[str] = None
    source_event_id: Optional[str] = None
    person_id: Optional[str] = None
```

Add the route inside `setup_planner_routes`:

```python
    # --- PATCH (partial update; absent key != explicit null) ---
    @router.patch("/items/{item_id}")
    def patch_item(request: Request, item_id: str, body: PlanItemPatch):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = _get_owned(db, item_id, user)
            fields = body.model_dump(exclude_unset=True)   # only keys the client sent
            if "priority" in fields and fields["priority"] not in _VALID_PRIORITY:
                fields["priority"] = "normal"
            if "status" in fields and fields["status"] not in _VALID_STATUS:
                fields.pop("status")                        # ignore an invalid status rather than corrupt it
            for k, v in fields.items():
                setattr(item, k, v)
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()
```

- [ ] **Step 4: Run PATCH tests**

Run: `./venv/bin/python -m pytest tests/test_planner_patch.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add routes/planner_routes.py tests/test_planner_patch.py
git commit -m "feat(hub): PATCH partial-update for PlanItem"
```

---

## Task 4: Soft-delete column, migration, DELETE route, tombstone exclusion

**Files:**
- Modify: `core/hub_models.py` (`PlanItem.deleted_at`, `_migrate_add_plan_item_deleted_at_column`, register in `run_hub_migrations`)
- Modify: `routes/planner_routes.py` (`_item_to_dict` fields, exclude tombstones in `_get_owned`/`_next_ordinal`/`list_items`, DELETE route)
- Modify: `src/today.py` (`day_view`), `routes/today_routes.py` (`_load_task`), `src/meeting_notes.py` (`_max_ordinal`, dup-check)
- Test: `tests/test_planner_softdelete.py`, `tests/test_hub_deleted_at_migration.py`

**Interfaces:**
- Produces: `PlanItem.deleted_at` column; `_item_to_dict` keys `deleted: bool`, `deleted_at: Optional[str]`; route `DELETE /api/planner/items/{item_id}`; migration `_migrate_add_plan_item_deleted_at_column()`.
- Consumes: existing `_get_owned`, `utcnow_naive`.

- [ ] **Step 1: Write the failing test** — `tests/test_planner_softdelete.py`

```python
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from core.database import Base
from core.hub_models import PlanItem
import routes.planner_routes as planner_routes
import src.today as today_mod


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _seed(SF, owner, **kw):
    db = SF()
    try:
        it = PlanItem(id=str(uuid.uuid4()), owner=owner, title=kw.pop("title", "t"), **kw)
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


def test_delete_soft_marks_and_excludes_from_list(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice", status="open")
    router = planner_routes.setup_planner_routes()
    delete = _endpoint(router, "/items/{item_id}", "DELETE")
    out = delete(_req("alice"), item_id=iid)
    assert out["deleted"] is True
    list_items = _endpoint(router, "/items", "GET")
    ids = {i["id"] for i in list_items(_req("alice"))["items"]}
    assert iid not in ids


def test_get_deleted_item_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice")
    router = planner_routes.setup_planner_routes()
    _endpoint(router, "/items/{item_id}", "DELETE")(_req("alice"), item_id=iid)
    get_item = _endpoint(router, "/items/{item_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        get_item(_req("alice"), item_id=iid)
    assert exc.value.status_code == 404


def test_day_view_excludes_tombstones(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    iid = _seed(SF, "alice", status="open", planned_day="2026-06-22")
    router = planner_routes.setup_planner_routes()
    _endpoint(router, "/items/{item_id}", "DELETE")(_req("alice"), item_id=iid)
    db = SF()
    try:
        view = today_mod.day_view(db, "alice", "2026-06-22", today="2026-06-22")
    finally:
        db.close()
    all_ids = {t["id"] for bucket in ("scheduled", "unscheduled", "overdue")
               for t in view.get("tasks", {}).get(bucket, [])}
    assert iid not in all_ids
```

(Adjust the `day_view` result-shape assertion to the actual structure returned by `src/today.py:day_view` — read it first; the key point is the deleted id must not appear in any task bucket.)

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_planner_softdelete.py -q`
Expected: FAIL (DELETE route + `deleted_at` not present).

- [ ] **Step 3: Add the column + migration in `core/hub_models.py`**

In `PlanItem` (near `completed_at`):

```python
    deleted_at    = Column(DateTime, nullable=True, index=True)   # soft-delete tombstone; NULL = live
```

(Ensure `DateTime` is imported — it already is for `completed_at`.)

Add the migration helper (mirror `_migrate_add_plan_item_planned_start_column`):

```python
def _migrate_add_plan_item_deleted_at_column():
    """Add `deleted_at` (soft-delete tombstone) to plan_items. Guarded + idempotent."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(plan_items)")
        columns = [row[1] for row in cursor.fetchall()]
        if "deleted_at" not in columns:
            conn.execute("ALTER TABLE plan_items ADD COLUMN deleted_at DATETIME")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_plan_items_deleted_at ON plan_items(deleted_at)")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'deleted_at' to plan_items")
    except Exception as e:
        logging.getLogger(__name__).warning(f"plan_items.deleted_at migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
```

Register it in `run_hub_migrations()` (add the call after the existing `_migrate_add_plan_item_planned_start_column()`):

```python
    _migrate_add_plan_item_deleted_at_column()
```

- [ ] **Step 4: Extend `_item_to_dict` and add the DELETE route + exclusions in `planner_routes.py`**

In `_item_to_dict`, add before `ai_enriched`:

```python
        "deleted": item.deleted_at is not None,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
```

In `_get_owned`, exclude tombstones — change the gate:

```python
        if user is not None and item.owner != user:
            raise HTTPException(404, "Task not found")
        if item.deleted_at is not None:
            raise HTTPException(404, "Task not found")
        return item
```

In `_next_ordinal`, add `.filter(PlanItem.deleted_at.is_(None))` to the max query. In `list_items`, after the owner filter add `q = q.filter(PlanItem.deleted_at.is_(None))`.

Add the DELETE route inside `setup_planner_routes`:

```python
    # --- DELETE (soft): set the tombstone so the change syncs to clients ---
    @router.delete("/items/{item_id}")
    def delete_item(request: Request, item_id: str):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = _get_owned(db, item_id, user)   # 404s if already tombstoned
            item.deleted_at = utcnow_naive()
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()
```

(`utcnow_naive` is already imported from `core.database`.)

- [ ] **Step 5: Exclude tombstones in the other live-read sites**

- `src/today.py` `day_view` — change `q = db.query(PlanItem).filter(PlanItem.status == "open")` to also chain `.filter(PlanItem.deleted_at.is_(None))`.
- `routes/today_routes.py` `_load_task` — change the query to `.filter(PlanItem.id == task_id, PlanItem.deleted_at.is_(None))`.
- `routes/people_routes.py` open-tasks query — add `PlanItem.deleted_at.is_(None)` to the `.filter(...)`.
- `routes/area_routes.py` open-tasks query — same.
- `src/meeting_notes.py` `_max_ordinal` and the dup-check query — add `PlanItem.deleted_at.is_(None)` to each `.filter(...)`.

- [ ] **Step 6: Write the migration test** — `tests/test_hub_deleted_at_migration.py`

```python
import sqlite3
import core.hub_models as hub_models


def test_deleted_at_migration_adds_column(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE plan_items (id TEXT PRIMARY KEY, ordinal INTEGER)")
    conn.execute("INSERT INTO plan_items (id, ordinal) VALUES ('x', 1024)")
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_plan_item_deleted_at_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(plan_items)").fetchall()]
    # fractional ordinal round-trips in the integer-affinity column (no migration needed there)
    conn.execute("UPDATE plan_items SET ordinal = 1536.5 WHERE id = 'x'")
    val = conn.execute("SELECT ordinal FROM plan_items WHERE id='x'").fetchone()[0]
    conn.close()
    assert "deleted_at" in cols
    assert val == 1536.5
```

- [ ] **Step 7: Run soft-delete + migration tests**

Run: `./venv/bin/python -m pytest tests/test_planner_softdelete.py tests/test_hub_deleted_at_migration.py -q`
Expected: PASS.

- [ ] **Step 8: Regression across hub + compile**

Run: `./venv/bin/python -m pytest tests/test_planner_crud.py tests/test_planner_capture.py tests/test_planner_owner_scope.py tests/test_today.py -q && ./venv/bin/python -m compileall -q routes core src`
Expected: PASS; compile clean. (If `tests/test_today.py` does not exist, drop it from the command.)

- [ ] **Step 9: Commit**

```bash
git add core/hub_models.py routes/planner_routes.py src/today.py routes/today_routes.py routes/people_routes.py routes/area_routes.py src/meeting_notes.py tests/test_planner_softdelete.py tests/test_hub_deleted_at_migration.py
git commit -m "feat(hub): soft-delete tombstones + DELETE route for PlanItem"
```

---

## Task 5: `/items/changes` delta endpoint

**Files:**
- Modify: `routes/planner_routes.py` (parse helper, `GET /items/changes` route)
- Test: `tests/test_planner_changes.py`

**Interfaces:**
- Produces: route `GET /api/planner/items/changes?since=<iso8601>` returning `{"items": [...], "cursor": "<iso8601>"}`; helper `_parse_since(s: Optional[str]) -> Optional[datetime]`.
- Consumes: `_owner`, `_item_to_dict`, `utcnow_naive`, `PlanItem.updated_at`, `PlanItem.deleted_at`.

- [ ] **Step 1: Write the failing test** — `tests/test_planner_changes.py`

```python
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace
from datetime import datetime, timedelta

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


def test_changes_malformed_since_400(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    changes = _endpoint(router, "/items/changes", "GET")
    with pytest.raises(HTTPException) as exc:
        changes(_req("alice"), since="not-a-date")
    assert exc.value.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_planner_changes.py -q`
Expected: FAIL (route not defined).

- [ ] **Step 3: Add the parse helper + route in `planner_routes.py`**

Add `from datetime import datetime` to the imports if not present. Add the helper module-level:

```python
def _parse_since(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Invalid 'since' timestamp (expected ISO-8601)")
    # stored values are naive UTC (utcnow_naive); normalize tz-aware input to naive UTC.
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=None).replace(tzinfo=None)
    return dt
```

Add the route inside `setup_planner_routes` (place it BEFORE `get_item` so `/items/changes` is not captured by `/items/{item_id}`; FastAPI matches in declaration order):

```python
    # --- CHANGES (delta sync): rows updated since the watermark, incl. tombstones ---
    @router.get("/items/changes")
    def list_changes(request: Request, since: Optional[str] = None):
        user = _owner(request, TODO_READ_SCOPES)
        since_dt = _parse_since(since)
        db = SessionLocal()
        try:
            q = db.query(PlanItem)
            if user is not None:
                q = q.filter(PlanItem.owner == user)
            if since_dt is not None:
                q = q.filter(PlanItem.updated_at >= since_dt)   # inclusive; client upserts by id
            items = q.order_by(PlanItem.updated_at.asc()).all()
            dicts = [_item_to_dict(it) for it in items]
            cursor = max((it.updated_at for it in items), default=None)
            cursor_iso = cursor.isoformat() if cursor else utcnow_naive().isoformat()
            return {"items": dicts, "cursor": cursor_iso}
        finally:
            db.close()
```

**Important:** this route MUST be declared before `@router.get("/items/{item_id}")`. If `get_item` is declared earlier in the file, move the `changes` route above it, or FastAPI will route `/items/changes` into `get_item` with `item_id="changes"`. Verify by reading the route order after editing.

- [ ] **Step 4: Run changes tests**

Run: `./venv/bin/python -m pytest tests/test_planner_changes.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full slice regression + compile**

Run: `./venv/bin/python -m pytest tests/test_planner_reorder.py tests/test_planner_patch.py tests/test_planner_softdelete.py tests/test_planner_changes.py tests/test_planner_crud.py tests/test_planner_capture.py tests/test_planner_owner_scope.py tests/test_people_owner_scope.py tests/test_areas_owner_scope.py tests/test_hub_deleted_at_migration.py -q && ./venv/bin/python -m compileall -q routes core src`
Expected: PASS; compile clean.

- [ ] **Step 6: Commit**

```bash
git add routes/planner_routes.py tests/test_planner_changes.py
git commit -m "feat(hub): /items/changes delta endpoint for incremental sync"
```

---

## Final verification (after all tasks)

- [ ] **Run the full hub test surface**

Run: `./venv/bin/python -m pytest tests/test_planner_*.py tests/test_people*.py tests/test_areas*.py tests/test_links.py tests/test_meeting_notes.py tests/test_hub_deleted_at_migration.py -q`
Expected: all green.

- [ ] **Live smoke (backend on :7860):** mint a `tide`-profile token, then exercise the round-trip:
  - `POST /api/tokens` with `profile=tide` → token.
  - `GET /api/planner/items/changes` (Bearer) → items + cursor.
  - `PATCH` a title, `POST /items/{id}/reorder`, `DELETE /items/{id}` → each 200.
  - `GET /api/planner/items/changes?since=<cursor>` → shows the edits + the tombstone with `"deleted": true`.
  - `GET /api/people` and `GET /api/areas` with the same token → 200 (scopes work).

---

## Self-Review

**Spec coverage:**
- §1 token scopes → Task 1 (api_token + people + area routes). ✓
- §2 ordinal Float + reorder → Task 2. ✓
- §3 PATCH → Task 3. ✓
- §4 soft-delete column/migration/DELETE + exclude at all enumerated read sites → Task 4 (today.py, today_routes, people, area, meeting_notes, planner all covered). ✓
- §5 `/items/changes` with inclusive `since`, cursor, tombstones, malformed-400 → Task 5. ✓
- Out-of-scope (MCP, people/areas delta sync, Horizon, GC, conflict) → not implemented, by design. ✓

**Placeholder scan:** Two spots ask the implementer to confirm a real symbol against the file before finalizing (the `PersonCreate`/`AreaCreate` field name in Task 1; the exact `day_view` result shape in Task 4). These are verification instructions, not missing content — the test code is fully written and the adjustment is a one-line field-name match. Acceptable.

**Type consistency:** `_ordinal_between(Optional[float], Optional[float]) -> float`, `ORDINAL_GAP = 1024.0`, `PlanItem.ordinal: Float`, `PlanItemPatch.ordinal: Optional[float]` all agree. `_item_to_dict` keys (`deleted`, `deleted_at`, `cursor`) match the tests. The `/items/changes` route-ordering hazard is called out explicitly.
