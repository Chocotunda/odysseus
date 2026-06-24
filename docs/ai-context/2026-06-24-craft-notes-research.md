# Modeling Tide's Notes Feature on Craft (craft.do)

**Decision-support report — 2026-06-24**
Audience: Odysseus/Tide architecture. Scope: design Tide's new Notes feature, using Craft as the reference product, reconciled against the Odysseus single-writer brain + `.md` vault + DB Link-graph + existing Keep-style `Note` model.

> Produced by research workflow `wf_76ec7162-d7e` (5 dimension finders → adversarial verify → synthesis). Craft's on-disk persistence and live conflict-merge are **not public**; internals-dependent claims are flagged. Recommendations lean on Craft's *verified* conceptual model + UX and on Craft's *own admissions* (JSON-canonical, Markdown-is-lossy, iCloud-multi-writer-is-dangerous).

---

## 1. Executive summary — what Craft's solution IS

Craft (craft.do, by Luki Labs of Budapest — *not* Apple/iCloud-native) is a **block-based document editor** where the atomic unit is the block: "Every paragraph in Craft is a Block." A document is a recursive **tree of blocks**, and the document's id *is* its root block id. "Page" and "card" are not separate object types — a block silently *becomes* a page the moment it gets nested children, and a card is merely a page rendered as a visual tile. Linking is structural, not textual: `@`-mentions (with `[[` auto-converting to `@`) create reference objects keyed by block identity, producing automatic "Links To This Page" backlinks. Critically for us, **Craft's canonical store is JSON, not Markdown** — Craft itself concedes plain Markdown is lossy for its model and that they "need to extend markdown to avoid feature loss"; Markdown is a deliberately lossy *interchange* format, and internal links use non-standard syntax that breaks on export to Obsidian. Sync is **server-authoritative** via Craft's own "Craft Sync Service" (socket.io + RDS Postgres) — *not* CloudKit, and Craft explicitly warns that file-on-iCloud multi-device editing "can lead to data conflicts and data loss." The editor UX is the part most worth copying: a fully keyboard-drivable slash/colon/`@` grammar, three redundant formatting paths, rich iOS gestures, and first-class drag-reorder — shipped as a Mac Catalyst app.

---

## 2. How Craft works, per dimension

### 2.1 Document model — *high confidence*
- **Block is the atomic unit.** Connect API type set: `text, page, collectionItem, image, video, file, drawing, whiteboard, table, collection, code, richUrl, line`.
- **Page is an emergent role, not a type.** A text block auto-converts to `page` when it gets children. "Only page, text, and card type blocks can be parent blocks."
- **Document = tree of blocks; root block id == document id.** Recursive `content` arrays. `indentationLevel` (0–5) is a visual axis distinct from page hierarchy.
- **Card = display style of a page** (`textStyle: card|page`, `cardLayout`). Not structural.
- **Pages nested in a document are blocks, NOT separate documents.** "Documents are the only top-level file unit." (The key contrast with Obsidian.)
- **Block schema (public API):** `type`, `id`, `markdown`, `indentationLevel`, `listStyle: none|bullet|numbered|toggle|task`, `decorations[]`, `metadata`, styling (`textStyle`, `font`, `textAlignment`).
- **Collections** = in-document database block (Table/Gallery/Kanban, up to 20 custom fields, two-way Relations).

> **Flag:** the field-enum schema is sourced *only* from Craft's public Connect API — authoritative for the API, NOT necessarily Craft's on-disk storage. Don't over-extend.

### 2.2 Markdown / file-format — *high confidence on the load-bearing fact*
- **Canonical store is JSON, not Markdown.** Craft's own blog: on-disk storage is JSON; they're "working on moving to a markdown-based version — (we'll need to extend markdown to avoid feature loss)." **The single most important finding: Craft itself concedes standard Markdown cannot losslessly round-trip a block/page/card model.**
- **Markdown export is intentionally lossy** (pages/cards/toggles flattened).
- **Import/export:** Markdown `.md` + TextBundle on all platforms; PDF/image; Word (not Windows).
- **Non-standard internal link syntax** — Craft `@`-links do NOT round-trip as `[[wikilinks]]`; Obsidian's importer special-cases them.
- **No evidence of YAML frontmatter** (*medium — argument from absence*).
- **"External Locations"** stores Craft's *own* format in a folder and **disables sync + collaboration** — NOT an Obsidian-style plain-`.md` vault.

