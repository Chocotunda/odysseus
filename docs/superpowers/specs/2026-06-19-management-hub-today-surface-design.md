# Management Hub — `/today` daily day-planner surface (design)

**Date**: 2026-06-19
**Status**: Approved (brainstorm) → ready for implementation plan
**Slice**: 3rd management-hub vertical slice (after tracer-slice + Area backbone). One spec, build sequenced in two phases.

---

## 0. Context

The management hub is a native connected entity-graph (`Link` spine + fixed node types: People/Tasks/Notes/Meetings/Areas/…). Two slices have shipped to `dev`:

1. **Tracer** — `Link` edge, `Person` node, meeting-notes → action-item → linked `PlanItem`, reverse-`Link` entity pages.
2. **Area backbone** — `Area` node, `set_area` single-primary edge, Area dashboard (reverse-`Link` aggregation partitioned by `from_type`).

This slice adds the **daily execution lens**: a new top-level `/today` page that aggregates the day's `PlanItem` tasks and `CalendarEvent` meetings into a planner, with the Area/People/Note graph context carried through. It is a *view/extension on the existing spine* — the highest-emotional-payoff next surface (Sunsama/Routine inspiration).

**`/planner` vs `/today`**: `/planner` stays the capture-and-groom backlog list. `/today` is where you run a single day. Plan in `/planner`, do your day in `/today`.

---

## 1. Goal & shape

A new `/today` page = a **two-view day planner** over **one shared day-model**, with date navigation (open on today; ◀ ▶ / picker to plan tomorrow the night before or review yesterday).

- **Overview view** — grouped buckets in priority order: **Overdue → Meetings (time-sorted) → Today's tasks**, plus a capacity total. The quick "what's my day" scan.
- **Timeline view** — hour-by-hour day. Meetings placed at their times; an **Unscheduled rail** holds today's untimed tasks + overdue. **Drag a task onto the timeline to time-block it** (sets a start time); drag within the timeline to move it.
- **Detail panel** — click any item → side panel. Read-focused, with the graph context (linked People / Notes / Area). Tasks get a **Complete** quick action inline. Editing tasks stays in `/planner`; editing events stays in the calendar.

### Key decisions (from brainstorm)
1. **Build all of it, sequenced** — this is one cohesive feature (one spec). Read foundation first, drag-interactivity second (see §6).
2. **Time-blocks are internal-only** — a private planning layer on the task. **Never written back to iCloud / CalDAV.** Your real calendar stays untouched.
3. **New top-level `/today` page** — its own nav button + slash command; `/planner` unchanged.
4. **Detail = read + quick-actions** (complete a task in-flow); no full inline editor.
5. **Date-parameterized** — `day` is a `YYYY-MM-DD` string param; defaults to server-today.

### Out of scope (explicitly deferred — captured so they're not lost)
- **Horizon planning** (Week/Month/Quarter/Year/Someday) — a future slice; needs a new `PlanItem.horizon` field + a planning rail. Not in this spec.
- **Drag-to-reorder the backlog**, capacity-vs-calendar-busy time math, resizable blocks (duration comes from `estimate_minutes` for now).
- Writing time-blocks out as real calendar events (decided against).
- Area *filtering* of the day (we show Area labels; filtering is later).
- Full inline editing of tasks/events from `/today`.

---

## 2. Data model

One new field on `PlanItem`, auto-created via `create_all` (no migration — consistent with prior slices):

- `PlanItem.planned_start` — nullable `String`, `"HH:MM"` **local** time. This **is** the time-block start. Mirrors the existing `planned_day` (`YYYY-MM-DD` local string) convention so day/time equality stays a pure string compare regardless of server tz.
  - **Duration** = `estimate_minutes` (existing field), defaulting to **30** when unset. (Resizable blocks deferred.)
  - A task is "time-blocked" iff `planned_start` is non-null **and** `planned_day == day`.

No new `Link` edges. Areas are resolved per item via the existing reverse-`Link` (`in_area`) lookup, reusing the Area-dashboard pattern.

