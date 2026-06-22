# Slice 1a — Odysseus client API readiness (REST sync)

**Date:** 2026-06-22
**Status:** Approved (brainstorm), ready for implementation plan
**Strategic context:** `docs/ai-context/2026-06-22-life-os-direction-decision-record.md` (Odysseus = brain + web back-office; native Tide = the macOS+iOS client). This is the **no-regret first build** named in that record's plan: the in-repo Python that makes the REST API ready for Tide's sync. The MCP/agent surface (record's part 4) is deferred to its own spec.

## Goal

Make the Odysseus REST API ready for **Tide's incremental, offline-capable sync** over the task graph. Three coupled capabilities:

1. **Token scopes** so a Tide-minted API token can reach People/Areas (today only `todos:*` is scope-aware).
2. **PATCH + reorder** for `PlanItem` (today: create/complete/plan only — no partial update, no reorder).
3. **`?since=` delta fetch + soft-delete tombstones** on `PlanItem` (today: no delta endpoint, no delete path, no `deleted_at`).

**Consumer:** Tide (the SwiftUI client). The MCP/agent surface is explicitly out of scope (separate spec when agent work begins).

### Why these three, together

They are the single coherent contract "the REST API Tide syncs against." Scopes unlock the read surface; PATCH/reorder are the write surface; `?since=`/tombstones are the delta surface. Splitting them would ship a half-usable sync.

## Grounding (verified against both repos this session)

**Odysseus side** (`/Users/kganpat/Projects/odysseus`):
- `core/hub_models.py` — `PlanItem` already has `updated_at` via `TimestampMixin` (`onupdate=utcnow_naive`). `ordinal` is `Integer`. **No `deleted_at`.**
- `routes/planner_routes.py` — already has the scope-aware `_owner(request, allowed)` gate (`todos:read`/`todos:write`), `_get_owned`, `_next_ordinal(ORDINAL_GAP=1024)`, `_item_to_dict`, and routes: GET `/items`, GET `/items/{id}`, POST `/items`, POST `/items/{id}/complete`, POST `/capture`, POST `/items/{id}/plan`. **No PATCH, no reorder, no DELETE.**
- `routes/people_routes.py`, `routes/area_routes.py` — use the **plain** `_owner(request) = require_user(request) or None`; **not token-scope aware**.
- `routes/api_token_routes.py` — `ALLOWED_SCOPES` includes `todos:*`, `documents:*`, `email:*`, `calendar:*`, `memory:*`, `cookbook:*` but **no `people:*`/`areas:*`**. `_normalize_scopes` has an `ensure_before(write, read)` helper; `TOKEN_PROFILES` has `chat`, `codex_todos`, etc.

**Tide side** (`/Users/kganpat/Projects/tide`):
- `Sources/TideCore/Ordinal.swift` — `Ordinal.between(a: Double?, b: Double?) -> Double`, gap `1024.0`: `(nil,nil)→0`, `(nil,b)→b-gap`, `(a,nil)→a+gap`, `(a,b)→(a+b)/2`. TODO: rebalance a bucket if a midpoint gap collapses (~50 inserts into one gap before Double precision exhausts).
- `Sources/TideCore/Models/TideTask.swift` — `sortIndex: Double`, `updatedAt: Date`, `id: UUID`, plus `completed/completedAt`, `plannedDay/dueDate/scheduledStart/durationMinutes/estimateMinutes`, `priorityRaw`, and (future) `backlogHorizonRaw`/`preferredTimeOfDayRaw`. No client-side tombstone field — the **server** tombstone tells Tide to delete locally.
- `Sources/TideCore/Store/TaskService.swift` — `reorder(_:between:and:)` and `moveToDay(...)` call `Ordinal.between` and stamp `updatedAt = .now`.

## Design

### 1. Token scopes — `routes/api_token_routes.py` + people/area routes

- Add to `ALLOWED_SCOPES`: `people:read`, `people:write`, `areas:read`, `areas:write`.
- Add `ensure_before` pairs (write implies read): `people:write→people:read`, `areas:write→areas:read`.
- Add a profile to `TOKEN_PROFILES`:
  `"tide": ["todos:read","todos:write","people:read","people:write","areas:read","areas:write"]`
  so minting a Tide token is one call.
- Wire the scope-aware gate into the graph routes. Copy `planner_routes._owner(request, allowed)` into `people_routes.py` and `area_routes.py`, defining module constants:
  - `PEOPLE_READ_SCOPES = {"people:read","people:write"}`, `PEOPLE_WRITE_SCOPES = {"people:write"}`
  - `AREA_READ_SCOPES = {"areas:read","areas:write"}`, `AREA_WRITE_SCOPES = {"areas:write"}`
  - Read endpoints (`GET ""`, `GET /{id}`, `GET /{id}/page`) gate on READ; mutating endpoints (`POST`, `PUT`, `DELETE`) on WRITE.
- **Browser sessions are unchanged** — the token branch only executes when `request.state.api_token` is set; otherwise it falls through to `require_user(request) or None` exactly as today. The single-user/no-auth localhost `None` owner path is preserved (the documented deliberate convention; the sec-scan's "fail-open IDOR" flag on this pattern is acknowledged and not exploitable under auth).

### 2. Reorder & ordinal type — `core/hub_models.py` + `routes/planner_routes.py`

- `PlanItem.ordinal`: declared type **`Integer` → `Float`** so the ORM round-trips Tide's `Double sortIndex`. `ORDINAL_GAP` becomes `1024.0`.
- **No SQLite ALTER/migration required.** SQLite preserves REAL values even in an integer-affinity column (a non-lossless float like `1024.5` is stored as REAL, not truncated) and compares INTEGER/REAL numerically. Existing integer ordinals remain valid and sort correctly alongside new fractional midpoints. Only the SQLAlchemy declaration changes, so the ORM accepts/returns floats. (Contrast with §4's `deleted_at`, which is a genuinely new column and *does* need a migration.)
- New `POST /items/{id}/reorder` with body `{before_id?: str, after_id?: str}`:
  - Owner-gate the target via `_get_owned`. Resolve `before_id`/`after_id` (each owner-scoped, tombstone-excluded); a provided id that isn't owned/live → 404.
  - Compute `item.ordinal = _ordinal_between(before.ordinal, after.ordinal)` where `_ordinal_between` mirrors `Ordinal.between` **exactly**: `(None,None)→0.0`, `(None,b)→b-1024.0`, `(a,None)→a+1024.0`, `(a,b)→(a+b)/2`.
  - Commit (auto-bumps `updated_at`), return `_item_to_dict`.
  - Carry the same rebalance TODO as TideCore (Double precision exhausts after ~50 inserts into one gap; rebalance is a future concern, not in 1a).

### 3. PATCH — `routes/planner_routes.py`

- `PATCH /items/{id}` with a Pydantic `PlanItemPatch` where **every field is Optional**: `title, notes, planned_day, due_date, planned_start, priority, status, estimate_minutes, project_id, ordinal, source_note_id, source_event_id, person_id`.
- Apply via `body.model_dump(exclude_unset=True)` so **absent ≠ explicit null** — a client that sends `{"planned_day": null}` clears the day; one that omits the key leaves it untouched. This is required for Tide to deliberately clear fields.
- Validation: clamp `priority` to `{none,normal,important,urgent}` and `status` to `{open,in_progress,done,cancelled}` (reject/normalize unknown — reuse the clamping spirit of `planner_ai.coerce_capture`, inline is fine). Owner-gate via `_get_owned`; a tombstoned row → 404.
- `updated_at` auto-bumps via `onupdate`. Existing `/plan` and `/complete` endpoints stay (back-compat; web UI uses them).

### 4. Soft-delete + DELETE — `core/hub_models.py` + migration + all live-read sites

- Add `deleted_at = Column(DateTime, nullable=True, index=True)` to `PlanItem`.
- **Guarded migration** `_migrate_add_plan_item_deleted_at_column()` wired into `init_db()` (the codebase convention — `create_all` does NOT add columns to an existing table; this is the exact lesson from the `/today` session's `planned_start` migration). Mirror that helper's shape (PRAGMA table_info check → guarded `ALTER TABLE plan_items ADD COLUMN deleted_at DATETIME`).
- New `DELETE /items/{id}` → sets `deleted_at = utcnow_naive()` (soft). Owner-gated. Returns the tombstone dict (or 204 — implementer's choice, default to returning the dict for symmetry).
- **Exclude tombstones (`deleted_at IS NULL`) at every live-read site** (enumerated this session):
  - `routes/planner_routes.py`: `_get_owned`, `_next_ordinal`, `list_items`.
  - `src/today.py`: `day_view` (the `query(PlanItem).filter(status=="open")`).
  - `routes/today_routes.py`: the task-detail `query(PlanItem).filter(id==...)`.
  - `routes/people_routes.py`: person-page open-tasks query.
  - `routes/area_routes.py`: area-page open-tasks query.
  - `src/meeting_notes.py`: the dup-check query and the `func.max(ordinal)` query.
- `_item_to_dict` gains `"deleted": item.deleted_at is not None` and `"deleted_at": iso|null`.

### 5. Delta endpoint — `routes/planner_routes.py`

- New **`GET /items/changes?since=<iso8601>`** (kept separate from `GET /items` so the web planner's live-only semantics are untouched). Gate on `TODO_READ_SCOPES`.
- Returns `{"items": [...], "cursor": "<iso8601>"}`.
- **Includes tombstones** (rows with `deleted_at` set) so the client learns deletions.
- `since` omitted → full set (initial sync). `since` present → `updated_at >= since` (inclusive `>=`; the client upserts by `id`, making re-fetched boundary rows idempotent and eliminating same-instant boundary-miss).
- `cursor` = `max(updated_at)` across returned rows, else the server's `utcnow_naive()` when the batch is empty. The client stores `cursor` and sends it as the next `since`.
- Parse `since` as ISO-8601 → naive UTC (to match `utcnow_naive` stored values); malformed → 400.

## Data flow (Tide sync)

1. User mints a `tide`-profile token (todos+people+areas r/w).
2. **Initial sync:** `GET /api/planner/items/changes` → all live items + `cursor`. Tide upserts, stores `cursor`.
3. **On wake / periodic:** `GET /api/planner/items/changes?since=<cursor>` → changed items + tombstones. Tide upserts live rows, deletes tombstoned rows locally, advances `cursor`.
4. **Edits:** Tide → `PATCH /items/{id}` (fields), `POST /items/{id}/reorder` (before/after ids), `DELETE /items/{id}` (soft). Each bumps `updated_at`, so it flows back on the next `changes` poll (and to other devices).
5. **Conflict policy:** last-writer-wins by `updated_at`. Single user, few devices — full CRDT/OT is out of scope.

## Testing

Mirror `tests/test_planner_*.py` (real in-memory SQLite engine + monkeypatched `SessionLocal`; endpoints invoked directly with a `SimpleNamespace` request, per `tests/test_planner_owner_scope.py`):

- `test_planner_patch.py` — partial update applies only sent keys; `{"planned_day": null}` clears vs omitted leaves intact; enum clamp; owner 404; tombstoned row 404.
- `test_planner_reorder.py` — all four `_ordinal_between` cases (top/bottom/middle/empty); result is a float; unknown/cross-owner neighbor → 404; `updated_at` bumped.
- `test_planner_softdelete.py` — DELETE sets `deleted_at`; row excluded from `list_items`, `get_item`, `today.day_view`, people-page, area-page; tombstone NOT excluded from `/changes`.
- `test_planner_changes.py` — `since` inclusive boundary; `cursor` echoes max `updated_at` (server-now when empty); tombstones present; malformed `since` → 400; no-`since` returns full set.
- `test_planner_migration.py` (or extend existing migration test) — `deleted_at` added to a pre-existing `plan_items` table.
- `test_people_owner_scope.py`, `test_areas_owner_scope.py` — mirror `test_planner_owner_scope.py`: bearer token with the right scope resolves to owner; missing scope → 403; browser session unchanged.

Plus `compileall` + the existing hub test suite must stay green.

## Out of scope (explicit)

- **MCP tools** exposing the graph (record's part 4) — its own spec when agent work begins.
- **People/Areas incremental sync** (`?since=`/soft-delete on Person/Area) — only **scopes** in 1a; their delta sync lands with Tide's People/Areas surfaces (slice 3).
- **Horizon / preferredTimeOfDay** fields on `PlanItem` (Tide models them; Odysseus adds them in a later slice).
- **Tombstone GC/purge** — keep tombstones indefinitely for now; add a purge if the table grows.
- **Conflict resolution beyond last-writer-wins.**

## Risks / watch-items

- The `ordinal` Integer→Float "no migration" claim rests on SQLite's type affinity. Verify in a test that a fractional ordinal written to a pre-existing integer-affinity column round-trips as a float and sorts correctly.
- Every new live-read filter is a place a future query could forget the tombstone exclusion. Consider whether a small shared helper (e.g. `_live(q)`) is worth it, or keep the filters explicit per the codebase's current style — decide during planning.
- `since` timezone handling: stored values are naive UTC (`utcnow_naive`); the parser must normalize incoming ISO-8601 (with or without `Z`/offset) to naive UTC or the comparison silently drifts.
