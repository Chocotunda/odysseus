# Tide Foundation-Parity Roadmap — bring Tide to the Odysseus custom-workspace foundation (ready for Notes)

**Date:** 2026-06-24
**Status:** ✅ **COMPLETE (2026-06-24).** All 9 slices shipped + merged + **live-verified against the real brain on :7860**. Tide is at foundation parity — the connected graph (People/Areas/Links), calendar visibility, task detail/edit, the /today planner, and the meeting-notes composer all work; the **5-rel spine integration test passes live**; the Notes-ready slot is in place. Ready to start Notes. Executed slice-by-slice via spec→subagent-TDD→review→**real live-backend testing**. See the "Completion" section at the bottom.
**Source:** research workflow `wf_26eb243c-f04` (4 inventory readers + architect synthesis).
**Spans:** `~/Projects/odysseus` (brain/contract) + `~/Projects/tide` (client).

## Goal (user, verbatim)
> "make sure tide app has the same foundation we've customised built… all our custom pages and functionality should be done. all so that we are ready to start adding notes. i also want to be able to see calendar and tasks details too." — and: trust research + real testing, work autonomously.

## Target — "foundation parity, ready for notes"
Tide stops being a tasks-only client and becomes a thin online-first window onto the Odysseus connected graph: the **`Link` spine mirrored on-device** (generic multi-entity sync + `TideLink` + reverse-Link helpers) so every entity page aggregates exactly as the web back-office does; **Person + Area sync** with the same seq-cursor + soft-delete-tombstone contract `PlanItem` has, rendered as real surfaces (not stubs); **real task detail/edit** (Tide currently CANNOT edit a task) exposing all fields + linked Person/Area/source-note, edits via the durable outbox; the **Odysseus CalDAV calendar visible** (read-only windowed pull + event detail + month view); a **/today day-planner** assembled from the synced model; and the **meeting-notes composer** wired as the end-to-end spine integration test (exercises all 5 Link rels in one save) — WITHOUT shipping a Notes editor. After this, a `Note` model + notes UI drops into the existing `Link` table + multi-entity sync + nav with no spine rework.

## Accepted decisions (research-recommended, user-delegated)
1. **Calendar source = hybrid (A):** keep iOS EventKit as a local-device agenda overlay AND pull Odysseus hub meetings by `uid` where the Link graph is involved. Two sources, reconciled in the UI; richer fields, no extra sync.
2. **Today read model = client-side assembly (A):** build `/today` from synced `PlanItem` + the calendar window + area Links via Tide's `AgendaBuilder` (offline-capable), not the server `/api/today`.
3. **Person/Area write direction = read-mostly first:** pull + task/area-assignment writes only; Person/Area create/edit deferred (shrinks the echo surface). Bidirectional CRUD later.

## Slices (dependency-ordered)

| # | Title | Repos | Effort | Depends |
|---|---|---|---|---|
| 0 | Multi-entity sync engine generalization (no new entity) | tide | L | — |
| 1 | Link spine: server soft-delete surgery + `/links/changes` feed | odysseus | L | — |
| 2 | Person + Area server delta contract (seq + tombstone + `/changes`) | odysseus | M | 1 |
| 3 | `TideLink` table + Area sync + Area dashboard surface | tide | L | 0,1,2 |
| 4 | People sync + People list/detail surfaces | tide | L | 3 |
| 5 | Odysseus calendar visibility (read-only windowed pull + event detail + month) | odysseus+tide | L | 0 |
| 6 | Task detail/edit view + linked-context panel | tide+odysseus | L | 4,5 |
| 7 | Today day-planner surface (assembled from synced model) | tide | L | 6 |
| 8 | Meeting-notes composer — spine integration test (no Notes editor) | tide+odysseus | L | 7 |

