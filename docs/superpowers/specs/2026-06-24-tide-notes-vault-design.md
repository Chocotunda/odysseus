# Tide Notes + `.md` Vault — Design Spec

**Date:** 2026-06-24
**Status:** Approved (brainstorm) → ready for implementation plan
**Repos:** `odysseus` (server) + `tide` (SwiftUI client)
**Depends on:** Tide foundation-parity (the multi-entity sync spine + the minimal `TideNote`/`NoteDTO` slot shipped in slice 8). No spine rework.
**Reference research:** `docs/ai-context/2026-06-24-craft-notes-research.md` (Craft modeled, adversarially verified).
**Direction authority:** `docs/ai-context/2026-06-22-life-os-direction-decision-record.md` §3.4 (notes = portable `.md` vault, `.md`-canonical body, DB-canonical graph, single-writer = Odysseus).

---

## 1. Goal & framing

Give Tide a real Notes surface synced from Odysseus, and project every note to a portable, Obsidian-compatible `.md` vault — modeling the **experience** of Craft (block-ish editing, slash menu, `@`/`[[` linking, automatic backlinks) while **rejecting** Craft's proprietary JSON-canonical block store in favor of Markdown-canonical files.

**The core principle (from the research):** Craft is JSON-canonical and admits Markdown can't round-trip its model; we invert that — **Markdown body is the single on-disk source of truth, "blocks" are only an editor/render projection.** This keeps the vault portable, diff-able, and Obsidian-compatible, and keeps Odysseus the sole writer.

**One object, two fidelities.** The existing Keep-style `Note` model *is* the note. A quick note and a long-form document are the same row / same `.md` file at different richness. The web back-office Notes UI continues to work unchanged on the same `Note` model. Meeting notes (already `Note` rows + Link edges) flow into the vault and the sync feed for free.

### 1.1 Slicing (phased, per decision)
The program ships in two slices. **This spec fully specifies Slice A**; Slice B is scoped at the end so the plan can sequence it without rework.

- **Slice A (this spec):** server `.md` vault + `Note` seq/tombstone sync feed + Tide notes surface with a **single-pane live-preview markdown editor** (slash menu, `@`/`[[` linking, checklist task-lines, backlinks panel). End-to-end working synced notes.
- **Slice B (next program):** upgrade the editor to a Craft-grade **per-block TextKit editor** (block cells, drag-reorder, gestures). Pure editor swap — no vault/sync/contract change.

### 1.2 Approved directional calls
1. **Subpages = separate linked `.md` files** (Obsidian-shaped), NOT in-note nesting (Craft-shaped). Deferred, but this is the directional commitment.
2. **Vault is one-way (DB → files) for now.** Reading external `.md` edits back in (Obsidian round-trip) is explicitly a later, deliberate slice — it's the multi-writer data-loss risk Craft warns about.
3. **Online-first** (consistent with current Tide). Richer offline note editing is deferred.

---

## 2. Server — the `.md` vault (single-writer projection)

Odysseus is the **only** process that writes vault files. Tide never touches files; it edits via the REST API, and the server projects to disk.

### 2.1 Location & constants
- New constant **`VAULT_DIR`** in `src/constants.py` (default `DATA_DIR/vault/`). No literal paths; guard creation so an unwritable path degrades gracefully (log + skip projection, never crash a note write).
- A new module **`src/note_vault.py`** owns serialization: `write_note(note, links) -> path`, `remove_note(note_id)`, `vault_path_for(note)`, slug helper. Pure-ish (filesystem + formatting only; no route logic).

### 2.2 File format
`.md` file per note. **Body = canonical Markdown; all other fields = YAML frontmatter.**

```markdown
---
id: <note uuid>            # stable identity
title: Q3 planning
created: 2026-06-24T18:00:00Z
updated: 2026-06-24T18:30:00Z
tags: [work]               # from Note.label (+ future labels)
color: blue
pinned: true
archived: false
note_type: note            # note | checklist
links:                     # mirrored from the Link graph (reverse not included)
  - "[[Person/Krishen Ganpat]]"
  - "[[Area/Work]]"
---

Body markdown. Checklist items render as task lines:

- [ ] follow up with Sam
- [x] book the room
```

- **Checklists:** the structured `items` JSON (for `note_type == "checklist"`) renders to `- [ ]`/`- [x]` lines appended to / merged with the body. For Slice A the **vault body is derived from `content` + `items`**; the DB stays canonical (one-way). Round-tripping edited task lines back into structured `items` is a Slice-B/bidirectional concern.
- **Frontmatter is the metadata mirror**, DB remains canonical for it. `links:` lists only the note's *outbound* resolved Links (reverse/backlinks are computed, never stored in the file).
- Serialization must be **deterministic** (stable key order, stable list order) so re-writing an unchanged note produces a byte-identical file (avoids vault churn / spurious file mtime changes).

