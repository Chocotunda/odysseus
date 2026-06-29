# Attendee Relationship Foundation (Slice 1) — Design

**Date:** 2026-06-29
**Status:** Approved — ready for implementation plan
**Repo/branch:** odysseus, `feat/attendee-relationship-foundation` off `dev`
**Decision record:** `docs/ai-context/2026-06-29-relationship-memory-layer-decision-record.md`
**Scope:** server-only. Tide display is Slice 2.

## Goal

On CalDAV sync, turn calendar **attendees** into graph entities: parse `ATTENDEE` from each event, match each attendee email to an existing `Person` (or auto-create one, tiered as calendar-sourced), and write `Meeting —attended_by→ Person`. Expose the resulting attendee list on the meeting-detail API. This is the data spine for the relationship-memory layer; everything is gated, reversible, and idempotent.

**Out of scope (deferred):** any Tide/UI change; per-attendee RSVP/role; removing attendance when an attendee drops off a re-synced event (additive only); email ingestion; LLM surfacing; multi-email merge; Area/context tagging.

## Components

### 1. Schema (two small migrations, mirroring `_migrate_add_calendar_account_id`)

- **`Person.source`** — `Column(String, nullable=False, default="manual")` on `core/hub_models.py:Person`. Values: `"manual"` (hand-curated, the default for all existing + API-created people) | `"calendar"` (auto-created from an attendee). Migration: `ALTER TABLE people ADD COLUMN source TEXT DEFAULT 'manual'` + backfill NULL→`'manual'`. Add `source` to the Person sync dict so the tier reaches Tide later (Slice 2 reads it; harmless now).
- **`CalendarCal.link_attendees`** — `Column(Boolean, nullable=False, default=True)` on `core/database.py:CalendarCal`. Migration: `ALTER TABLE calendars ADD COLUMN link_attendees BOOLEAN DEFAULT 1`. Per-calendar opt-out.

### 2. Config — the global flag

- `src/constants.py`: `CALENDAR_ATTENDEE_LINKING = _env_bool("ODYSSEUS_CALENDAR_ATTENDEE_LINKING", False)` (default **OFF**). Use the existing env-bool helper pattern in that file. When false, the whole feature is a strict no-op (no parsing, no writes).

### 3. New module `src/contacts.py` (pure-ish, the chokepoint + unit-testable)

```python
def normalize_email(raw: str) -> str | None
    # strip a leading 'mailto:' (case-insensitive), trim, lowercase;
    # return None if empty or not email-shaped (must contain '@').

def parse_attendees(vevent) -> list[tuple[str, str]]
    # From an icalendar VEVENT component, return [(email, display_name)] for
    # ATTENDEE (and ORGANIZER) properties. Each property value is 'mailto:addr';
    # display_name = its CN param if present, else the email local-part.
    # SKIP entries whose CUTYPE param is 'ROOM' or 'RESOURCE' (rooms aren't people).
    # icalendar: prop may be a single vCalAddress or a list; CN via prop.params.get('CN').
    # De-dupe within the event by normalized email.

def owner_self_addresses(db, owner: str) -> set[str]
    # Normalized set of the owner's OWN addresses to self-skip:
    # the admin user email (ODYSSEUS_ADMIN_USER / settings) + every
    # EmailAccount.imap_user for this owner. Lowercased.

def resolve_or_create_person_by_email(db, owner, email, name, *, cache: dict) -> str
    # Returns a Person id. Dedup order:
    #   1. cache[email] (within this sync run) -> return it
    #   2. existing LIVE Person for owner with normalized email match -> use it
    #      (do NOT flip its source; a 'manual' person stays manual)
    #   3. create Person(id=uuid, owner, name=name or email, email=email,
    #      source='calendar'); p.seq = next_person_seq(db, owner); db.add(p)
    # Populate cache[email] = id. Caller commits.

def link_event_attendees(db, owner, event_uid, vevent, *, self_addrs, cache) -> int
    # Orchestrates one event: for each (email,name) in parse_attendees(vevent)
    # where normalize_email(email) not in self_addrs:
    #   pid = resolve_or_create_person_by_email(...)
    #   L.add_link(db, owner, L.NODE_MEETING, event_uid, L.REL_ATTENDED_BY,
    #              L.NODE_PERSON, pid)   # idempotent: revives/dedups by tuple
    # Returns count linked. No commit here (caller owns the transaction).
```

`add_link` (`src/links.py`) already reviving/deduping by the `(owner, from, rel, to)` tuple means re-sync never duplicates an edge, and the calendar `attended_by` collapses with any meeting-note `attended_by` for the same pair — one fact (decision-record §5).

### 4. Hook into CalDAV sync

