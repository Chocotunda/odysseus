# Management Hub — Tracer Slice Design

**Date:** 2026-06-19
**Status:** Approved (brainstorm) → ready for implementation plan
**Scope:** One vertical slice that lays the minimal connective spine of the management hub and proves it end to end.

---

## 0. Context — the endgame and why this slice

Odysseus has grown a pile of capabilities connected to the user's real data (chat, email/Gmail, calendar+contacts/iCloud, documents/RAG, notes, planner/tasks, research, gallery, memory, cookbook). They surface as ~11 separate nav "tools."

The user's endgame is a **whole management hub** spanning **work + personal life + knowledge (second-brain)**, with AI woven through to enhance — not drive — it. The stated risk: shipping more isolated features (Planner Phase 2, a People view, a Meetings view) and ending with a *drawer of tools* instead of a *hub*.

### The unifying insight (from research into Tana, Routine, Sunsama, Todoist)

All four reference products are the same machine in different clothes:

- **Tana** — the foundation: everything is a **typed object** (`#person`, `#meeting`) with **fields** and **automatic bidirectional links**; "views" are just queries over the graph. Its fatal flaw is making the *user* design the schema — we dodge that by **hard-coding our node types**.
- **Routine** — proves **Notes→Tasks→Meetings must be a real link, not a mention**: an action item created in a meeting note stays bound to that meeting and **resurfaces, still-incomplete, when reopened.**
- **Sunsama** — the **daily ritual** layer (plan-the-day, workload meter, timeboxing, auto-rollover, reflection). *Deferred to a later slice.*
- **Todoist** — **frictionless capture** with **verify-before-commit** parse chips (`+Wiggert`→a real Person; `#Health`→an Area). We adopt the verify-before-commit principle now; full capture grammar later.

**Conclusion:** the hub is **one typed-node graph + a thin link table**, and every feature is a *view* onto that graph. Lock the spine once; nothing gets rebuilt. This is the foundation. The 8 first-class node types are **People, Tasks, Notes, Meetings, Projects/Areas, Emails, Documents, Goals/Habits** — a *fixed, built-in* schema (no user-authored supertags).

### Decisions taken in brainstorming

- **Scope:** work + life + knowledge (a personal life-OS / second-brain), AI as an enhancer.
- **Single-user personal hub.** "People" are entities the user tracks (reports, contacts); they do **not** log into Odysseus. Everything stays owner-scoped to the user.
- **Approach:** **tracer slice on a minimal spine** — build only the keystone (link table + a real Person node), drive ONE high-value loop through it end to end, then widen view by view.

---

## 1. The tracer slice — scope

**The loop (the manager 1:1 vision, end to end):**

1. Open a **meeting** (a real iCloud event — 366 exist) → create a **meeting note** attached to it, tagged with a **Person** (e.g. "Wiggert").
2. Jot **action items** in the note → each becomes a **task**, linked back to the note + meeting + person.
3. A **Person page** aggregates: their meetings, their notes, their open tasks.
4. **Resurfacing:** opening a new note/meeting with that person shows last time's *incomplete* action items.

**Done =** the user runs one real 1:1 against their live iCloud calendar; action items land as linked tasks and resurface on the Person page and the next meeting.

**Deliberately OUT of this slice** (each becomes a clean follow-on once the spine is proven):
the daily "Today" surface, timeboxing + workload meter, full capture-chip grammar, Goals/Habits, Email/Document nodes, AI meeting transcription/diarization, Projects/Areas beyond the existing `PlanProject`.

---

## 2. Data model — the keystone

Two new tables. **One join table *is* the graph.**

```
Link:    id, owner, from_type, from_id, rel, to_type, to_id, created_at
         INDEX (owner, from_type, from_id)
         INDEX (owner, to_type, to_id)

Person:  id, owner, name, contact_uid?, email?, role?, archived
         contact_uid optionally references one of the 242 iCloud contacts
```

**`rel` vocabulary is small and explicit** (not free-form):

| from | rel | to |
|---|---|---|
| Note | `note_of` | Meeting (`CalendarEvent.uid`) |
| Note | `about` | Person |
| Task (`PlanItem`) | `from_note` | Note |
| Task | `about` | Person |
| Meeting | `attended_by` | Person |

