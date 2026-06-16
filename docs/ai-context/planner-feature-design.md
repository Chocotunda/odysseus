# ODYSSEUS Daily Planner — Design Document (Final)

> Produced by a multi-agent design workflow (14 agents): codebase deep-dive + local Tide repo
> review + web research on Sunsama/Routine/AI-task patterns, synthesized into a design,
> adversarially reviewed, then finalized. Answers: "Can we build a Tide/Sunsama/Routine-style
> task-management system powered by Odysseus's local AI, as a dedicated integrated page?"

## 0. Locked decisions (2026-06-16)

Scope decisions confirmed with the user; they override the open questions in §9 where they conflict.

1. **Vision: a connected personal workspace, not a standalone planner.** One linked graph — Notes (team knowledge base *as a manager* + private) ↔ Tasks (incl. action points from notes/meetings) ↔ People (team members, e.g. Wiggert) ↔ Meetings (calendar events). Everything cross-references with back-references; the **planner is the daily lens** onto the graph, a future notes/knowledge page is the other lens. Design the linking model up front; ship planner + task↔note/meeting/person refs first, grow team-notes on the same foundation. (See memory `planner-workspace-vision`.)
2. **Single owner for now (Open Q1 = A).** Just the user; team members are referenced *entities*, they do NOT log in. No sharing/permissions/assignment yet. Still stamp a concrete owner on every write and keep the owner-scoping patterns (cheap insurance + future multi-user path).
3. **Calendar: internal-only (Open Q3).** CalDAV stays read-only (busy intervals for capacity + meetings to attach notes to). No write-back. Planner is home base.
4. **Timezone: server timezone (Open Q7).** Always runs on this one Mac; compute the `YYYY-MM-DD` day strings from server-local tz. (Still store as tz-resolved date strings so the day-equality invariant holds if this ever changes.)
5. **Namespace: `planner` (Open Q1-namespace).** `/api/planner`, `static/js/planner.js`, `tool-planner-btn`. Stays clear of the scheduler's `tasks`.
6. **Phase 1 priority = task capture.** The tracer bullet centers on getting tasks in fast (manual + NL capture), with the linking fields designed-in. Time-blocking / Week / capacity move to Phase 2 as planned.

**Implication for the data model (§4):** add nullable reference fields to `PlanItem` now even though their UIs come later — `source_note_id` (FK → notes), `source_event_id` (CalendarEvent id), `person_id` (FK → contacts/Person). This is what makes "note from a Wiggert meeting → action-point task → visible in planner with a back-reference" work without a later migration scramble. People likely reuse the existing `Contact`/CalDAV model rather than a new table (to verify when building).

---

## 1. Verdict

**Yes, with caveats — and it's a strong fit.** A Sunsama/Routine/Tide-style daily-planning page is buildable natively in Odysseus by adding owner-scoped `PlanItem`/`PlanProject` models, a `routes/planner_routes.py` factory, and a `static/js/planner.js` page, then wiring the existing local-AI surface (`llm_core` one-shot calls, the `builtin_actions` scheduler, the agent `manage_*` tool pattern) for the AI features. The headline approach: **build the planner natively (do NOT integrate Tide), keep all scheduling/recurrence/capacity math in plain Python, and use the local LLM only for the fuzzy language/judgment layer (parse, classify, phrase, decompose) via schema-constrained one-shot calls.** The defining hardware constraint: this is a 24GB Mac serving **one model at a time** via Ollama — so the dominant cost is **model *swaps*, not inference**, and AI features must avoid forcing a swap on the interactive path.

Caveats:
- (a) the `tasks` namespace is already taken by the scheduler (see §2), so the feature uses `planner`;
- (b) per the agent-PR policy in `CLAUDE.md`/`CONTRIBUTING.md`, upstreaming must start as an *issue*, not a PR — but for a personal clone that governs contribution, not building;
- (c) two owner-scoping footguns must be fixed before any code ships (see §9 P0 items): the ownership gate must be written against the coerced `_owner(request)`, not `require_user` directly, and list queries must pass `include_shared=False`.

---

## 2. What already exists & what's reusable

There are **two unrelated things called "task"** in this repo; conflating them is the #1 design hazard.

