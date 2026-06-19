# Management Hub — Area/Context Life-OS Backbone Design

**Date:** 2026-06-19
**Status:** Approved (brainstorm) → ready for implementation plan
**Builds on:** `docs/superpowers/specs/2026-06-19-management-hub-tracer-slice-design.md` (the Link graph spine + Person node + meeting-note loop, shipped on `feat/management-hub-tracer-slice`).

---

## 0. Context — why this slice

The tracer slice proved the connective spine: a polymorphic `Link` table + a first-class `Person` node, with a meeting → note → linked-tasks → Person-page resurfacing loop. The next structural need, surfaced by researching the user's real Tana workspace via the connected `tana-local` MCP, is the **Area/Context dimension** — the coarse life-partition the user actually organizes by.

In the user's Tana, a shared **Context/Area** field runs through everything: `Person.Context` = *Work / Personal / Krishna Movements*, `Task.Area` = *Work / Personal*, and it "drives Personal/Work/KM dashboards." Our hub has no equivalent — it is a flat graph. Adding Areas is what turns it from a connected graph into a **work + life + KM life-OS**: every entity belongs to a life-area, and each area has a dashboard aggregating its people, tasks, notes, and meetings.

### Decisions taken in brainstorming
- **Area is a first-class node** (not a coarse enum), so an Area is openable — an **Area dashboard page** that aggregates its members, can later nest Projects, and links via the `Link` table.
- **Single primary Area per node** — a node belongs to exactly one Area (a task is Work *or* Personal, never both). Clean dashboards, simple picker; a node with none is "Unassigned".
- **Membership is a `Link` edge, not a per-node column** — keeps `Link` canonical (consistent with the whole hub), needs zero migrations across the node tables, and the Area dashboard reuses the *already-tested* Person-page aggregation code.
- The Area *node* **supersedes** the harvested Tana `Task.area` / `Person.context` *enums* — we do NOT add those enum columns; the `in_area` edge is the area dimension.

---

## 1. Scope

**The loop (done =):** assign Wiggert and a meeting-note's tasks to **Work** → open the **Work** dashboard → see those people + tasks + notes + meetings aggregated; the **Personal** dashboard shows none of them. The work/life partition made real.

**In scope:**
- `Area` node + table; seed **Work / Personal / Krishna Movements** per owner (editable).
- `in_area` `Link` rel + single-area enforcement (`set_area` / `clear_area` helpers).
- Area CRUD routes + Area dashboard aggregation endpoint.
- An **Area picker** wired into the **Person** create/edit form and the **meeting-note composer** (the note + its promoted tasks inherit the area).
- Frontend: Areas list + Area dashboard page + nav button + routes.