**`Link` is the canonical link mechanism for all node types going forward.** `PlanItem`'s existing soft fields (`source_note_id`/`source_event_id`/`person_id`) stay populated as a cheap denormalized cache (no migration), but the graph **reads from `Link`**. This is what lets every *future* node (email, doc, goal) link the same way with zero new schema — the spine generalizes.

**Backlinks** = the reverse query on `Link`. The Person page is three reverse-`Link` queries. Pure SQLite, owner-scoped per the patterns in `core/database.py`. No graph engine.

**What already exists and is reused:** `PlanItem` (has `estimate_minutes`, `project_id`, the `source_*` soft fields, `priority`/`status`/`planned_day`/`due_date`), `Note` (has `content`, `items` JSON checklist, `ai_classification`/`ai_content_hash`), `CalendarEvent` (keyed by `uid`).

**Owner-scoping is a security boundary.** `Link` and `Person` are new owner-scoped surfaces; every query uses `owner_filter` (`src/auth_helpers.py`). This gets P0 isolation tests.

---

## 3. Surfaces (UI)

Three additions, reusing existing patterns (`static/js/planner.js` + `notes.js` templates; CSS vars `--red`/`--fg`/`--card`/`--border`; inline monochrome SVG, **no emoji**; Fira Code; dark default).

### 3.1 Meeting-note composer (from a calendar event or "+ Meeting note")

```
┌─ Meeting note ─────────────────────────────────┐
│  1:1 — Wiggert            [▾ Person: Wiggert ✎] │  ← person picker (search contacts / new)
│  ◷ Thu Jun 19, 14:00 · "Weekly 1:1"   [linked] │  ← pre-attached meeting (read-only chip)
│ ────────────────────────────────────────────── │
│  Notes                                          │
│  [ free prose → Note.content ]                  │
│                                                 │
│  Action items                          [+ add]  │  ← checklist (reuses Note.items JSON)
│  ☐ Send Q3 deck            → not yet a task     │
│  ☐ Review perf doc         → not yet a task     │
│ ────────────────────────────────────────────── │
│           [ Save note ]   [ Save + make tasks ] │
└─────────────────────────────────────────────────┘
```

- **Save note** → writes Note + `note_of→meeting` + `about→person` links.
- **Save + make tasks** → same, plus promotes each unchecked action item to a linked `PlanItem`.
- The **person picker** is the only genuinely new widget (search the 242 iCloud contacts or create a new Person). Everything else is `notes.js` styling.

### 3.2 Promoted action item shows its task link

```
  Action items
  ☐ Send Q3 deck            → ◳ task · due Jun 23  ✎   ← chip links to the PlanItem
  ☐ Review perf doc         → ◳ task                ✎
```

Monochrome SVG task glyph, `--red` on hover, click jumps to the task. Re-saving an already-promoted item is **idempotent** on the `from_note` link (update, never duplicate).

### 3.3 Person page (`/people/<id>`, new `people.js`, nav button `tool-people-btn`)

```
┌─ Wiggert ──────────────────────────────  [✎ edit] ┐
│  Direct report · wiggert@…            (iCloud link) │
│                                                     │
│  ▸ Open items (3)                       [resurfaced]│  ← the management payoff
│     ☐ Send Q3 deck         from 1:1 Jun 19 · due 23 │
│     ☐ Review perf doc       from 1:1 Jun 19         │
│     ☐ Book travel approval  from 1:1 Jun 12 ⚠ overdue│ ← carries across meetings
│                                                     │
│  ▸ Meetings (5)    ◷ Weekly 1:1 — Jun 19 · 2 notes  │
│  ▸ Notes (6)       ▤ 1:1 — Jun 19 "Q3 roadmap…"     │
└─────────────────────────────────────────────────────┘
```

Three collapsible sections, each a reverse-`Link` query. **Open items** = `tasks about=person AND status=open`, overdue-first — this is the resurfacing, and this page *is* the proof the graph works.

---

## 4. AI's role (kept small — the local-LLM principle)

**Exactly one AI touch**, reusing the shipped `src/planner_ai.py` pattern (small `utility` lane, background, never blocks; mirrors `/capture`→`enrich_item`).