### Slice details
- **0 — Sync engine generalization (tide).** Replace the `SyncState` `id=1` singleton with a per-entity cursor table (`syncCursors(entity TEXT PK, cursor INT)`; migrate PlanItem→`entity='planItem'`); add an **entity-type column to `SyncOutboxEntry`** (default existing→`'planItem'`) so Person/Area/Link pushes can't collide with task pushes; generalize `OdysseusClient` to a resource-parameterized changes/create/patch/delete (keep a PlanItem facade); make `SyncCoordinator` generic over (DTO, mapping, table) / a per-entity coordinator. **No server change. Regression gate = the just-verified tasks round-trip, zero behavior diff.** Risks: regressing tasks; over-abstraction vs GRDB `@Table`; outbox migration must default `entity='planItem'`.
- **1 — Link soft-delete + `/links/changes` (odysseus).** Add `seq`+`deleted_at` to `Link`; `next_link_seq`; migration. Rewrite `src/links.py`: `remove_links_for`/`set_area`/`clear_area` become **row-enumerated soft-deletes** (set `deleted_at`+per-row seq, NO bulk `.delete`); `add_link`/`links_from`/`links_to` filter `deleted_at IS NULL`; **`add_link` must REVIVE a tombstoned edge** (clear `deleted_at`, new seq) so `set_area`'s delete-then-add doesn't violate `uq_links_edge`. `GET /api/links/changes?since=` (incl. tombstones). Add `links:read` scope + tide profile. **The `uq_links_edge` revive is the central trap.** Every Link writer must bump seq.
- **2 — Person/Area delta contract (odysseus).** `seq`+`deleted_at` on `Person`+`Area`; `next_person_seq`/`next_area_seq`; migrations. `DELETE /people|areas/{id}` → soft-delete (+ now-soft `remove_links_for`). Stamp seq in create/update/delete **and `ensure_seeded_areas`** (auto-mints on first GET — miss it and seeded areas never reach `/changes`). `GET /api/{people,areas}/changes?since=`.
- **3 — TideLink + Area sync + Area dashboard (tide).** `TideLink @Table` + migration + reverse-Link helpers (`linksTo/linksFrom(type,id,rel)`); wire Link + Area onto the slice-0 engine; extend `Area @Table` (archived/sortOrder) + `AreaDTO`; **drop Tide's local-only area seeding**. `MacAreaView` dashboard (reverse-Link people/tasks/notes/meetings + color dots); route `.area(UUID)` to it. First real entity on the spine.
- **4 — People sync + surfaces (tide).** `TidePerson @Table` + `PersonDTO`; Person onto the spine (read-mostly + optional create/edit via the discriminated outbox); tasks/meetings/notes resolved via `TideLink` reverse queries. `MacPeopleView` list + `MacPersonView` detail; sidebar People section + nav. Canonical reverse-Link parity proof.
- **5 — Calendar visibility (odysseus+tide).** Add **`calendar:read` to the tide profile (confirmed absent today)**; reuse `GET /api/calendar/events?start=&end=` (rrule-expanded). Tide: read-only windowed pull cached per visible window (NOT seq model — CalDAV lifecycle incompatible); replace `CalendarStubView` with a month grid + event-detail view; hub meetings resolve by `uid` for graph surfaces; EventKit stays a local overlay.
- **6 — Task detail/edit + linked context (tide+odysseus).** Add `personId/sourceNoteId/sourceEventId/projectId` to `TideTask` + map in `SyncMapping` (dropped today); **widen `patchBody` so status round-trips `in_progress`/`cancelled`** (the known contract debt). `MacTaskDetailView`/iOS `TaskDetailView`: all fields editable via `TaskService` + outbox PATCH; linked Person/Area/source-note by name; complete+schedule. **Tide has NO task editor today.**
- **7 — Today day-planner (tide).** Assemble client-side (decision A) from synced PlanItem + calendar window + area Links via `AgendaBuilder`. `MacTodayView` (Overview: overdue/meetings/tasks + capacity; Timeline: hour grid + drag-to-time-block) reusing `MacCalendarRailView`. Sidebar/tab route.
- **8 — Meeting-notes composer (tide+odysseus).** Reuse `GET /api/meeting-notes/meetings`, `POST /api/meeting-notes`, `/{id}/promote`, `GET /{id}` (poll `ai_enriched`). Minimal `TideNote @Table`+`NoteDTO` (note id holder only). Composer modal: Person/Meeting/Area pickers + action-item checklist + Save-make-tasks → exercises all 5 rels. **The "ready for notes" seam** — forces the minimal Note slot without a Notes editor.

## Cross-cutting (every slice)
- Multi-entity cursor (per-entity keyed table) + **entity-type-discriminated outbox** (confirmed-breaking constraints in slice 0).
- **Echo avoidance per entity** — pulled changes write directly to GRDB, never re-enqueue; Links have a subtler echo (edges are side effects of people/area/meeting-note writes → one action emits across feeds, reconcile client-side without dup).
- **Link soft-delete invariants server-wide:** `add_link` idempotency + `links_from/to` + `uq_links_edge` all filter `deleted_at IS NULL`; `set_area` revives; bulk deletes → row-enumerated per-row-seq updates.
- **ALL-WRITERS-SET-SEQ** incl. non-obvious: `ensure_seeded_areas`, `set_area/clear_area/remove_links_for`, `meeting_notes` promote/save.
- **Nav wired in BOTH roots:** macOS `MacSidebarSelection` enum + `detailView` switch + title AND iOS `TabView` (repurpose Pages/Calendar stubs) — every surface needs both.
- **Schema hygiene:** turn OFF `eraseDatabaseOnSchemaChange` before slice 3 (first real graph data); forward-only migrations.
- Token scopes: `people:*`/`areas:*` already in tide profile; **add `links:read` + `calendar:read`**.