---

## 3. Backend

### `src/today.py` — the day model (pure logic, owner-scoped)
`day_view(db, owner, day, *, today=None) -> dict` gathers everything for `day`:

- **Meetings** — `CalendarEvent` for calendars owned by `owner` that fall on `day`. **Reuse the calendar feature's existing range logic**: the SQL overlap filter from `list_events` (`dtstart < end AND dtend > start`) **plus `_expand_rrule(ev, start_dt, end_dt)`** (module-level helper in `routes/calendar_routes.py`) so **recurring meetings (1:1s, standups) correctly land on the day**. Day bounds are the local `[day 00:00, day+1 00:00)` window; honor `is_utc` exactly as the calendar serializer does (Z-suffix on output). All-day events separated from timed.
- **Scheduled tasks** — `PlanItem` where `planned_day == day`, `status == "open"`, `planned_start` non-null; ordered by `planned_start` then `ordinal`.
- **Unscheduled (today) tasks** — `planned_day == day`, `status == "open"`, `planned_start` null; ordered by `ordinal`.
- **Overdue tasks** — `due_date < today` (string compare), `status == "open"`; only surfaced when `day == today`. Ordered by `due_date` then `ordinal`.
- **Area per item** — batch reverse-`Link` `in_area` lookup for the day's tasks + meetings (mirror `area_page`'s batched approach); attach `{area_id, area_name, area_color}`.
- **Capacity** — sum of `estimate_minutes` over the day's tasks (scheduled + unscheduled), defaulting unset to 30. Returned as `capacity_minutes`.

Returns a dict: `{ day, is_today, meetings: [...], scheduled_tasks: [...], unscheduled_tasks: [...], overdue_tasks: [...], capacity_minutes }`. Each task carries `id,title,planned_start,due_date,priority,estimate_minutes,status,area_*`; each meeting carries `uid,summary,dtstart,dtend,all_day,is_utc,location,area_*`.

**Owner scoping**: every query filtered by `owner`; meetings via `CalendarCal.owner`. Same `_owner = require_user or None` + `owner is not None and …` convention as the other hub routes (the codebase-wide pattern — the sec-scan "fail-open" flag on it is a known false positive; do not special-case).

### `routes/today_routes.py` (`/api/today`) — HTTP-thin, mirrors `area_routes.py`
- `GET /api/today?day=YYYY-MM-DD` → `day_view(...)`. `day` optional, defaults to server-today (computed as a local date string). Validates the date format; 400 on garbage.
- `GET /api/today/task/{id}` → task detail for the panel: the `PlanItem` fields **plus** linked People (`about`), source Note (`from_note`), and Area, resolved via `links_from`/`links_to`. Cross-owner → 404.
- `POST /api/today/task/{id}/schedule` body `{planned_day: str, planned_start: str|null}` → set / move / clear a time-block. `planned_start=null` returns the task to the Unscheduled rail; a different `planned_day` moves it to another day. Validates `HH:MM` / `YYYY-MM-DD`. Cross-owner → 404.
- **Complete** is **not re-implemented** — the frontend calls the existing `POST /api/planner/items/{id}/complete`.

Registered in `app.py` via `setup_today_routes()` next to the other hub routers, plus a `/today` SPA shell route alongside `/planner` and `/areas`.

---

## 4. Frontend (`static/`)

- **`js/today.js`** — injected-panel page (same pattern as `planner.js`/`areas.js`):
  - **Header**: date nav (◀ `Today` ▶ + native date input) and an **Overview / Timeline** view toggle.
  - **Overview**: the three grouped buckets (Overdue / Meetings / Tasks) + a capacity line. Counts in headers. Empty-state per bucket.
  - **Timeline**: hour rows over a fixed window (~6am–10pm), meetings + time-blocked tasks absolutely-positioned by start time and `estimate_minutes` height; an **Unscheduled rail** beside it with today's untimed tasks + overdue. (Phase 2) drag a rail item onto an hour → `POST …/schedule`; drag a block to move; drop back on the rail → clear `planned_start`.
  - **Detail panel**: click an item → side panel with full fields + linked People/Notes/Area; tasks show **Complete** (→ planner complete endpoint, then refresh).
  - All untrusted strings escaped via the existing `_esc`; reuse CSS vars (`--red`/`--fg`/`--bg`/`--card`/`--border`), inline monochrome SVG, **no emoji**, Fira Code.