In `src/caldav_sync.py` `_sync_blocking`, inside the per-VEVENT loop (after the existing upsert of `existing`/`new_ev`, ~line 423/436), add — guarded so it's a strict no-op when off:

```python
if CALENDAR_ATTENDEE_LINKING and getattr(cal, "link_attendees", True):
    link_event_attendees(db, owner, uid_val, comp,
                         self_addrs=self_addrs, cache=person_cache)
```

`self_addrs = owner_self_addresses(db, owner)` and `person_cache = {}` are computed once per `_sync_blocking` call (before the event loop), not per event. The existing sync already holds the `cal` (CalendarCal) row and commits per batch — attendee links join that same transaction.

### 5. Endpoint — expose attendees on meeting detail

Extend `GET /api/meeting-notes/meeting/{uid}` (`routes/meeting_notes_routes.py`, the route shipped last slice). After building the event dict, attach:

```python
d["attendees"] = [
    {"id": p.id, "name": p.name, "email": p.email, "source": p.source}
    for p in _attendees_for_meeting(db, owner, uid)   # via attended_by links -> Person
]
```

`_attendees_for_meeting` queries live `attended_by` links (`from_type=meeting, from_id=uid`) → loads the owner's live People for those ids → returns them (name-sorted). Owner-scoped; tombstoned people/links excluded. No new scope (still `calendar:read`/`notes:read`).

## Data flow

```
CalDAV VEVENT ──parse_attendees──▶ [(email, name), …]
   │  (skip rooms/resources, skip owner self-addresses)
   ▼
normalize_email ──▶ resolve_or_create_person_by_email (dedup by email, cache)
   │                         └─ existing Person  OR  new Person(source='calendar', seq)
   ▼
add_link  Meeting(uid) ─attended_by→ Person(id)   (idempotent, deduped)
   ▼
GET /api/meeting-notes/meeting/{uid}  ──▶  {…event…, attendees:[{id,name,email,source}]}
        (and the existing reverse-Link spine already lets a Person aggregate these meetings)
```

## Error handling / edge cases

- **Feature off / calendar opted out:** strict no-op (no parse, no writes). Default state on merge — nothing changes until `ODYSSEUS_CALENDAR_ATTENDEE_LINKING=1`.
- **No `ATTENDEE` on the event** (most personal/solo events): `parse_attendees` returns `[]` → nothing linked.
- **Malformed / non-mailto attendee** (e.g. a URL, empty CN): `normalize_email` returns `None` → that attendee skipped, others still processed. Never throw out of the sync loop — wrap per-event linking so one bad event can't abort the sync (log + continue).
- **Room / resource attendees** (`CUTYPE=ROOM|RESOURCE`): skipped — they aren't people.
- **Owner self:** skipped via `self_addrs` — you never become an attendee-Person on your own meeting.
- **Re-sync idempotency:** `cache` prevents double-create within a run; email-match prevents cross-run dupes; `add_link` dedups the edge. Re-running a full sync is a no-op on already-linked data.
- **Existing manual Person matched by email:** linked, but `source` stays `manual` (we never downgrade a curated person to calendar).
- **Attendee removed from a re-synced event:** the stale `attended_by` edge remains (additive-only; decision-record §7). Documented limitation.

## Testing (pytest)

Unit (`src/contacts.py`, no DB or fake session in the established `_FakeSession` style):
- `normalize_email`: `mailto:` strip, case-fold, trim, reject non-email/empty.
- `parse_attendees`: single vs list ATTENDEE; CN→name vs local-part fallback; ROOM/RESOURCE skipped; ORGANIZER included; in-event email de-dupe. (Build VEVENTs with the `icalendar` lib.)
- `owner_self_addresses`: admin email + EmailAccount imap_user collected + lowercased.
- `resolve_or_create_person_by_email`: cache hit; existing-Person match (source unchanged); create path sets `source='calendar'` + a seq; two different emails → two People; same email twice → one.
- `link_event_attendees`: links non-self attendees, skips self, returns count; calls `add_link` with `(NODE_MEETING, uid, REL_ATTENDED_BY, NODE_PERSON, pid)`.

Integration:
- Endpoint returns `attendees` for a meeting with linked people; owner isolation (another owner's meeting → 404 already covered); empty list when none.
- Migrations apply idempotently (run twice) and backfill `source='manual'` / `link_attendees=1`.
- Gate: with the flag off, a sync of an event with attendees creates **zero** People/links.

## Global constraints

- Paths/config via `src/constants.py` (the flag), never literals.
- Owner-scoping is a security boundary — every People/Link query filters by `owner`.
- Any Person write sets `seq = next_person_seq(db, owner)`; any Link write goes through `src/links.add_link` (which sets link seq). Never hand-write seq.
- Feature **disabled by default**; merging changes no behavior until the env flag is set.
- Conventional Commits; commit-body trailers per project convention.
