# Slice 1 — Link sync spine: soft-delete + `/links/changes`

**Date:** 2026-06-24
**Repo:** `~/Projects/odysseus`, branch `feat/link-sync-spine` (off `dev`)
**Program:** Tide foundation-parity roadmap, slice 1. **Highest-risk server change** (everything aggregates through `Link`).
**Status:** Design approved (program `/goal`).

## 1. Purpose
Make the polymorphic `Link` table **tombstone-capable** and expose it as a **seq-cursor delta feed** so edges sync to Tide exactly like `PlanItem` does. The trap: `Link` is **hard-deleted** today (`remove_links_for`/`set_area`/`clear_area` use bulk `.delete`), so Tide could never learn an edge vanished; and a soft-deleted edge re-added via `set_area`'s delete-then-add would violate `uq_links_edge` unless the tombstoned row is **revived**.

**Out of scope:** any Tide change; any new node type; `links:write` from clients (links are server-side side effects of people/area/note writes — Tide gets `links:read` only).

## 2. Mirror the existing PlanItem contract
`PlanItem` already has this exact shape — mirror it for `Link`:
- `next_plan_item_seq(db, owner)` (`core/hub_models.py:238`, `max(seq).filter(owner)+1`) → add `next_link_seq(db, owner)`.
- `_migrate_add_plan_item_{seq,deleted_at}_column()` (guarded ALTER) registered in `run_hub_migrations()` (`:247`) → add `_migrate_add_link_{seq,deleted_at}_column()` + register.
- `GET /items/changes?since=` (`routes/planner_routes.py:242`: `seq > since`, `order_by(seq.asc())`, `cursor = max(seq, default=since or 0)`, owner-filtered) → mirror for `/api/links/changes`.

## 3. Model + migration
`core/hub_models.py` `Link`: add `seq = Column(Integer, index=True)` and `deleted_at = Column(DateTime, nullable=True, index=True)`. Add guarded `_migrate_add_link_seq_column()` + `_migrate_add_link_deleted_at_column()` (copy the PlanItem helpers' guarded-ALTER form; **no backfill needed** — existing links get `seq=NULL`/`deleted_at=NULL`; a NULL seq simply never appears in a `seq > since` feed until next touched, acceptable since Tide starts from cursor 0 and these are pre-existing edges — BUT to be safe, **backfill existing live links with per-owner sequential seq** mirroring `_migrate_add_plan_item_seq_column`'s backfill so the initial full pull sees them). Register both in `run_hub_migrations()`.

## 4. `src/links.py` rewrite (the surgery)
A new owner-scoped `_now()`/seq stamp on every write. **All Link mutations go through this file**, so centralizing here covers every writer (meeting_notes, people/area routes).

- **`add_link(...)`** — find the existing edge **regardless of `deleted_at`** (the unique tuple). Three cases:
  - exists & live (`deleted_at IS NULL`) → return it unchanged (idempotent).
  - exists & tombstoned → **REVIVE**: `deleted_at = None`, `seq = next_link_seq(db, owner)`, commit, return. (This is what makes `set_area` delete-then-add safe against `uq_links_edge`.)
  - not exists → create with `id`, `seq = next_link_seq`, `deleted_at = None`, commit, return.
- **`links_from` / `links_to`** — add `.filter(Link.deleted_at.is_(None))` so reverse-Link aggregation never sees tombstones.
- **`remove_links_for(db, owner, node_type, node_id)`** — replace the bulk `.delete()` with **row-enumeration**: fetch the live edges touching the node (either end, `deleted_at IS NULL`), and for EACH set `deleted_at = utcnow()` + `seq = next_link_seq(db, owner)`; commit once. Return the count.
- **`set_area(...)`** — replace the bulk delete of the old `in_area` edge with the same row-enumerated soft-delete (only live `in_area` edges from the node), commit, then `add_link(... REL_IN_AREA ...)` (which revives or creates). Falsy `area_id` → soft-delete only, return None.
- **`clear_area(...)`** — row-enumerated soft-delete of live `in_area` edges; return count.

**Invariant:** `add_link` idempotency, `links_from/to`, and the unique-edge assumption all treat a tombstoned row as "absent for reads, revivable for writes." Only ONE physical row per edge tuple ever exists (live or tombstoned) — revive, never re-insert.

## 5. The `/links/changes` endpoint
New `routes/link_routes.py` `setup_link_routes()` (mirror `people_routes.py`'s `_owner(request, allowed)` scope gate), registered in `app.py` next to `setup_people_routes()`:
- `GET /api/links/changes?since=<int>` → `{links: [...], cursor}`; gate on `links:read` (read scope set). Query: owner-filter, `seq > since` (when `since` given), `seq IS NOT NULL`, `order_by(seq.asc())`; **include tombstones** (do NOT filter `deleted_at` here — the client needs to learn deletes). `cursor = max(seq, default=since or 0)`.
- `_link_to_dict(l)` → `{id, from_type, from_id, rel, to_type, to_id, seq, deleted: l.deleted_at is not None, deleted_at, updated_at}`.

## 6. Scopes
`routes/api_token_routes.py`: add `"links:read"` to `ALLOWED_SCOPES`; add `"links:read"` to the `tide` profile (`TOKEN_PROFILES["tide"]`). (`links:write` not added — no client writes.)

## 7. Testing
- **Unit (pytest, mirror `tests/test_*` hub tests):**
  - `add_link` idempotency (live edge returns same row, no dup); **revive** (tombstone an edge, `add_link` the same tuple → same `id`, `deleted_at` cleared, `seq` advanced, NO `uq_links_edge` IntegrityError).
  - `set_area` reassign: A→area1 then A→area2 → the area1 `in_area` edge is tombstoned (deleted_at set, seq advanced), the area2 edge live; re-assign back to area1 → revives the original row (no uq violation).
  - `remove_links_for` soft-deletes every touching edge (both ends), each with a fresh seq; `links_from/to` no longer return them.
  - `/links/changes?since=0` returns all live + tombstoned edges with monotonic seq; `?since=<n>` is exclusive; cursor = max seq.
  - reverse-Link reads (`links_from/to`) exclude tombstones — confirm `person_page`/`area_page` aggregation unaffected.
  - **ALL-writers-set-seq:** a meeting-note save (`save_meeting_note`/`promote_action_item`) produces edges that appear in `/links/changes` with seq set.
- **Real test (live `:7860`, the user-trust bar):** restart uvicorn (NO auto-reload — must restart after backend changes); via the API: create a meeting note wiring `note_of`/`about`/`attended_by`/`in_area`, `GET /api/links/changes?since=0` → all edges appear with monotonic seq; reassign a person's area → old `in_area` appears as a tombstone (`deleted:true`, seq advanced) + new edge live, NO error; delete a person → `remove_links_for` tombstones every touching edge; re-open the web People/Area pages → reverse-Link aggregation still renders (tombstone filter didn't break reads).
- Compile clean (`python -m compileall -q core routes src`); existing hub tests stay green.

## 8. Files
- `core/hub_models.py` — Link `seq`+`deleted_at`; `next_link_seq`; `_migrate_add_link_*`; register in `run_hub_migrations`.
- `src/links.py` — the soft-delete + revive rewrite.
- `routes/link_routes.py` — **new**; `/api/links/changes`.
- `app.py` — register `setup_link_routes()`.
- `routes/api_token_routes.py` — `links:read` scope + tide profile.
- `tests/test_link_sync.py` — **new** unit tests.