- **Trigger:** on **Save + make tasks**, fire a FastAPI `BackgroundTasks` job. The note + the user's hand-typed checklist save *instantly*; AI runs after.
- **Job:** `extract_action_items(note)` — extends `src/planner_ai.py`, `/no_think`.
  - **Input:** note prose + person name + meeting date.
  - **Prompt job:** "From these meeting notes, list concrete action items as JSON. For each: `title`, `owner` (`me` | the person | null), `due_hint` (verbatim phrase or null). Do NOT invent items." Strict JSON, one shot.
  - **Output example:**
    ```json
    [ {"title":"Send Q3 deck","owner":"wiggert","due_hint":"by Friday"},
      {"title":"Review perf doc","owner":"me","due_hint":null} ]
    ```
- **Coercion (Python owns correctness; the 4B is never trusted)** — reuse/extend `coerce_capture`: clamp `owner` to known persons (unknown → null, never a hallucinated person), resolve `due_hint`→date with **dateutil + server tz** (the Phase-2 date fix, landed here), drop empties. Model failure → degrade silently; the hand-typed checklist is already saved and promotable.

**UX — suggestive, never auto-commit** (Todoist verify-before-commit):

```
  AI found 1 more action item        ✓ add    ✗ dismiss
  ☐ "Schedule perf review with Wiggert"  (owner: me)
```

AI-extracted items the user *didn't* type appear as **dim candidate chips** with one-tap add/dismiss. Hand-typed items always promote deterministically with **no AI in the path**. Net: **AI augments capture, Python guarantees the links and dates, the user confirms anything invented.** One model call, background, degradable.

---

## 5. Testing

TDD, mirroring `tests/test_planner_*.py` (in-memory SQLite, monkeypatched `SessionLocal`, direct route calls with a `SimpleNamespace` request).

- **`test_link_model`** — create / query / reverse-query links; **owner isolation P0**.
- **`test_person_crud`** — CRUD + owner scope + optional `contact_uid`.
- **`test_meeting_note_flow`** — saving a meeting note writes the `note_of` + `about` edges.
- **`test_action_item_promotion`** — action items become linked tasks (`from_note` + `about` edges; soft-field cache stays consistent); **idempotent** re-save.
- **`test_person_page_aggregation`** — the three reverse queries return the right tasks/meetings/notes and **exclude other owners' and other persons' rows**.
- **`test_resurfacing`** — opening a new note for a person surfaces *only* their incomplete tasks.
- **`test_extract_action_items`** — stubbed model: applies, coerces (clamps unknown owner, resolves date), degrades to the raw note on failure.

**Live verification:** run one real 1:1 against the iCloud calendar; screenshot the Person page with resurfaced tasks.

---

## 6. Files (anticipated)

- `core/database.py` — `Link` + `Person` models (owner-scoped).
- `routes/people_routes.py` (+ helpers) — Person CRUD + person-page aggregation; `setup_people_routes` in `app.py`; `/people/<id>` SPA shell route.
- `routes/planner_routes.py` / a notes route — meeting-note save writes links; action-item promotion creates linked `PlanItem`s.
- `src/links.py` (new, small) — link create/query/reverse helpers (the one place link reads/writes live).
- `src/planner_ai.py` — add `extract_action_items` + coercion.
- `static/js/people.js` (new), `static/js/notes.js` (meeting-note composer + action-item promotion), `static/index.html` (`tool-people-btn` + route), `static/app.js` (wiring), `static/js/slashCommands.js` (`/people`), `static/style.css` (`.person-*` block reusing vars).
- `tests/test_links.py`, `tests/test_people_*.py`, `tests/test_meeting_notes.py`.

---

## 7. What this unlocks next (not in scope, but the payoff)

Once the spine exists, each deferred item is a *view*, not a rebuild:
- **Daily "Today" surface** (Sunsama/Routine) — tasks + meetings for today on one timeline; later add timeboxing + workload meter.
- **Capture bar** with verify-before-commit chips (`+person`/`#area`) — the global NSPanel overlay already exists.
- **Email / Document nodes** — link a thread or doc to a person/task via the *same* `Link` table.
- **Goals / Habits** — longer-horizon nodes tasks roll up into.
- **Projects/Areas** — promote `PlanProject` to the cross-cutting grouping node.
