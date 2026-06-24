# Slice 2 — Person + Area delta contract (seq + tombstone + `/changes`)

**Date:** 2026-06-24
**Repo:** `~/Projects/odysseus`, branch `feat/person-area-delta` (off `dev`)
**Program:** Tide foundation-parity roadmap, slice 2. Depends on slice 1 (merged — `Link` soft-delete + `remove_links_for` is now soft).
**Status:** Design approved (program `/goal`).

## 1. Purpose
Give `Person` and `Area` the same three sync primitives `PlanItem`/`Link` now have — `seq`, `deleted_at`, and a `seq>since` `/changes` feed — so they pull to Tide. **Direct mirror of slice 1**, no revive trap (nodes, not edges). The one gotcha: `ensure_seeded_areas` auto-mints the 3 default areas on a new owner's first `GET /api/areas`, so it MUST stamp `seq` or the seeded areas never reach `/areas/changes`.

**Out of scope:** Tide changes; any new field; `links:write`. Scopes already exist (`people:read`/`areas:read` are in `ALLOWED_SCOPES` + the tide profile) — no scope change needed.

## 2. Model + migrations (mirror slice 1)
`core/hub_models.py`:
- `Person`: add `seq = Column(Integer, index=True)`, `deleted_at = Column(DateTime, nullable=True, index=True)`.
- `Area`: same two columns.
- `next_person_seq(db, owner)` / `next_area_seq(db, owner)` — copy `next_link_seq` (`max(seq).filter(owner)+1`).
- `_migrate_add_person_{seq,deleted_at}_column()` + `_migrate_add_area_{seq,deleted_at}_column()` — copy the slice-1 guarded-ALTER helpers **with the per-owner seq backfill** (`ORDER BY owner, updated_at, id`, counter 1..N per owner) so the initial full pull sees pre-existing people/areas. Register all four in `run_hub_migrations()` (after the link migrations).

## 3. Route changes — `routes/people_routes.py`
- `create_person`: stamp `p.seq = next_person_seq(db, owner)` on the new row (before commit). (set_area already bumps the Link seq via slice 1.)
- `update_person`: stamp `p.seq = next_person_seq(...)` when persisting field changes (name/email/role) so the edit reaches `/changes`.
- `delete_person`: convert from hard-delete to **soft-delete** — set `p.deleted_at = utcnow()` + `p.seq = next_person_seq(...)`, commit (do NOT `db.delete(p)`); keep the existing `remove_links_for(...)` call (now soft from slice 1).
- `_person_to_dict`: add `"seq": p.seq`, `"deleted": p.deleted_at is not None`, `"deleted_at": ...`.
- New `GET /api/people/changes?since=<int>` (gate `people:read`, mirror `/items/changes`): owner-filter, `seq > since`, `seq IS NOT NULL`, `order_by(seq.asc())`, **include tombstones**, `cursor = max(seq, default=since or 0)`; `{people: [...], cursor}`.
- **Live-read sites must exclude tombstones:** the person list (`list_people`) and `person_page` must filter `Person.deleted_at.is_(None)` so a soft-deleted person disappears from reads (it already filters tombstoned Link edges; add the Person-row filter).

## 4. Route changes — `routes/area_routes.py`
- `create_area`: stamp `a.seq = next_area_seq(db, owner)`.
- `update_area`: stamp `a.seq = next_area_seq(...)` on field changes (name/color/sort_order/archived).
- `delete_area`: **soft-delete** (`deleted_at` + `seq`), keep `remove_links_for(... NODE_AREA ...)`.
- **`ensure_seeded_areas` (`src/areas.py`): stamp `seq` on each of the 3 seeded rows** (`next_area_seq(db, owner)` per row) — the critical gotcha; without it the auto-seeded areas are invisible to `/areas/changes`.
- `_area_to_dict`: add `seq`/`deleted`/`deleted_at`.
- New `GET /api/areas/changes?since=` (gate `areas:read`, same shape).
- **Live-read sites exclude tombstones:** `list_areas` and `area_page` filter `Area.deleted_at.is_(None)`.

## 5. Testing
- **Unit (pytest):**
  - `next_person_seq`/`next_area_seq` monotonic per owner.
  - create/update/delete person & area each advance seq and appear in the entity's `/changes`; delete appears as a tombstone (`deleted:true`).
  - **`ensure_seeded_areas` stamps seq:** a fresh owner's first `GET /api/areas` → the 3 seeded areas appear in `/areas/changes?since=0` with `seq > 0` and correct hex colors.
  - `/people/changes` + `/areas/changes`: `seq>since` exclusive, include tombstones, cursor = max.
  - soft-deleted person/area excluded from `list`/`page` reads but present (tombstoned) in `/changes`.
  - existing people/area/meeting-note tests stay green.
- **Real test (live `:7860`):** restart uvicorn (no auto-reload). Mint a tide token. `GET /api/areas/changes?since=0` → the 3 seeded areas with seq>0 + colors. Create/rename/archive an area + create/rename a person via the API → each shows in its `/changes` with advancing seq. Delete one → tombstone in `/changes`, gone from the list. Confirm `/links/changes` (slice 1) still works (set_area on a person still tombstones+revives edges).
- Compile clean; full hub suite green.

## 6. Files
- `core/hub_models.py` — Person/Area seq+deleted_at; `next_person_seq`/`next_area_seq`; 4 migration helpers; register.
- `routes/people_routes.py` — seq stamps, soft-delete, tombstone-filtered reads, `/people/changes`, `_person_to_dict`.
- `routes/area_routes.py` — same for areas, `/areas/changes`.
- `src/areas.py` — `ensure_seeded_areas` stamps seq.
- `tests/test_person_area_sync.py` — **new**.