- **`index.html`** — nav `tool-today-btn` + favicon shape + title entry.
- **`app.js`** — import + button wiring + `/today` route.
- **`js/slashCommands.js`** — `/today` command + `_cmdOpen` target.
- **`style.css`** — `.today-*` block (timeline grid, hour rows, blocks, rail, detail panel) reusing the existing palette.

---

## 5. Reuse (do not reinvent)
- **Recurring-meeting expansion + day overlap** → `_expand_rrule` + the `list_events` SQL overlap pattern in `routes/calendar_routes.py`.
- **Event serialization / `is_utc` Z-suffix** → mirror `_event_to_dict`.
- **Task complete** → existing `/api/planner/items/{id}/complete`.
- **Owner gate + cross-owner 404** → `area_routes.py` / `people_routes.py` pattern.
- **Reverse-`Link` Area aggregation (batched)** → `area_page`.

---

## 6. Build sequence (sequenced in the implementation plan; one spec)

**Phase 1 — read foundation (ships value on its own):**
- `PlanItem.planned_start` field.
- `src/today.py` `day_view` (meetings incl. rrule expansion, scheduled/unscheduled/overdue tasks, areas, capacity).
- `GET /api/today` + `GET /api/today/task/{id}` + `/today` shell route + nav/slash wiring.
- `today.js` rendering **both views** (timeline read-only: shows meetings + any existing blocks + the unscheduled rail) + detail panels + day-nav + complete action.

**Phase 2 — interactivity:**
- `POST /api/today/task/{id}/schedule`.
- Drag-to-timeblock (rail → timeline), drag-to-move, drag-to-unschedule in `today.js`.

---

## 7. Testing

- **`tests/test_today.py`** (unit, real in-memory SQLite + monkeypatched `SessionLocal`, mirroring `test_areas.py`): `day_view` returns meetings on the day, planned/unscheduled/overdue tasks partitioned correctly, recurring meeting lands on the day, capacity sums (incl. 30-min default), Area resolved per item, **owner-scope isolation** (another owner's tasks/meetings never leak).
- **`tests/test_today_routes.py`** (mirroring `test_people_routes.py`): `GET /api/today` default-day + explicit-day, bad-date 400, task-detail includes linked people/note/area, `schedule` sets/moves/clears `planned_start`, cross-owner `GET`/`schedule`/detail → 404.
- **Static checks**: `compileall` clean; `node --check` on every changed JS.
- **Live verify (Chrome, `:7860`)**: open `/today` against the real iCloud events + the Wiggert/Work demo data — overview buckets correct, timeline places a real meeting at its time, drag a task to time-block it, click event/task → detail panel, complete a task, nav to tomorrow. Screenshot.

---

## 8. Gotchas / notes for the implementer
- **Recurring events are the trap** — a daily standup has one DB row with one `dtstart`; without `_expand_rrule` it only ever shows on its original day. This is why we reuse the calendar expansion rather than a naive `dtstart`-on-day SQL filter.
- **Timezone**: stay on the established string-equality convention for tasks (`planned_day`/`planned_start`/`due_date` are local strings). Meetings need real datetime range math — that's the one place to use the calendar helpers, honoring `is_utc`.
- **Duration default** lives in one constant; unset `estimate_minutes` → 30 for both capacity and block height.
- **No migration** — `planned_start` appears via `create_all` on existing rows as NULL (= unscheduled), which is the correct default.
- Backend does **not** auto-reload — restart uvicorn on `:7860` before live verify.