### 2.3 Filenames
**Title-slug + short id:** `<slug(title)>-<first 4-6 of id>.md` (e.g. `q3-planning-9f3c.md`). Untitled → `untitled-<id4>.md`.
- On title change, the file is **renamed** (write new path, remove old path) — `note_vault` detects the path delta from the prior slug.
- Short-id suffix guarantees collision-freedom without a global title index.

### 2.4 Write triggers (single-writer discipline)
Every `Note` mutation projects to the vault, in the **same** code paths that already persist the note:
- `routes/note_routes.py`: create, update (PUT), pin, archive, checklist item toggle, reorder (reorder changes `sort_order` only — no body change, but still re-stamp if `updated_at` moves).
- `src/meeting_notes.py`: `save_meeting_note`, `promote_action_item` (when it edits the note).
- Any agent/MCP note writer.
- **Delete** → `note_vault.remove_note(id)` (after the soft-delete; see §3).

To avoid scattering calls, add a single **`persist_note(db, note, *, links=None)`** helper (in `src/note_vault.py` or a thin `src/notes_service.py`) that callers invoke after committing a note; it both sets `seq` (see §3) and writes the vault file. **All writers route through it.** (This is the structural fix for the recurring "a writer silently skips the cross-cutting concern" trap — same lesson as ALL-WRITERS-SET-SEQ and the `deleted_at` site-sweep.)

### 2.5 Backfill
Guarded one-time pass in `run_hub_migrations()` (idempotent): for every existing non-deleted `Note`, write its `.md` file if absent. Mirrors the `seq`/`deleted_at` backfill pattern. Logs count; never fails startup.

---

## 3. Server — `Note` sync feed (mirror the `PlanItem` contract)

The proven 1b-0 contract, applied to `Note`.