## Definition of done (parity checklist)
Link graph mirrored on-device + generic multi-entity sync; Odysseus seq+tombstone `/changes` for Person/Area/Link; Link soft-delete correct (revive on re-add); Area sync live (seeded areas appear; local seeding removed) + Area dashboards; People sync + Person detail via reverse-Link; task detail/edit (all fields incl. in_progress/cancelled) + linked context; calendar visible (month + event detail); Today assembled from synced model; meeting-notes composer round-trips all 5 rels; nav in both roots; a Notes-shaped slot exists so `Note` drops in next with no spine rework.

---

## Completion (2026-06-24) — all 9 slices shipped + live-verified

**Final state:** odysseus `dev @ 226f26c` (+ 0039fd9/740e43d/f9a7623/126c0fa earlier), tide `master @ 94896b5`. Brain run locally on :7860 (pollers off) for every live test. Each slice: spec → subagent-TDD implement → adversarial review → fix wave → **real live test** (gated Swift smokes against :7860 + direct API checks) → ff-merge + push.

- **0** multi-entity sync engine (tide `cab825a`) — per-entity cursors + entity-discriminated outbox + resource-param client + `EntitySync` descriptor registry; tasks round-trip preserved (live).
- **1** Link soft-delete + `/links/changes` (odysseus `740e43d`) — seq+tombstone on Link; row-enum soft-delete + **revive-on-readd** (uq_links_edge trap); revive verified live.
- **2** Person+Area delta contract (odysseus `f9a7623`) — seq+tombstone+`/changes`; `ensure_seeded_areas` stamps seq; seeded areas backfilled (live).
- **3** TideLink + Area sync + Area dashboard (tide `7fca7e7`) — graph mirror + reverse-Link helpers; `eraseDatabaseOnSchemaChange` removed; live pull verified.
- **4** People sync + list/detail (tide `8b65b3b`) — Person pages aggregate via reverse-Link (about); live pull verified.
- **5** Calendar visibility (odysseus `226f26c` + tide `23ef4c2`) — `calendar:read` scope + scope-aware GET /events; Tide windowed in-memory month view (off the seq lane); naive-local window fix; live fetch of ~150 real events verified.
- **6** Task detail/edit + linked context (tide `bfd9c67`) — `TaskStatus` enum (`completed` computed; additive v7); 4 FK fields mapped; **in_progress/cancelled round-trip verified live**; first real task editor.
- **7** Today day-planner (tide `d640c19`) — client-side `TodayAssembler` (overdue/meetings/scheduled/unscheduled + capacity + area chips) reusing the Agenda; drag-to-time-block via outbox; cancelled-task exclusion fixed.
- **8** Meeting-notes composer (odysseus `126c0fa` + tide `94896b5`) — `notes:read/write` scope-aware; minimal `TideNote` (id/title/content); `MeetingNoteClient`; macOS composer; **the gated 5-rel spine live test PASSES** (note_of/about/attended_by/in_area/from_note all created). The Notes-ready seam.

**Cross-cutting verified:** the polymorphic Link spine round-trips end-to-end; per-entity sync cursors; echo avoidance per entity; soft-delete tombstones everywhere; nav in both roots (macOS sidebar + iOS tabs).

**Follow-ups (for the Notes program / iOS polish — NONE block the foundation):**
- **iOS meeting-notes composer** — macOS-only this slice (the spine proof is on macOS).
- **Note sync feed** — `TideNote` is written from the save response only; a `/api/notes/changes` delta feed (+ a Note entity descriptor) lands with the Notes program.
- **Note detail/editor + the `.md` vault** — the next program (this foundation deliberately leaves the slot).
- Server: clean stale `note_of`/`about`/`attended_by` edges on meeting-note re-save (`src/meeting_notes.py` TODO).
- Minors logged in the per-slice `.superpowers/sdd/*-report.md` (gitignored): ViewModel tests for the composer, two-button save, etc.