**A. Scheduler "tasks" — backend automation (NOT user to-dos).** `ScheduledTask` (`core/database.py:570`) + `TaskRun` (`core/database.py:650`) are an automation engine: LLM prompts / builtin actions / research runs that fire on cron/schedule/event/webhook triggers. `routes/task_routes.py` (52KB), `static/js/tasks.js` (128KB scheduler admin UI calling `/api/tasks`), the `tool-tasks-btn` nav entry (`index.html:895`), and the `/api/tasks` prefix are **all occupied**. The planner must NOT reuse the `tasks` name, `/api/tasks`, or `tool-tasks-btn`.

**B. Notes with todo affordances (the closest existing user-facing thing).** `Note` (`core/database.py:1608`) has `items` (JSON checklist `[{text,done}]`), `due_date` (string), `repeat` (none/daily/weekly/monthly/yearly), and reserved AI fields `ai_classification` (JSON `{kind, solvable, confidence, task_prompt, tools, items?}`), `ai_content_hash` (gates re-classification to avoid LLM spend), and `agent_session_id` (links a "solve with agent" chat). `routes/note_routes.py` is the cleanest CRUD-factory template to copy — **including its `_owner(request) = require_user(request) or None` coercion and its `# SECURITY:` ownership-gate comment, which are load-bearing (see §9 P0-1).**

**Reusable for free:**
- **Reminder dispatch** — `dispatch_reminder()` (`routes/note_routes.py:138`): browser/email/ntfy/webhook channels, 25-min dedupe cache `data/note_pings_{owner}.json` (under `DATA_DIR`, verified), optional LLM one-sentence synthesis. Call it verbatim.
- **Scheduler** — `src/task_scheduler.py` runs builtin actions one-at-a-time behind a `Semaphore(1)` (verified); the `ping_notes`/`ping_events` loops and `daily_brief` already do due-time scanning and **explicit per-owner fan-out** (`task_scheduler.py:531-560`).
- **Calendar** — `src/caldav_sync.py` + `CalendarEvent` give busy-interval data (tz-aware/UTC) for the capacity/time-block math.
- **Owner-scoping** — `owner_filter` (`src/auth_helpers.py:140`) + `require_user` (`:62`) + `EncryptedText`. **Note the two sharp edges in §9.**
- **Local-AI entry points** — `resolve_endpoint(prefix, owner)` (`src/endpoint_resolver.py:270`, supports `"utility"`/`"research"`/`"default"`), `llm_call_async`, `stream_llm`, and automatic `<think>` suppression for Ollama (`payload["think"]=False`, `src/llm_core.py:1632`).
- **Deps already present** — `icalendar` + `python-dateutil` are in `requirements.txt` (verified), so RRULE expansion and date parsing need no new dependency.

**What's missing (the greenfield):** a user-facing item with status workflow + priority + duration/estimate + time-block placement; a today/planner/week UI; recurrence instance expansion; capacity warnings; the morning/evening rituals; and the validation/coercion layer for AI output.

---

## 3. Recommended approach: **native build, not Tide-via-MCP**

**Decision: build natively in Odysseus.** Rejecting Tide integration:

- **Platform mismatch.** Tide is Swift/SwiftUI + **CloudKit** (`iCloud.com.krishen.tide`), Apple-only. Odysseus is Python/SQLite/local-first, runs Linux/Windows/macOS native + Docker. A CloudKit dependency contradicts the self-hosted/offline positioning.
- **Tide's MCP server doesn't exist yet.** Per the Tide dossier the MCP server is *Slice 7*, unstarted and blocked behind Sunsama-parity slices 2–6. There is nothing to connect to.
- **Impedance + ownership.** Tide tasks live in a personal CloudKit DB with no Odysseus owner-scoping; bridging through MCP would bypass the `owner` boundary.

**What we DO borrow from Tide — its domain-model invariants** (portable to Python):
1. **`planned_day` membership by exact equality, not range query** — but stored as a **`YYYY-MM-DD` string in the user's timezone**, not a server-naive `DateTime` (corrected; see §4 and §9 P1-6).
2. **Fractional/gapped ordinal** sort keys for drag-reorder — but using an **integer-gap (lexorank-style) scheme with a specified rebalance**, not raw float midpoints (corrected; see §4 and §9 P1-4).
3. **`estimate_minutes` (soft) distinct from `duration_minutes` (hard time-block)** — Sunsama's planned-vs-actual split.
4. `Priority` enum (None/Normal/Important/Urgent) and `BacklogHorizon` (Someday/NextWeek/NextMonth/Never).
5. The **SurfacingEngine heuristic** (~91 lines of scoring: overdue > due-soon > priority > planned-today > stale > time-of-fit) — reimplement in Python as a deterministic ranker with **tunable weights** (the Tide weights are an unvalidated starting point for this user, not gospel), NOT an LLM call.