### 2.3 Linking & organization — *high confidence*
- **`@`-mention links; `[[` auto-converts to `@`.** Stores structured reference objects keyed by block/page/document identity, not literal text.
- **Block-level AND page-level link granularity** (Roam-style).
- **Automatic backlinks** ("Links To This Page"). Drag-and-drop preserves the link; cut-and-paste deletes it (identity-keyed).
- **Deep links** via `craftdocs://` (spaceId + blockId; open/create/search commands).
- **Org hierarchy:** Spaces > Folders > Documents > Pages > Blocks, plus Tags, Collections, Daily Notes.
- **Daily Notes** are date-titled documents on a Calendar view alongside Tasks + synced system Events.
- **"Light PKM"** (*medium — reviewer consensus*): real backlinks but shallower graph than Obsidian/Roam.

### 2.4 Sync & offline — *high confidence on architecture; internals deferred*
- **Server-authoritative, NOT CloudKit.** "Craft Sync Service" (socket.io + RDS Postgres) "owns all document-related data." Migrated off Realm Sync (licensing/uncertainty); Realm remains the on-device store.
- **Per-frontend divergence:** mobile is **offline-first** (whole space mirrored, fully editable offline); web is **online-first** (per-document).
- **File-on-iCloud multi-writer is a stated data-loss risk** in Craft's own field experience — strong corroboration for single-writer.

> **Genuinely unknown:** live merge path (CRDT vs OT) is not public; whole architecture is single-sourced to the vendor.

### 2.5 Editor UX — *high confidence; the most copy-worthy dimension*
- **Three fast-input grammars, all keyboard-drivable:** `/` (blocks/actions), `:` (emoji), `@` (links/backlinks).
- **Three redundant formatting paths:** markdown auto-render, shortcuts (`Cmd-B/I`), toolbar with hover-shortcuts.
- **~80 block-oriented keyboard shortcuts**, Focus mode, Quick Open, Tab/Shift-Tab indent.
- **Rich iOS gestures** (swipe-select/extend/indent blocks, two-finger search, long-press menus).
- **First-class drag-reorder** (single/multi-block, cross-window).
- **Built with Mac Catalyst** — the reason it shares an iPad codebase yet feels native.
- **Known cost of the block model:** text selection is **confined to a single block** — no cross-block character selection.
- **Awards (corrected):** App Store **Mac App of the Year 2021**; a 2021 Apple Design Award **finalist** (did NOT win an ADA).

---

## 3. What to adopt for Tide notes