**Deliberately OUT (later slices):**
- Company/Team nodes (Person's other Tana refs).
- Projects-under-Areas and Goals on the dashboard (the dashboard ships with People/Tasks/Notes/Meetings only).
- Threading an Area *filter* into the Planner / email / document lists.
- Multiple Areas per node.
- The Task `horizon` field (separate enrichment).

---

## 2. Data model

Reuses the tracer-slice spine. **No per-node schema changes** — membership is edges.

```
Area:  id, owner, name, color, sort_order, archived       (new table; owner-scoped)
Link:  add rel  REL_IN_AREA = "in_area"   →   <node> —in_area→ Area
```

- **`Area`** subclasses `TimestampMixin, Base`; `id`/`owner` per the standard pattern; `color` reuses a UI CSS-var hex; `sort_order` for list ordering; `archived` boolean. Composite index `ix_areas_owner_archived` on `(owner, archived)`.
- **`set_area(db, owner, node_type, node_id, area_id)`** in `src/links.py` — deletes any existing `in_area` edge from that node, then `add_link(... REL_IN_AREA ... area_id)`. This enforces "exactly one primary Area" at the edge level. **`clear_area(db, owner, node_type, node_id)`** removes it. Both commit.
- **Seeding** — `ensure_seeded_areas(db, owner)` creates Work / Personal / Krishna Movements rows (distinct CSS-var colors) **iff** the owner has zero Area rows. Idempotent; called at the top of the Areas list endpoint.
- **Area dashboard** = `links_to(db, owner, NODE_AREA, area_id, rel=REL_IN_AREA)` partitioned by `from_type` → fetch the People / open Tasks (`status="open"`) / Notes / Meetings. This is the Person-page aggregation pattern keyed on the Area.
- **New node-type constant** `NODE_AREA = "area"` in `src/links.py`.
- **Owner-scoping is a security boundary** — `Area` is a new owner-scoped surface; every query filters by `owner`; the dashboard derives `owner` from the loaded Area (which 404s across owners). P0 isolation tests.

---

## 3. Surfaces (UI)

Reuses tracer-slice patterns (`people.js` panel + `_esc`; the picker shape from `meetingNote.js`; CSS vars `--card`/`--border`/`--fg`/`--red`; inline monochrome SVG, **no emoji**; Fira Code; dark default).

### 3.1 Areas list + dashboard (`static/js/areas.js`, nav `tool-areas-btn`, routes `/areas` + `/areas/{id}`)
- `openAreas()` — the seeded Areas as cards (name + color dot + member counts); each opens the dashboard. Inline "+ Area" form (name + color) POSTs to `/api/areas`.
- `openArea(id)` — header (name, color dot, rename) + four reverse-`Link` sections: **People**, **Open tasks** (overdue-first), **Notes**, **Meetings**. Same render as the Person page, keyed on the Area. *The payoff screen.*

### 3.2 Area picker (reusable widget)
A compact control listing the owner's Areas (each with a color dot) plus "Unassigned". Reused in:
- the **Person** create/edit form — sets the person's Area on save (`set_area`).
- the **meeting-note composer** — one picker that stamps the note **and** every promoted task with the chosen Area.

### 3.3 Color
Each Area carries a CSS-var hex, shown as a dot on its card, in the picker, on the dashboard header, and as a small chip on Person/task rows that have an Area. No new color literals.

---

## 4. AI's role

**None.** Area assignment is explicit and deterministic — a picker, not a model guess. The only smart behavior is deterministic inheritance: a task promoted from a meeting note adopts the note's Area via a `set_area` call. No new AI surface (consistent with the local-LLM-small principle).

---

## 5. Testing

TDD, mirroring `tests/test_people_routes.py` (in-memory SQLite, monkeypatched `SessionLocal`, `SimpleNamespace` request, direct endpoint calls).

- **`test_area_model_crud`** — create / list / rename / archive + **owner isolation P0**.
- **`test_area_seeding`** — first access for an owner with no areas seeds Work/Personal/KM exactly once; a second call adds nothing (idempotent).
- **`test_set_area_is_single`** — assigning a second Area to a node *replaces* the first (exactly one `in_area` edge remains); `clear_area` removes it.
- **`test_area_page_aggregation`** — People / open Tasks / Notes / Meetings linked `in_area` surface on the dashboard, partitioned by `from_type`; **excludes other owners' and other areas' nodes** (P0).
- **`test_meeting_note_stamps_area`** — saving a meeting-note with an `area_id` sets `in_area` on the note and on each promoted task (extends `tests/test_meeting_notes.py`).

**Live verification (Chrome):** assign Wiggert → Work; save a meeting note (with tasks) under Work; open the **Work** dashboard → people/tasks/notes/meetings appear; the **Personal** dashboard is empty. Screenshot.

---

## 6. Files (anticipated)

- `core/database.py` — `Area` model (owner-scoped, `ix_areas_owner_archived`).
- `src/links.py` — `NODE_AREA`, `REL_IN_AREA`, `set_area`, `clear_area`.
- `src/areas.py` (new, small) — `ensure_seeded_areas` + the dashboard aggregation helper (so the route stays thin), OR fold aggregation into the route mirroring `people_routes.person_page`.
- `routes/area_routes.py` (new) — `setup_area_routes()` (`/api/areas`): list (seeds), create, get, update (rename/color/archive), delete, and `/api/areas/{id}/page`. Registered in `app.py`; `/areas` + `/areas/{id}` SPA routes.
- `src/meeting_notes.py` — accept an optional `area_id` in `save_meeting_note`; stamp note + promoted tasks via `set_area`. `routes/meeting_notes_routes.py` — pass `area_id` through.
- `routes/people_routes.py` — accept `area_id` on create/update; stamp via `set_area`; include the person's area in `_person_to_dict` / the page payload.
- `static/js/areas.js` (new), `static/js/areaPicker.js` (new, reusable), `static/js/people.js` + `static/js/meetingNote.js` (wire the picker), `static/index.html` (`tool-areas-btn` + routes + script tags), `static/app.js` (nav + routing), `static/js/slashCommands.js` (`/areas`), `static/style.css` (`.area-*` block).
- `tests/test_areas.py`, additions to `tests/test_people_routes.py` and `tests/test_meeting_notes.py`.

---

## 7. What this unlocks next (out of scope)

Once Areas exist, later slices are views/extensions on top: per-Area filtering in the Planner/Today surface; Company/Team nodes (Person's remaining Tana refs); Projects-under-Areas (PlanProject gains an `in_area` edge, surfaces on the Area dashboard); Goals (period-scoped, per Area); and Sunsama-style channel routing keyed on Area — mirroring how the user's Tana Context already "drives Personal/Work/KM dashboards."