**Future bridge (optional, deferred):** once Tide ships Slice 7, add a one-way *import* MCP connector as an external `McpServer` config — never a hard dependency.

This matches the upstream roadmap neighborhood (issues #2072/#4063/#4066 "agent-readable Notes/Todos", #4381 "fan-in briefing") so the design stays mergeable in spirit.

---

## 4. Data model (`core/database.py`)

Follows verified conventions: `TimestampMixin`, `owner = Column(String, nullable=True, index=True)`, string UUID PKs, JSON-in-Text for lists, composite indexes in `__table_args__`, FK `ondelete="SET NULL"`. **No `EncryptedText`** — planner content (titles, notes, dates) is not secret-at-rest like passwords/tokens; encrypting would block search/sort with no threat-model benefit. (Encrypting private titles is a deliberate later opt-in, not a default.)

**Two corrections from review, baked in below:**
- **Timezone (P1-6):** `planned_day` and `day` are **`String` (`YYYY-MM-DD`) computed in the user's timezone**, not naive server `DateTime`. This makes the central "exact equality determines day membership" invariant correct even when the server runs UTC in Docker. The canonical tz source is an **open question (§9 OQ-7)** — until resolved, the implementation reads a per-owner tz setting and falls back to the server tz with an explicit, logged default.
- **Ordinal (P1-4):** `ordinal` is an **`Integer`** allocated on a gap scheme (new items at `max+1024`; insert-between uses the integer midpoint; when an adjacent gap `< 2`, rebalance that contiguous run to fresh `1024` multiples in one transaction). A regression test inserts at position 0 fifty times and asserts no collision/rebalance failure.

```python
class PlanProject(TimestampMixin, Base):
    """A project / area / channel that PlanItems group under (Sunsama 'channel')."""
    __tablename__ = "plan_projects"
    id         = Column(String, primary_key=True, index=True)
    owner      = Column(String, nullable=True, index=True)
    name       = Column(String, nullable=False)
    color      = Column(String, nullable=True)      # reuse CSS-var hexes
    archived   = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    __table_args__ = (Index('ix_plan_projects_owner', 'owner', 'archived'),)

class PlanItem(TimestampMixin, Base):
    """A user-facing task/todo with planning + time-block fields."""
    __tablename__ = "plan_items"
    id            = Column(String, primary_key=True, index=True)
    owner         = Column(String, nullable=True, index=True)  # ALWAYS stamped on write — never left default (P0-2)
    title         = Column(String, nullable=False, default="")
    notes         = Column(Text, nullable=True)
    # planning
    planned_day   = Column(String, nullable=True, index=True)   # 'YYYY-MM-DD' in user tz; NULL = backlog
    due_date      = Column(String, nullable=True)               # 'YYYY-MM-DD' in user tz
    backlog_horizon = Column(String, default="none")  # none|someday|next_week|next_month|never
    priority      = Column(String, default="normal")  # none|normal|important|urgent
    status        = Column(String, default="open")    # open|in_progress|done|cancelled
    completed_at  = Column(DateTime, nullable=True)   # instant; UTC-aware
    # time / effort  (estimate = soft, duration = hard block)
    estimate_minutes = Column(Integer, nullable=True)
    scheduled_start  = Column(DateTime, nullable=True)   # tz-aware instant; NULL = untimed
    duration_minutes = Column(Integer, nullable=True)
    actual_minutes   = Column(Integer, nullable=True)    # from focus timer
    preferred_time   = Column(String, nullable=True)     # morning|afternoon|evening
    # ordering (integer gap scheme, see §4 note)
    ordinal       = Column(Integer, default=0, index=True)
    # links
    project_id    = Column(String, ForeignKey("plan_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    parent_id     = Column(String, ForeignKey("plan_items.id", ondelete="SET NULL"), nullable=True, index=True)
    recurring_id  = Column(String, ForeignKey("plan_recurring.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id    = Column(String, nullable=True)        # chat session that spawned/solves it
    source        = Column(String, default="user")       # user|agent|email|calendar
    # AI (mirror Note's reserved fields)
    ai_classification = Column(Text, nullable=True)      # JSON {project, priority, estimate, confidence}
    ai_content_hash   = Column(String, nullable=True)    # gate re-classification, avoid LLM spend
    project = relationship("PlanProject")
    __table_args__ = (
        Index('ix_plan_items_owner_day', 'owner', 'planned_day'),
        Index('ix_plan_items_owner_status', 'owner', 'status'),
    )

class PlanRecurring(TimestampMixin, Base):
    """Recurrence template; instances are materialized as PlanItems by a scheduled action."""
    __tablename__ = "plan_recurring"
    id         = Column(String, primary_key=True, index=True)
    owner      = Column(String, nullable=True, index=True)  # propagated to every materialized PlanItem (P0-3)
    title      = Column(String, nullable=False)
    rrule      = Column(String, nullable=True)   # iCal RRULE; expansion in Python (icalendar + dateutil)
    project_id = Column(String, nullable=True)
    priority   = Column(String, default="normal")
    estimate_minutes = Column(Integer, nullable=True)
    last_materialized = Column(String, nullable=True)  # 'YYYY-MM-DD' watermark for idempotency
    active     = Column(Boolean, default=True)
    __table_args__ = (Index('ix_plan_recurring_owner', 'owner', 'active'),)

class PlanDay(TimestampMixin, Base):
    """Per-day ritual state: morning plan committed, shutdown done, reflection note."""
    __tablename__ = "plan_days"
    id          = Column(String, primary_key=True, index=True)
    owner       = Column(String, nullable=True, index=True)
    day         = Column(String, nullable=False, index=True)  # 'YYYY-MM-DD' in user tz
    planned     = Column(Boolean, default=False)
    shutdown    = Column(Boolean, default=False)
    reflection  = Column(Text, nullable=True)   # LLM-generated evening recap (markdown)
    objective   = Column(Text, nullable=True)   # optional daily/weekly objective text
    __table_args__ = (Index('ix_plan_days_owner_day', 'owner', 'day', unique=True),)
```

**Table creation & migration (P1-5, P1-7):** New tables auto-create via `Base.metadata.create_all` in `init_db()` (`database.py:1796`) — no migration function for the initial tables. But because `backlog_horizon`/`preferred_time`/`actual_minutes` are likely to churn, **pre-commit to the migration-helper location now**: a `_migrate_plan_items(conn)` stub following the verified `PRAGMA table_info(...)` idempotent-ALTER pattern, called from `init_db()`, so Phase 5 columns don't silently fail to exist on upgraded DBs.

**`PlanDay` get-or-create (P1-5):** the unique `(owner, day)` index means morning-brief and shutdown writers must use explicit get-or-create: attempt `INSERT ... ON CONFLICT(owner, day) DO NOTHING` then `SELECT`, or catch `IntegrityError` and re-select. The design no longer assumes the row exists. Note SQLite treats each `NULL` as distinct in unique indexes — single-user `owner=""` is fine; **null owner is not** — a further reason to never write a null owner (P0-2).

---

## 5. Backend

**New files:**
- `routes/planner_routes.py` — `setup_planner_routes(task_scheduler)` factory returning an `APIRouter(prefix="/api/planner")`, copying `note_routes.py` structure: Pydantic `PlanItemCreate/Update`, `_item_to_dict`, and crucially **`_owner(request) = require_user(request) or None`** with the ownership gate **written against `_owner`'s output**:
  ```python
  user = _owner(request)                       # NOT require_user(request) directly
  if user is not None and row.owner != user:   # require_user returns "", never None — coercion is mandatory (P0-1)
      raise HTTPException(404)                  # 404 not 403 — don't leak existence
  ```
- `src/planner_logic.py` (business layer) — pure-Python deterministic engines: `surface_ranked(items, now, busy)` (SurfacingEngine port, tunable weights), `auto_schedule(items, busy, working_hours)` (greedy interval placer), `capacity(items, day, busy)` (planned-min vs available-min), `expand_recurring(template, horizon)` (RRULE expansion). **No LLM in this module.**
- `src/planner_ai.py` — the LLM-touching helpers (§7), each a single schema-constrained `llm_call_async`, **each wrapping its output in a Pydantic validate-and-coerce step (§7).**
- `src/planner_constants.py` *(or additions to `src/constants.py`)* — any persisted planner path (e.g. focus-timer state, reflection export) must be a constant per CLAUDE.md, never a `Path(DATA_DIR)/...` literal (P4-17). `dispatch_reminder`'s own `note_pings_{owner}.json` is reused as-is (already constant-backed).
- New entries in `src/builtin_actions.py` — `plan_evening_reflection`, `plan_materialize_recurring`, `plan_ping_due` — registered in `BUILTIN_ACTIONS` + seeded **paused** in `HOUSEKEEPING_DEFAULTS` (like email actions). Each must: implement a `TaskNoop` "nothing to do" path; be **idempotent** (recurrence materialization keyed off `last_materialized` watermark so a double-run doesn't double-create); and follow the **explicit per-owner fan-out** pattern from `task_scheduler.py:531-560`, stamping `PlanItem.owner = template.owner` on every materialized row (P0-3). This is treated as its own correctness surface (a Phase 4 workstream), not a footnote. **`plan_prioritize` is intentionally NOT seeded here — see §7.2 / §9 P2-9.**

**Wiring into `app.py`** (next to the verified `app.include_router(setup_note_routes(task_scheduler))`):
```python
from routes.planner_routes import setup_planner_routes
app.include_router(setup_planner_routes(task_scheduler))
```

**Endpoints (`/api/planner`):**
| Method | Path | Purpose |
|---|---|---|
| GET | `/items?day=&status=&project=` | list (owner-filtered, **`include_shared=False`** — P0-2) |
| POST | `/items` | create (stamps concrete owner) |
| GET/PUT/DELETE | `/items/{id}` | CRUD |
| POST | `/items/{id}/complete` | mark done, set `completed_at` |
| POST | `/items/{id}/plan` | set `planned_day` (or NULL = backlog) |
| POST | `/items/{id}/schedule` | set `scheduled_start`+`duration_minutes` |
| POST | `/items/reorder` | batch `ordinal` from drag |
| GET | `/day/{date}` | day view: items + capacity + busy intervals |
| GET | `/week/{date}` | 7-day grid |
| GET | `/surface?now=` | deterministic "what next" ranking |
| POST | `/capture` | **AI** NL→structured (§7.1) |
| POST | `/items/{id}/breakdown` | **AI** subtasks (§7.5) |
| POST | `/day/{date}/auto-schedule` | deterministic placer + **AI** narration (narration async — §7.3) |
| POST | `/day/{date}/shutdown` | trigger evening reflection |
| GET/POST | `/projects` , `/recurring` | CRUD for groupings/recurrence |

---

## 6. Frontend

**Nav** — add a `tool-planner-btn` `.list-item` in `static/index.html` near the verified `tool-notes-btn` (`:885`), with an inline monochrome SVG (calendar-with-checkmark, `stroke="currentColor" fill="none" stroke-width="2"`). Add `/planner` to the favicon `SHAPES` and `titles` maps and the `_routeOpen` table in `app.js`. **Do not touch `tool-tasks-btn`** — that's the scheduler.

**Module** — `static/js/planner.js`, exporting `openPanel/closePanel/togglePanel/isPanelOpen`, imported in `app.js` and wired to the new button (same pattern as `notesModule`). Add a `/planner` slash command in `slashCommands.js`.

**Visual style (enforced — `CLAUDE.md:97-105`):** only `var(--bg/--fg/--panel/--border/--red/--green/--warn)`; reuse `.admin-card`, `.list-item`, `.doc-action-icon-btn`, `.memory-search-input`, `.admin-toggle-row`; **no Unicode emoji anywhere — inline SVG only**; Fira Code; dark-default; light via CSS vars. New CSS goes after the `.notes-pane` block in `style.css`. **Run the app + attach a screenshot** before considering any visual work done.

**Views (one page, segmented control at top):**
```
┌─ Planner ───────────────────────[Today][Week][Backlog][⊟]┐
│ ┌─ Capture bar (NL console) ──────────────────────┐      │
│ │ + "ship report fri 2pm ~45m #work !urgent"      │      │  ← /capture → AI parse
│ └──────────────────────────────────────────────────┘      │
│  TODAY  ·  Mon 16 Jun     capacity: 5h20m / 6h ▓▓▓▓░       │  ← deterministic
│ ┌─ timeline (08:00–20:00) ──┬─ untimed today ──────┐      │
│ │ 09:00 ▮ Standup (cal)      │ ☐ Review PR  ~30m !  │      │
│ │ 10:00 ▮ Ship report ~45m   │ ☐ Email triage ~15m  │      │  drag untimed→timeline
│ │ 13:00 ▮ (free)             │ ☐ Plan Q3            │      │  = /schedule
│ └───────────────────────────┴──────────────────────┘      │
│ [⟳ Plan my day (AI)]   [◳ Shutdown & reflect (AI)]         │
└────────────────────────────────────────────────────────────┘
```
- **List + capture (Phase 1):** flat filterable list with the NL capture bar — the minimal differentiating surface (see resequenced roadmap §8).
- **Today (Phase 2):** calendar+task fusion (CalDAV busy blocks alongside time-blocked items), untimed column, drag-to-timebox, capacity bar.
- **Week (Phase 2):** 7 day-columns, drag items across days (sets `planned_day`).
- **Backlog (Phase 2):** `planned_day IS NULL`, grouped by project, with horizon triage.

Every AI-produced field renders as an **editable default** with visible provenance, never a silent auto-commit.

---

## 7. Local-AI-powered features (the differentiator)

**Golden rule:** the LLM does *language and judgment*; Python does *math, time, placement, recurrence, capacity*. Every AI call is **one-shot, single-purpose, schema-constrained JSON** — never an interactive agent loop. Feed the model **today's date + tiny context**, never history.

**Two cross-cutting corrections from review (apply to every row):**

- **Swap cost dominates inference cost (P2-8, P2-11).** With one model at a time on 24GB, the expensive event is unloading/loading a model (~10–30s), not tokens. So the model-selection rule is **"prefer the model already resident"**, not "always the smallest model." Concretely: interactive features (capture, breakdown, narration) should **resolve to the resident/default endpoint when one is loaded** (typically the chat model, e.g. JOSIEFIED-Qwen3-14B per the memory note) rather than force a swap to a 4B and back. A dedicated small model is only worth a swap for **batch/nighttime** work when no chat is in progress. The previous "default to 4B for capture" rule is corrected because, mid-chat, it *guarantees two swaps*.
- **`format: json` guarantees valid JSON, not correct fields (P2-10).** Every AI call passes its raw output through a **Pydantic validate-and-coerce layer in `planner_ai.py`**: clamp enums (`priority`/`backlog_horizon`/`preferred_time`) to the allowed set, fuzzy-match any returned `project` to an ID in the *provided* list (else drop it), discard unknown keys, and fall back to the unenriched manually-captured item if output is structurally valid but semantically junk. Treat the `consolidate_memory` Ollama-JSON pattern as a *starting point*, not a contract.

| Feature | Odysseus entry point | Model policy | IO shape | Latency / UX |
|---|---|---|---|---|
| **7.1 NL capture** | `POST /api/planner/capture` → one-shot `llm_call_async`, `temperature=0`, `format: json` | **resident/default endpoint if loaded** (`resolve_endpoint("default", owner)`); fall back to `"utility"` only if no model resident | in: `{text, today, projects[capped+MRU-ranked ≤~15], labels[]}` → out: `{title, due_phrase, estimate_minutes, project, priority}`. **Resolve the date phrase in Python (dateutil) in the user's tz** — small models do date *spotting* well, date *arithmetic* badly. Output goes through validate-and-coerce. | <1s on resident model; the item is created **immediately from the raw text regardless of AI** — AI only enriches editable fields. Capture never blocks on a swap. The highest-leverage feature. |
| **7.2 Auto-prioritize** | **Deferred to Phase 5 experiment — NOT shipped in the rituals.** | — | A nightly batch that emits noisy `importance:0–1` floats duplicates what the deterministic SurfacingEngine already derives from priority+due math; the part left to the LLM is the least reliable thing a small model does and the user can't see why a score changed. | Cut from the core roadmap to protect trust (P2-9). |
| **7.3 Daily-plan narration** | `POST /day/{date}/auto-schedule`: Python greedy placer (`planner_logic.auto_schedule`) returns placements **synchronously and instantly**; narration is generated **async/streamed in after** | resident/default endpoint; never force a swap for narration | in: placements + capacity numbers → out: short markdown plan + overload flags + 1–3 suggested deferrals. **Placement is 100% Python; LLM only narrates.** | Placement: instant. Narration: streamed in when ready (may be 2–5s, or longer if a swap is unavoidable — but the schedule is already usable). User reviews/commits (anti-autopilot). |
| **7.4 Time-block suggestion** | inside auto-schedule (Python only) | n/a | greedy interval-fit over CalDAV busy + working hours. | Instant; LLM never places blocks (would hallucinate overlaps). |
| **7.5 Task breakdown** | `POST /items/{id}/breakdown`, on-demand button | resident/default endpoint | in: `{title, notes, due}` → out: `{subtasks:[{title, estimate_minutes}]}` capped 3–7, validated/coerced. | Interactive but explicit (user clicked); spinner acceptable. If a larger model is genuinely better here and isn't resident, the swap is acceptable *because the user explicitly initiated it* (unlike capture). |
| **7.6 End-of-day reflection** | scheduled `builtin_action` `plan_evening_reflection` (evening cron, ship paused) + `POST /day/{date}/shutdown` | **batch/nighttime — a swap to a larger model is acceptable here** because no chat is in progress | in: `{completed[], incomplete[], planned_min, actual_min}` (counts+titles only) → out: markdown recap + carryover proposal + tomorrow's top-3 → `PlanDay.reflection` (via get-or-create). | Background batch; pairs with `daily_brief`/reminder dispatch. |

**Gating & context:** gate every classification behind `ai_content_hash` (verified pattern on `Note`) so unchanged items don't re-spend tokens. Cap and MRU-rank the `projects[]` list passed to the model (≤ ~15) — a small model's instruction-following degrades past ~20 options (P2-12). Reserve any deliberate larger-model use for the explicit on-demand (7.5) and nighttime batch (7.6) paths so it never thrashes a live chat.

---

## 8. Phased roadmap (each phase independently shippable)

**Resequenced per P3-14: the differentiator (NL capture) moves into the MVP; drag-reorder/timeline move to Phase 2.** The goal is to prove local-model capture works before investing in timeline/Week/capacity UI.

**Phase 0 — Issue first (if upstreaming).** Per the agent-PR policy, file a feature issue referencing #2072/#4063/#4066/#4381 before any PR. For the personal clone, skip to Phase 1.

**Phase 1 — Tracer bullet that proves the differentiator.** `PlanItem` + `PlanProject` models (with corrected owner-stamping + tz-string day + integer ordinal); `routes/planner_routes.py` CRUD + `/complete` + `/plan` + **`/capture`**; `planner.js` with a **flat List view + the NL capture bar** + manual create/complete; the validate-and-coerce layer; nav button + route + slash command; screenshot. Ships the headline AI feature end-to-end on the resident model. (~1 week.)

**Phase 2 — Planning surfaces + deterministic engines.** `planner_logic.py`: surfacing rank, greedy auto-scheduler, capacity bar; integer-ordinal drag-reorder (`/reorder`); Today timeline with CalDAV busy intervals; Week + Backlog views; time-block drag (`/schedule`). Zero new AI — all "smart" math is deterministic. (~1–1.5 weeks.)

**Phase 3 — Remaining interactive AI.** 7.5 breakdown and 7.3 plan narration (placement instant, narration async). `planner_ai.py` rounded out; `ai_content_hash` gating. (~1 week.)

**Phase 4 — Rituals & recurrence (its own correctness surface).** `PlanDay` + `PlanRecurring`; `builtin_actions` for evening reflection (7.6), `plan_materialize_recurring` (idempotent RRULE expansion with per-owner fan-out + watermark), `plan_ping_due` (reuse `dispatch_reminder`). Each with a `TaskNoop` no-op path. Seed paused in `HOUSEKEEPING_DEFAULTS`. Add the `_migrate_plan_items` helper now even if no column changes yet. (~1–1.5 weeks.)

**Phase 5 — Polish & experiments.** Focus/Pomodoro timer → `actual_minutes`; planned-vs-actual analytics; weekly objectives; **7.2 auto-prioritize as an opt-in experiment** (clearly explainable, off by default); optional Tide *import* MCP connector once Tide Slice 7 lands.

---

## 9. Risks, constraints & open questions

**P0 — Security / correctness (must fix before any code):**

1. **Ownership gate must be written against `_owner(request)`, not `require_user` (verified-wrong in the draft).** `require_user` (`auth_helpers.py:62-110`) returns the string `""` — never `None` — in single-user/anonymous/bypass cases. So `if user is not None and row.owner != user` written against `require_user`'s raw return makes `"" is not None` true and 404s *every legitimately-owned row*. The note routes avoid this only because `_owner(request) = require_user(request) or None` coerces `""`→`None`. **Mandate `_owner` coercion and write the gate against its output** (see §5). This is the footgun the note routes' `# SECURITY:` comment warns about.

2. **`owner_filter` default leaks null-owner rows across users.** `owner_filter(..., include_shared=True)` (default) returns `owner == user OR owner IS NULL`. In multi-user mode any null-owner `PlanItem` (minted by a `builtin_action`, agent capture, or a failed-owner-resolution import) becomes visible to **every** user — and the planner is exactly the surface where background actions mint rows. **Fix:** all planner list queries pass `include_shared=False`, and **every write path stamps a concrete owner** (never rely on the `nullable=True` default). Add a `sub_owner_scope` test: create a null-owner `PlanItem`, assert user B cannot see it.

3. **Scheduler owner-propagation contract.** `builtin_actions` run with `owner: str` and the existing loops fan out per-owner explicitly (`task_scheduler.py:531-560`). `plan_materialize_recurring` / `plan_evening_reflection` must follow that fan-out, and every materialized `PlanItem.owner` must equal the template owner. State this contract in code, not just prose — combined with P0-2 this is the actual cross-owner-leak site.

**P1 — Data model:**

4. **Ordinal precision (corrected in §4).** Use the integer-gap scheme with a specified rebalance, not raw float midpoints (float64 mantissa exhausts in ~50 same-position inserts and SQLite REAL collisions are silent). Test: insert at position 0 fifty times.
5. **`PlanDay` get-or-create (corrected in §4).** Explicit `INSERT ... ON CONFLICT DO NOTHING`/catch-`IntegrityError` for the unique `(owner, day)`; never write null owner (NULLs are distinct in SQLite unique indexes).
6. **Timezone (corrected in §4).** `planned_day`/`due_date`/`day` are `YYYY-MM-DD` strings in the user's tz, not server-naive `DateTime`, so day-membership equality is correct in Docker/UTC.
7. **Migration pre-commitment (corrected in §4/§8).** Add the idempotent `_migrate_plan_items` helper in Phase 4 even before a column changes, since Phase 5 fields will need it on upgraded DBs.

**P2 — Local-AI realism (corrected in §7):** swap cost dominates → prefer the resident model on interactive paths; validate-and-coerce every AI output (don't trust `format: json` for field/enum correctness); cap+MRU-rank project lists; placement instant + narration async; 7.2 deferred.

**P3 — Scope:** native build confirmed (P3-13); SurfacingEngine weights are a tunable starting point, not validated for this user; MVP resequenced to prove capture in Phase 1 (P3-14).

**P4 — Conventions:** any new persisted planner path goes through `src/constants.py` (P4-17). Visual style + agent-PR policy already correctly internalized — inline SVG, CSS vars, no emoji, screenshot, issue-first.

**Open questions needing the user's decision:**
1. **Namespace confirm:** `/api/planner` + `planner.js` + `tool-planner-btn` — agree?
2. **Relationship to Notes (pivotal — under-weighted in the draft, P3-15).** `Note` already has `items`, `due_date`, `repeat`, *and the exact AI fields* (`ai_classification`/`ai_content_hash`/`agent_session_id`) the planner mirrors. A new table genuinely buys time-blocking/capacity/recurrence-instance semantics `Note` lacks — but it also risks two parallel "list of things to do" surfaces and duplicated reminder/AI/CRUD code. **Recommendation: new `PlanItem` table, justified specifically on the time-block/capacity/recurrence fields, with a planned `Note → PlanItem` promotion path rather than two permanent silos.** Confirm this framing, or prefer extending `Note`.
3. **CalendarEvent linkage:** should a time-block also create a real CalDAV event (write-back via `caldav_writeback.py`), or stay planner-internal? Affects whether blocks appear on the phone calendar.
4. **Default models:** confirm which model is the "resident/default" assumption for interactive AI (memory note suggests JOSIEFIED-Qwen3-14B) and whether a small utility model is worth provisioning for nighttime batch (7.6).
5. **Reflection delivery:** browser-only, or also ntfy/email via `dispatch_reminder`?
6. **Tide import:** wanted eventually, or fully drop the Tide angle?
7. **Canonical timezone source (new, from P1-6):** where does the user's tz come from — a per-owner setting, the browser, or the server? Until decided, the implementation uses a per-owner tz setting with a logged server-tz fallback.

**Files referenced (verified to exist):** `core/database.py` (`Note`:1608, `ScheduledTask`:570, `TaskRun`:650, `create_all`:1796), `routes/note_routes.py` (`dispatch_reminder`:138), `routes/task_routes.py` (scheduler — do not extend), `static/js/tasks.js` (scheduler UI — do not extend), `static/index.html` (`tool-notes-btn`:885, `tool-tasks-btn`:895), `src/auth_helpers.py` (`require_user`:62, `owner_filter`:140), `src/endpoint_resolver.py` (`resolve_endpoint`:270), `src/llm_core.py` (think-suppress:1632), `src/builtin_actions.py`, `src/task_scheduler.py` (per-owner fan-out:531-560, `Semaphore(1)`), `src/caldav_sync.py`, `src/caldav_writeback.py`; `requirements.txt` (`icalendar`, `python-dateutil`).