### 3.1 Schema additions (`core/database.py` `Note`)
- **`seq`** — per-owner monotonic integer, assigned on **every** write. Guarded additive migration `_migrate_add_note_seq_column()` + per-owner 1..N backfill.
- **`deleted_at`** — nullable timestamp tombstone. Guarded additive migration `_migrate_add_note_deleted_at_column()`.
- Both registered in `run_hub_migrations()` (create_all won't ALTER an existing table).

### 3.2 Seq allocator
- New **`core.hub_models.next_note_seq(db, owner)`** (mirrors `next_plan_item_seq`). The `persist_note` helper (§2.4) calls it so **all** writers set `seq`. Sweep with `grep -rn "Note(" src/ routes/ mcp_servers/` + every `note.save`/commit site.

### 3.3 Endpoints (`routes/note_routes.py`)
- `GET /api/notes/changes?since=<seq>` → `{items, cursor}`. `seq > since` (exclusive), **includes tombstones** (so clients learn deletes), `cursor = max(seq)`. `items` shape = the existing note dict + `seq`, `deleted`, `deleted_at`.
- `DELETE /api/notes/{id}` → **soft delete** (set `deleted_at`, bump `seq`, remove vault file). All live-read sites exclude `deleted_at IS NOT NULL`: list, get, pin/archive/toggle/reorder lookups, meeting-notes dup/max scans, and any people/area page that surfaces notes.
- Reuse the existing `notes:read` / `notes:write` scopes (added slice 8, `126c0fa`); add them to the **`tide` token profile** in `routes/api_token_routes.py`. Browser/cookie sessions unchanged; wire the scope-aware owner gate at each call site (mirror `planner_routes`).

### 3.4 Wikilink ↔ Link reconciliation (server-owned)
When a note body is saved, the server is the single place that reconciles text links with the graph:
- Parse `[[Type/Name]]` wikilinks the **editor inserted** (Slice A guarantees these are picker-generated, hence resolvable) → upsert `Link` rows (note → entity, appropriate rel).
- Mirror the note's current outbound `Link` rows → frontmatter `links:`.
- Bind Links to the **note id**, never text position.
- Stale-edge cleanup on re-save (drop Links no longer present) — also closes the existing `src/meeting_notes.py` TODO for stale `note_of`/`about`/`attended_by` edges.
- **Scope for Slice A:** free-text / fuzzy wikilink auto-resolution is a refinement; the must-have is that picker-inserted links round-trip to graph Links and back to frontmatter.

---

## 4. Tide — `Note` on the sync spine (additive, no coordinator rework)

### 4.1 Model & DTO
- Promote stub `TideNote` → full `@Table("tideNotes")`: `id`, `title`, `body` (markdown), `noteType`, `tags`, `color`, `pinned`, `archived`, `sortOrder`, `createdAt`, `updatedAt`, `seq`. Local-only/unsynced fields preserved-from-existing on upsert (per the established mapper pattern).
- Flesh out `NoteDTO` ⇄ `TideNote` pure mappers (Codable mirror of the server note dict; naive-UTC ISO `Z` trick; LWW by `updated_at`).

### 4.2 Sync wiring
- Register **`EntitySync.note`** descriptor: pull `/api/notes/changes?since=seq` → upsert-by-UUID / tombstone-delete / advance cursor, **writes GRDB directly** (echo avoidance).
- Add a **note lane** to the discriminated durable outbox: optimistic push **PATCH→404→POST**, delete tolerant of 404, LWW. (Notes need a `PATCH /api/notes/{id}` + `POST /api/notes`; confirm/extend `note_routes` to accept the same all-Optional patch semantics as planner — `exclude_unset` so absent ≠ null.)
- Additive GRDB migration → **Tide DB v9**. Forward-only, additive.

> **Server note:** if `note_routes` PUT is full-replace (not partial-patch), add a partial `PATCH` or confirm PUT is safe for optimistic field edits. Resolve during planning by reading `note_routes.py`.

---

## 5. Tide — Notes UI (Slice A)

- **Notes list** — macOS sidebar entry + iOS tab. Pinned first, then by recency; archived filtered out (toggle to show). Reuses existing list/card patterns + visual style (CSS-var equivalents, monochrome SVG, Fira Code — N/A in SwiftUI but match Tide's existing component styling).
- **Note detail + single-pane live-preview markdown editor:**
  - Live markdown rendering (headings, lists, task lines, quotes, code, bold/italic).
  - **`/` slash menu** — insert heading / bullet / numbered / task / quote / code / divider. Keyboard-navigable.
  - **`@` and `[[` linking** — opens a single cross-type picker over the synced graph (People / Areas / Notes / Meetings); inserts a resolvable `[[Type/Name]]` wikilink.
  - **Checklist task-lines** toggle (`- [ ]` ↔ `- [x]`) inline.
  - Edits flow through the outbox (online-first, optimistic).
- **Backlinks panel** — "Linked from" = reverse-Link query (`linksTo(.note, id)`), identical mechanism to the other entity pages. Also show the note's outbound links.
- **Create / delete** via outbox; delete = soft (tombstone round-trip).

---

## 6. Testing & verification (same discipline as the 9 parity slices)

- **Server (pytest):** seq/deleted_at migrations + per-owner backfill (0 NULL); `persist_note` sets seq + writes vault on **all** writer paths (regression test per writer); vault file format (deterministic bytes, frontmatter keys, checklist rendering, slug+id filename, rename-on-retitle, remove-on-delete); `/notes/changes` cursor (exclusive, tombstones included); wikilink→Link upsert + frontmatter mirror + stale-edge cleanup; scope gate (notes:read/write; non-notes token 403).
- **Tide (`swift test`):** `NoteDTO`⇄`TideNote` mappers; note outbox push/coalesce; `EntitySync.note` descriptor dispatch; v9 migration on-disk.
- **Gated live smoke** (`*LiveTests` vs the brain on `:7860`, env-gated): create note → `.md` file appears in the vault with correct frontmatter+body → pull into Tide → edit body+title → file rewrites/renames → soft-delete → tombstone pulled + file removed. Plus a wikilink round-trip (picker-insert `[[Person/…]]` → Link row created → frontmatter mirrors it → reverse-Link shows on the person page).
- **GUI verification:** rebuild + **reinstall `/Applications/Tide.app`** (today's stale-build lesson) and confirm the notes surface live.

---

## 7. Slice B (scoped, not built here)

Editor upgrade only — no contract/vault/sync change:
- Per-block TextKit-backed editor (`List`/`LazyVStack` of block cells), drag-reorder, iOS block gestures, richer slash grammar.
- Optional round-trippable `^block-id` anchors **iff** block-level deep-links/backlinks are wanted.
- Decide single-block vs cross-block text selection explicitly.

## 8. Out of scope (deferred)
Reading external `.md` edits back (bidirectional vault / Obsidian-edit-through); subpages as nested files; daily/date notes; `tide://` deep-link scheme; images/attachments in the vault (`image_url` stays DB-only for now); websocket/SSE push (seq poll is fine); structured-`items` round-trip from edited task lines.

## 9. Risks & notes
- **No production precedent for the SQL + agent + mobile + `.md`-vault trio** (decision record §10) — Slice A *is* the small prototype; keep the vault one-way to contain risk.
- **ALL-WRITERS-SET-SEQ / persist-note** is the load-bearing invariant; the `persist_note` chokepoint exists specifically to prevent a writer skipping seq or vault projection.
- **Backend has no auto-reload** — restart uvicorn after server changes before any live smoke.
- **Vault determinism** matters: non-deterministic serialization would churn files on every save; pin key/list ordering.