### 3.1 Note/document model — adopt the *conceptual* block tree, reject the *proprietary* block store
**Recommendation: Markdown body stays canonical (single source of truth on disk); blocks are a parse-time/render-time projection, never a separate persisted format.** The inverse of Craft's JSON-canonical model, and the correct inversion — Craft itself admits standard Markdown can't losslessly hold their model, so rather than fight that, we keep Markdown canonical and constrain the editor to constructs that *do* round-trip.
- Persist what we already persist: body = canonical Markdown file (YAML frontmatter + wikilinks); graph/metadata = DB rows; Odysseus single writer. **No new on-disk block format.**
- Treat "blocks" as a SwiftUI editor concept only; serialize back to plain CommonMark/Obsidian Markdown on save. Block ids ephemeral (or optional round-trippable `^block-id` anchors).
- **Do NOT adopt nested-pages-as-blocks.** Our top-level unit is the note = one `.md` file (Obsidian-shaped). Subpages = separate `.md` files linked via the graph + wikilinks. (Craft's defining choice; our defining rejection.)
- Cards = optional rendering style of a `[[wikilink]]`; zero new storage.
- **Collections / in-note databases: do NOT build.** Fights Markdown-canonical; overlaps our DB graph.

### 3.2 Reconciliation with the existing Keep-style `Note` model
- **Keep the existing fields** — `title`/`color`/`pinned`/`archived`/`labels` → YAML frontmatter; `content` → canonical Markdown body; structured checklist items → Markdown task lines (`- [ ]`) so they round-trip. Keep-style quick-note and long-form note become the **same object at two fidelities** (Craft's "fast input vs full document" continuum).
- **Plug into the spine slot already shipped** (slice 8's minimal `TideNote`/`NoteDTO`): reuse per-entity seq cursor, discriminated outbox, tombstones, reverse-Link helpers. **No sync-engine rework.**
- Route meaningful links (note↔Person/Area/Meeting/Task) through the **Link graph**, not labels — where we beat Craft's "light PKM."

### 3.3 Editor model in SwiftUI
- **Per-block TextKit-backed editors, not one giant `TextEditor`** (a `List`/`LazyVStack` of block cells). Naive single-`TextEditor` hits the polish/perf ceiling.
- **Adopt the slash/`@` grammar wholesale** — `/` block menu, `@`/`[[` Link insertion resolving against the graph.
- **Three redundant formatting paths** (auto-render + shortcuts + toolbar).
- **Decide the single-block-selection constraint explicitly** (accept it cheaply or invest to beat it).
- **iOS gestures + drag-reorder** are a large part of "feels native."
- **Skip Catalyst** — pure-SwiftUI block editor must deliberately invest in TextKit-backed cells + motion polish.

### 3.4 Linking via the Link graph + wikilinks (two layers)
1. **On disk:** standard Obsidian `[[wikilinks]]` (vault-portable — the thing Craft can't emit cleanly).
2. **In the DB:** a `Link` row per resolved reference → automatic backlinks via reverse-Link aggregation (existing helpers).
- Bind the DB Link to note id (+ optional `^block-id`), not text position (Craft's "drag preserves / cut deletes" lesson).
- Adopt a `tide://` deep-link scheme analogous to `craftdocs://`.
- Single cross-type `@`-picker over the whole graph.

### 3.5 How sync fits our online-first / single-writer model (Craft validates our choices)
- **Server-authoritative is correct** — Odysseus single writer = Craft's "Sync Service owns all data."
- **The `.md` vault is a server-side projection, NOT a multi-writer iCloud source.** Craft's own "iCloud multi-device → data loss" warning is direct evidence. Tide must **never** write vault files or sync via iCloud/Dropbox.
- **Per-entity seq-cursor pull + durable outbox push already match the contract.** Any note writer on the server **must set seq** (ALL-WRITERS-SET-SEQ).
- A websocket/SSE push layer is the natural future (Craft uses socket.io) — defer; seq cursor is fine.

---

## 4. What NOT to copy / risks
1. **Don't adopt Craft's JSON/proprietary canonical block store** (it violates Markdown-canonical; Craft itself is migrating away).
2. **Don't persist blocks as source of truth** (block-id drift; use round-trippable `^block-id` only if durable anchors needed).
3. **Don't adopt nested-pages-as-blocks** (breaks one-note-one-file, portability, the graph).
4. **Don't replicate non-standard internal link syntax** (emit standard wikilinks for Obsidian compatibility).
5. **Don't build Collections / in-note typed databases** (lossy to Markdown; duplicates the graph; Notion scope-creep).
6. **Don't build real-time CRDT/OT collaboration** (single-writer removes the need; Craft's merge path is undocumented).
7. **Don't let Tide write vault files or sync via iCloud/Dropbox** (Craft's data-loss cautionary tale).
8. **Don't depend on the single-sourced field-enum schema** as a spec to clone.
9. **Inherit single-block text-selection only by deliberate choice.**
10. **Don't chase Craft's styling surface** (gradient text, fonts, covers) — unverified Markdown round-trip.

---

## 5. Open questions to decide before we spec
1. **Block-projection editor vs simpler Markdown-pane editor for v1?** (Craft-grade UX, bigger build) vs (single live-preview editor, faster).
2. **Cross-block text selection — accept Craft's limitation, or invest to beat it?**
3. **One Note object at two fidelities (recommended), or two surfaces?**
4. **Subpages: graph-linked separate `.md` files (recommended) — or genuine in-note nesting?** (The core "Obsidian-shaped vs Craft-shaped" decision.)
5. **Block anchors in the vault: none, or optional `^block-id`?**
6. **`tide://` deep-link scheme — in scope for v1?**
7. **Daily Notes / date-titled notes on the calendar?**
8. **Offline note editing now, or stay strictly online-first?**
9. **Card/preview rendering of wikilinks in the editor — v1 or later?**
10. **Minimal frontmatter schema** (exact YAML keys) to lock the vault format before building the editor.
