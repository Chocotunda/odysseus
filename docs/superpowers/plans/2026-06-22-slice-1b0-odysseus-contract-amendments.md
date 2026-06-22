# Slice 1b-0 — Odysseus Contract Amendments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Amend the (already-shipped) slice-1a Odysseus REST contract so the native Tide client can sync reliably — accept a client-supplied `id` on create (idempotent upsert) and add a monotonic per-owner `seq` column used as the `?since=` cursor (replacing raw `updated_at`).

**Architecture:** Additive changes inside `core/hub_models.py` (model + guarded migration) and `routes/planner_routes.py` (create idempotency, a `_next_seq` helper assigned on every write, and the `/items/changes` cursor switch). `updated_at` stays as the last-writer-wins tie-breaker; `seq` becomes the pagination cursor. No new dependencies.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), Pydantic v2, pytest.

## Global Constraints

- Python 3.11+. No new dependencies.
- **Owner-scoping is a security boundary.** Exact-owner only; a missing/null-owner/cross-owner row 404s (never 403 — don't leak existence). Preserve the `_owner(request, allowed)` + `_get_owned` pattern. Writes require `TODO_WRITE_SCOPES`; reads accept `TODO_READ_SCOPES`.
- A new column on the **existing** `plan_items` table requires a guarded, idempotent `_migrate_*` helper wired into `run_hub_migrations()` — `create_all()` does NOT alter existing tables. The migration must also **backfill** existing rows so the cursor is coherent.
- `seq` is **per-owner monotonic, assigned on EVERY write** (create, capture, complete, plan, patch, reorder, delete, and the background `enrich_item`). Missing a site = a row that silently never syncs. `seq` is allocated as `max(seq for owner) + 1` and is NOT filtered by `deleted_at` (tombstones occupy seq space; a delete itself bumps seq).
- This is a deliberate breaking change to 1a's `?since=` (ISO timestamp → integer `seq`). There are no shipped clients, so switch outright — do not keep dual behavior.
- Test pattern: mirror `tests/test_planner_changes.py` / `tests/test_planner_crud.py` — real in-memory SQLite (`create_engine("sqlite://", StaticPool)`) + `monkeypatch.setattr(planner_routes, "SessionLocal", SessionFactory)`; endpoints via the `_endpoint(router, path, method)` helper, called with `SimpleNamespace` requests. Migration tests use a raw `sqlite3` legacy DB + `monkeypatch.setattr(hub_models, "DATABASE_URL", ...)` (see `tests/test_hub_deleted_at_migration.py`).
- Run focused tests: `./venv/bin/python -m pytest <path> -q`. `./venv/bin/python -m compileall -q routes core` must stay clean.
- Conventional Commits; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01PGDyWgh782Qs364coEss8k`.

---

## File Structure
- `core/hub_models.py` — MODIFY: add `PlanItem.seq` column; add `_migrate_add_plan_item_seq_column()` (with backfill) registered in `run_hub_migrations()`.
- `routes/planner_routes.py` — MODIFY: `PlanItemCreate.id`; idempotent create; `_next_seq` helper; assign `seq` at all 8 write sites; `_item_to_dict` gains `seq`; `/items/changes` switches to the integer `seq` cursor; remove the now-dead `_parse_since` (+ unused `timezone` import).
- Tests (CREATE/MODIFY): `tests/test_planner_client_id.py`, `tests/test_planner_seq.py`, `tests/test_hub_seq_migration.py`, and rewrite `tests/test_planner_changes.py` for the seq cursor.

---

## Task 1: Client-supplied id on create (idempotent upsert)

**Files:**
- Modify: `routes/planner_routes.py` (`PlanItemCreate`, `create_item`)
- Test: `tests/test_planner_client_id.py`

**Interfaces:**
- Produces: `PlanItemCreate.id: Optional[str]`; `POST /api/planner/items` accepts and honors a client id idempotently.
- Consumes: existing `_owner`, `_item_to_dict`, `_next_ordinal`.

- [ ] **Step 1: Write the failing test** — `tests/test_planner_client_id.py`

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


def _endpoint(router, path, method):
    full = f"/api/planner{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_create_honors_client_id(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    cid = str(uuid.uuid4())
    out = create(_req("alice"), body=planner_routes.PlanItemCreate(id=cid, title="A"))
    assert out["id"] == cid


def test_create_same_id_is_idempotent(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    cid = str(uuid.uuid4())
    first = create(_req("alice"), body=planner_routes.PlanItemCreate(id=cid, title="A"))
    second = create(_req("alice"), body=planner_routes.PlanItemCreate(id=cid, title="A retried"))
    assert second["id"] == cid
    # idempotent: returns the existing row, does NOT create a duplicate or overwrite via create
    assert second["title"] == "A"
    db = SF()
    try:
        assert db.query(PlanItem).filter(PlanItem.id == cid).count() == 1
    finally:
        db.close()


def test_create_client_id_cross_owner_404(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    cid = str(uuid.uuid4())
    create(_req("bob"), body=planner_routes.PlanItemCreate(id=cid, title="bob's"))
    with pytest.raises(HTTPException) as exc:
        create(_req("alice"), body=planner_routes.PlanItemCreate(id=cid, title="alice steal"))
    assert exc.value.status_code == 404


def test_create_without_id_still_generates(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    out = create(_req("alice"), body=planner_routes.PlanItemCreate(title="no id"))
    assert out["id"]  # a server-generated uuid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_planner_client_id.py -q`
Expected: FAIL (`PlanItemCreate` has no `id`; create ignores it / duplicates).

- [ ] **Step 3: Add `id` to `PlanItemCreate`**

In `routes/planner_routes.py`, add as the FIRST field of `PlanItemCreate`:

```python
    id: Optional[str] = None
```

- [ ] **Step 4: Make `create_item` honor the client id idempotently**

In `create_item`, immediately after `user = _owner(request, TODO_WRITE_SCOPES)` and opening the session, before constructing the new `PlanItem`, add the idempotency guard; then use the client id (or a fresh uuid) as the row id:

```python
        # Idempotent create on a client-supplied id (optimistic-create support).
        if body.id:
            existing = db.query(PlanItem).filter(PlanItem.id == body.id).first()
            if existing is not None:
                if user is not None and existing.owner != user:
                    raise HTTPException(404, "Task not found")  # never collide across owners
                return _item_to_dict(existing)                  # idempotent: return existing, no dup
```

Then change the `id=str(uuid.uuid4())` line in the `PlanItem(...)` constructor to:

```python
                id=body.id or str(uuid.uuid4()),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_planner_client_id.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Regression**

Run: `./venv/bin/python -m pytest tests/test_planner_crud.py tests/test_planner_capture.py tests/test_planner_owner_scope.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add routes/planner_routes.py tests/test_planner_client_id.py
git commit -m "feat(hub): accept client-supplied id on create (idempotent upsert)"
```

---

## Task 2: Monotonic per-owner `seq` column, migration, and assignment on every write

**Files:**
- Modify: `core/hub_models.py` (`PlanItem.seq`, `_migrate_add_plan_item_seq_column`, `run_hub_migrations`)
- Modify: `routes/planner_routes.py` (`_next_seq` helper; assign `seq` at all 8 write sites; `_item_to_dict` gains `seq`)
- Test: `tests/test_planner_seq.py`, `tests/test_hub_seq_migration.py`

**Interfaces:**
- Produces: `PlanItem.seq` (Integer); `planner_routes._next_seq(db, user) -> int`; every write assigns a fresh per-owner `seq`; `_item_to_dict` includes `"seq"`; `_migrate_add_plan_item_seq_column()`.
- Consumes: existing write routes.

- [ ] **Step 1: Write the failing test** — `tests/test_planner_seq.py`

```python
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
    out = capture(_req("alice"), body=planner_routes.CaptureBody(text="buy milk"),
                  background_tasks=SimpleNamespace(add_task=lambda *a, **k: None))
    assert out["seq"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_planner_seq.py -q`
Expected: FAIL (`seq` not in dict / not assigned).

- [ ] **Step 3: Add the `seq` column in `core/hub_models.py`**

In the `PlanItem` model, near `ordinal`/`deleted_at`, add:

```python
    seq           = Column(Integer, index=True)   # per-owner monotonic write sequence; the ?since= cursor
```

(`Integer` is already imported.)

- [ ] **Step 4: Add the guarded migration + backfill, and register it**

Add to `core/hub_models.py` (mirror `_migrate_add_plan_item_deleted_at_column`):

```python
def _migrate_add_plan_item_seq_column():
    """Add per-owner monotonic `seq` to plan_items + backfill existing rows. Guarded + idempotent."""
    import sqlite3
    from collections import defaultdict
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(plan_items)")
        columns = [row[1] for row in cursor.fetchall()]
        if "seq" not in columns:
            conn.execute("ALTER TABLE plan_items ADD COLUMN seq INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_plan_items_seq ON plan_items(seq)")
            # backfill: per-owner 1..N in a stable (updated_at, id) order
            rows = conn.execute(
                "SELECT id, owner FROM plan_items ORDER BY owner, updated_at, id"
            ).fetchall()
            counters = defaultdict(int)
            for rid, owner in rows:
                counters[owner] += 1
                conn.execute("UPDATE plan_items SET seq = ? WHERE id = ?", (counters[owner], rid))
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added + backfilled 'seq' on plan_items")
    except Exception as e:
        logging.getLogger(__name__).warning(f"plan_items.seq migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
```

Register it in `run_hub_migrations()` after the `deleted_at` call:

```python
    _migrate_add_plan_item_seq_column()
```

- [ ] **Step 5: Add the `_next_seq` helper in `routes/planner_routes.py`**

Add inside `setup_planner_routes` next to `_next_ordinal`:

```python
    def _next_seq(db, user: Optional[str]) -> int:
        # Per-owner monotonic. NOT filtered by deleted_at — tombstones occupy seq
        # space and a delete itself bumps seq so the change feed carries it.
        current = db.query(func.max(PlanItem.seq)).filter(PlanItem.owner == user).scalar()
        return (current or 0) + 1
```

- [ ] **Step 6: Assign `seq` at every write site, before each `db.commit()`**

Insert `item.seq = _next_seq(db, <owner>)` immediately before the `db.commit()` in each of these, using the owner variable already in scope (`user` in routes; `owner` in `enrich_item`). For the new-row routes (`create_item`, `capture`) the item is constructed but unflushed — `_next_seq` queries committed rows so the new item correctly gets `max+1`.

| Function | owner var | insert before its `db.commit()` |
|---|---|---|
| `create_item` | `user` | `item.seq = _next_seq(db, user)` |
| `capture` | `user` | `item.seq = _next_seq(db, user)` |
| `complete_item` | `user` | `item.seq = _next_seq(db, user)` |
| `plan_item` | `user` | `item.seq = _next_seq(db, user)` |
| `reorder_item` | `user` | `item.seq = _next_seq(db, user)` |
| `patch_item` | `user` | `item.seq = _next_seq(db, user)` |
| `delete_item` | `user` | `item.seq = _next_seq(db, user)` |
| `enrich_item` (module-level background fn) | `owner` | `item.seq = _next_seq_global(db, owner)` — see note |

**Note on `enrich_item`:** it is a module-level function, NOT inside `setup_planner_routes`, so it cannot see the closure `_next_seq`. Add an identical module-level helper and use it there:

```python
def _next_seq_global(db, owner: Optional[str]) -> int:
    current = db.query(func.max(PlanItem.seq)).filter(PlanItem.owner == owner).scalar()
    return (current or 0) + 1
```

and in `enrich_item`, before its `db.commit()`, add `item.seq = _next_seq_global(db, owner)`. (Have the in-closure `_next_seq` simply call `_next_seq_global(db, user)` to avoid duplication.)

After editing, grep to confirm every commit site assigns seq:
`grep -n "db.commit()\|item.seq = _next_seq" routes/planner_routes.py` — every `db.commit()` in a write route must have a preceding `item.seq = ...`.

- [ ] **Step 7: Add `seq` to `_item_to_dict`**

In `_item_to_dict`, add (near `ordinal`):

```python
        "seq": item.seq,
```

- [ ] **Step 8: Write the migration test** — `tests/test_hub_seq_migration.py`

```python
import sqlite3
import core.hub_models as hub_models


def test_seq_migration_adds_and_backfills(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE plan_items (id TEXT PRIMARY KEY, owner TEXT, updated_at TEXT)")
    conn.executemany(
        "INSERT INTO plan_items (id, owner, updated_at) VALUES (?, ?, ?)",
        [("a", "alice", "2026-06-20"), ("b", "alice", "2026-06-21"), ("c", "bob", "2026-06-20")],
    )
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_plan_item_seq_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(plan_items)").fetchall()]
    seqs = dict(conn.execute("SELECT id, seq FROM plan_items").fetchall())
    conn.close()
    assert "seq" in cols
    assert seqs == {"a": 1, "b": 2, "c": 1}   # per-owner 1..N by updated_at order
```

- [ ] **Step 9: Run seq + migration tests**

Run: `./venv/bin/python -m pytest tests/test_planner_seq.py tests/test_hub_seq_migration.py -q`
Expected: PASS.

- [ ] **Step 10: Regression + compile**

Run: `./venv/bin/python -m pytest tests/test_planner_crud.py tests/test_planner_capture.py tests/test_planner_patch.py tests/test_planner_reorder.py tests/test_planner_softdelete.py tests/test_planner_owner_scope.py -q && ./venv/bin/python -m compileall -q routes core`
Expected: PASS; compile clean.

- [ ] **Step 11: Commit**

```bash
git add core/hub_models.py routes/planner_routes.py tests/test_planner_seq.py tests/test_hub_seq_migration.py
git commit -m "feat(hub): per-owner monotonic seq on every PlanItem write (sync cursor)"
```

---

## Task 3: Switch `/items/changes` to the integer `seq` cursor

**Files:**
- Modify: `routes/planner_routes.py` (`list_changes` signature + body; remove `_parse_since` + unused `timezone` import)
- Test: rewrite `tests/test_planner_changes.py`

**Interfaces:**
- Produces: `GET /api/planner/items/changes?since=<int>` returning `{"items": [...], "cursor": <int>}`, `seq`-ordered, including tombstones.
- Consumes: `seq` (Task 2), `_item_to_dict`, `_owner`.

- [ ] **Step 1: Rewrite the test** — replace `tests/test_planner_changes.py` contents

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


def _endpoint(router, path, method):
    full = f"/api/planner{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def test_changes_no_since_returns_all_with_int_cursor(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    changes = _endpoint(router, "/items/changes", "GET")
    a = create(_req("alice"), body=planner_routes.PlanItemCreate(title="A"))
    out = changes(_req("alice"), since=None)
    assert {i["id"] for i in out["items"]} == {a["id"]}
    assert out["cursor"] == a["seq"]


def test_changes_since_is_exclusive_and_includes_tombstones(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    delete = _endpoint(router, "/items/{item_id}", "DELETE")
    changes = _endpoint(router, "/items/changes", "GET")

    a = create(_req("alice"), body=planner_routes.PlanItemCreate(title="A"))   # seq 1
    b = create(_req("alice"), body=planner_routes.PlanItemCreate(title="B"))   # seq 2
    tomb = delete(_req("alice"), item_id=b["id"])                              # seq 3, tombstone

    out = changes(_req("alice"), since=a["seq"])   # since=1 -> seq>1
    ids = {i["id"]: i for i in out["items"]}
    assert a["id"] not in ids            # seq 1 not > 1
    assert b["id"] in ids                # seq 2 and seq 3 both belong to b's id after delete
    assert ids[b["id"]]["deleted"] is True
    assert out["cursor"] == tomb["seq"]  # max seq returned


def test_changes_cursor_empty_batch_echoes_since(monkeypatch):
    SF = _sf()
    monkeypatch.setattr(planner_routes, "SessionLocal", SF)
    router = planner_routes.setup_planner_routes()
    create = _endpoint(router, "/items", "POST")
    changes = _endpoint(router, "/items/changes", "GET")
    a = create(_req("alice"), body=planner_routes.PlanItemCreate(title="A"))
    out = changes(_req("alice"), since=a["seq"])   # nothing newer
    assert out["items"] == []
    assert out["cursor"] == a["seq"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_planner_changes.py -q`
Expected: FAIL (route still takes an ISO `since`; `cursor` is an ISO string; uses `updated_at`).

- [ ] **Step 3: Rewrite `list_changes` to use the `seq` cursor**

Replace the `list_changes` body in `routes/planner_routes.py` with:

```python
    # --- CHANGES (delta sync): rows with seq > since, incl. tombstones ---
    @router.get("/items/changes")
    def list_changes(request: Request, since: Optional[int] = None):
        user = _owner(request, TODO_READ_SCOPES)
        db = SessionLocal()
        try:
            q = db.query(PlanItem)
            if user is not None:
                q = q.filter(PlanItem.owner == user)
            if since is not None:
                q = q.filter(PlanItem.seq > since)        # exclusive; seq is unique-per-owner monotonic
            items = q.order_by(PlanItem.seq.asc()).all()
            dicts = [_item_to_dict(it) for it in items]
            cursor = max((it.seq for it in items), default=(since or 0))
            return {"items": dicts, "cursor": cursor}
        finally:
            db.close()
```

- [ ] **Step 4: Remove the now-dead `_parse_since` and unused import**

Delete the module-level `_parse_since` function. Then check whether `timezone` (from `from datetime import datetime, timezone`) is still used anywhere:
`grep -n "timezone" routes/planner_routes.py` — if `_parse_since` was its only user, change the import to `from datetime import datetime` (keep `datetime` if still referenced elsewhere; otherwise remove the whole line). Confirm with `./venv/bin/python -m compileall -q routes`.

- [ ] **Step 5: Run changes tests**

Run: `./venv/bin/python -m pytest tests/test_planner_changes.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Full 1b-0 + hub regression + compile**

Run: `./venv/bin/python -m pytest tests/test_planner_client_id.py tests/test_planner_seq.py tests/test_hub_seq_migration.py tests/test_planner_changes.py tests/test_planner_crud.py tests/test_planner_capture.py tests/test_planner_patch.py tests/test_planner_reorder.py tests/test_planner_softdelete.py tests/test_planner_owner_scope.py tests/test_hub_deleted_at_migration.py -q && ./venv/bin/python -m compileall -q routes core src`
Expected: all PASS; compile clean.

- [ ] **Step 7: Commit**

```bash
git add routes/planner_routes.py tests/test_planner_changes.py
git commit -m "feat(hub): switch /items/changes to monotonic seq cursor"
```

---

## Final verification (after all tasks)

- [ ] **Live smoke (backend on :7860, restart uvicorn first — no auto-reload):** with a `tide` token: create an item with a client-supplied `id` (verify the returned id matches + a re-POST is idempotent); `GET /items/changes` returns integer `cursor`; mutate the item; `GET /items/changes?since=<cursor>` returns it with a higher `seq`; delete it; `?since=` shows the tombstone (`deleted:true`) at a higher `seq`.

---

## Self-Review

**Spec coverage (1b-0 in the design doc §"1b-0"):**
- "accept client-supplied `id` on create (upsert-on-PK / idempotent)" → Task 1. ✓
- "add a monotonic per-owner `seq` column ... assigned on every write ... guarded migration + backfill" → Task 2 (all 8 write sites enumerated incl. background `enrich_item`). ✓
- "switch `?since=` to the `seq` cursor (`updated_at` stays the LWW tie-breaker)" → Task 3 (route switched; `updated_at` left untouched in `_item_to_dict`). ✓

**Placeholder scan:** none. The one judgment step (Task 3 Step 4: is `timezone` still used?) is a concrete grep-and-decide, not a vague directive.

**Type consistency:** `seq: Integer` (model) ↔ `_next_seq/_next_seq_global -> int` ↔ `since: Optional[int]` ↔ `cursor` int. `_item_to_dict["seq"]` matches the tests. `PlanItemCreate.id: Optional[str]` matches `id=body.id or str(uuid.uuid4())`.

**Watch-item:** every `db.commit()` in a write route must be preceded by a `seq` assignment — the Task 2 Step 6 grep is the guard against a missed site (a missed site = a row that never appears in the change feed).
