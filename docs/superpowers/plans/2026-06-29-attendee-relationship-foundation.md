# Attendee Relationship Foundation (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On CalDAV sync, turn calendar `ATTENDEE`s into email-keyed `Person` nodes (tiered by `source`) linked to their meeting via `attended_by`, and expose attendees on the meeting-detail API — server-only, gated, idempotent.

**Architecture:** Two small column migrations (`Person.source`, `CalendarCal.link_attendees`) + a global feature flag; a new pure-ish `src/contacts.py` chokepoint (parse → normalize → dedup-by-email → match/create → `add_link`); a guarded call inside `caldav_sync._sync_blocking`; an extension to `GET /api/meeting-notes/meeting/{uid}`.

**Tech Stack:** FastAPI, SQLAlchemy, the `icalendar` lib (already a dependency, used in `caldav_sync`), pytest.

## Global Constraints

- Repo/worktree: `/Users/kganpat/Projects/odysseus-attendee-foundation` (branch `feat/attendee-relationship-foundation`). Tests: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest <file> -v` run with cwd = the worktree.
- **Disabled by default:** `ODYSSEUS_CALENDAR_ATTENDEE_LINKING` unset ⇒ the whole feature is a strict no-op. Merging changes no behavior.
- Owner-scoping is a security boundary — every People/Link query filters by `owner`.
- Any `Person` write sets `seq = next_person_seq(db, owner)`. Every `attended_by` edge goes through `src.links.add_link` (which sets the link seq + dedups/revives by tuple) — never hand-write a Link or its seq.
- Identity = **normalized email** (strip a leading `mailto:` case-insensitively, `.strip()`, `.lower()`); must contain `@` to be valid.
- A matched **existing** Person keeps its current `source` (never downgrade a `manual` person to `calendar`).
- Conventional Commits; end each commit body with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01PVkd2jDLGNwYBu7ZNMCJZt`

---

### Task 1: Schema + config foundation

**Files:**
- Modify: `core/hub_models.py` (add `Person.source` column)
- Modify: `core/database.py` (add `CalendarCal.link_attendees` column; two `_migrate_*` functions; register them in `init_db`)
- Modify: `routes/people_routes.py` (`_person_to_dict` includes `source`)
- Modify: `src/constants.py` (the feature flag)
- Test: `tests/test_attendee_schema.py` (new)

**Interfaces:**
- Produces: `Person.source: str` (`"manual"` default | `"calendar"`); `CalendarCal.link_attendees: bool` (default `True`); `src.constants.CALENDAR_ATTENDEE_LINKING: bool`; `_person_to_dict` output now contains `"source"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_attendee_schema.py`:

```python
"""Slice 1 schema/config: Person.source, CalendarCal.link_attendees, feature flag."""
import os
import sqlite3
import importlib
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import Person, next_person_seq


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_person_source_defaults_manual_and_in_dict():
    import routes.people_routes as pr
    db = _db()
    p = Person(id=str(uuid.uuid4()), owner="alice", name="Bob", email="b@x.com")
    p.seq = next_person_seq(db, "alice")
    db.add(p); db.commit()
    assert p.source == "manual"                      # model default
    d = pr._person_to_dict(p)
    assert d["source"] == "manual"                   # exposed in sync dict


def test_calendar_link_attendees_defaults_true():
    from core.database import CalendarCal
    db = _db()
    c = CalendarCal(id="c1", owner="alice", name="Work")
    db.add(c); db.commit()
    assert c.link_attendees is True


def test_flag_default_off():
    import src.constants as constants
    importlib.reload(constants)
    assert constants.CALENDAR_ATTENDEE_LINKING is False


def test_person_source_migration_idempotent(tmp_path, monkeypatch):
    # Simulate an OLD db where `people` has no `source` column.
    dbfile = tmp_path / "old.db"
    conn = sqlite3.connect(dbfile)
    conn.execute("CREATE TABLE people (id TEXT PRIMARY KEY, owner TEXT, name TEXT)")
    conn.execute("INSERT INTO people (id, owner, name) VALUES ('p1','alice','Old')")
    conn.commit(); conn.close()

    import core.database as cdb
    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{dbfile}")
    cdb._migrate_add_person_source()
    cdb._migrate_add_person_source()   # idempotent: second run must not raise

    conn = sqlite3.connect(dbfile)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(people)")]
    val = conn.execute("SELECT source FROM people WHERE id='p1'").fetchone()[0]
    conn.close()
    assert "source" in cols
    assert val == "manual"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_attendee_schema.py -v`
Expected: FAIL — `Person` has no attribute `source` / `_migrate_add_person_source` not defined / `CALENDAR_ATTENDEE_LINKING` missing.

- [ ] **Step 3: Write minimal implementation**

In `core/hub_models.py`, add to the `Person` class (next to `role`):

```python
    source      = Column(String, nullable=False, default="manual")  # manual | calendar | email — provenance tier
```

In `core/database.py`, add to the `CalendarCal` class (next to `account_id`):

```python
    link_attendees = Column(Boolean, nullable=False, default=True)  # opt-out: feed attendee→Person linking
```

In `core/database.py`, add two migrations (mirror `_migrate_add_calendar_account_id`):

```python
def _migrate_add_person_source():
    """Add `source` (manual|calendar|email) to people for the relationship tier. Idempotent."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(people)").fetchall()]
        if columns and "source" not in columns:
            conn.execute("ALTER TABLE people ADD COLUMN source TEXT DEFAULT 'manual'")
            conn.execute("UPDATE people SET source='manual' WHERE source IS NULL")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'source' column to people")
    except Exception as e:
        logging.getLogger(__name__).warning(f"people.source migration failed: {e}")
    finally:
        if conn:
            conn.close()


def _migrate_add_calendar_link_attendees():
    """Add `link_attendees` (per-calendar opt-out) to calendars. Idempotent."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(calendars)").fetchall()]
        if columns and "link_attendees" not in columns:
            conn.execute("ALTER TABLE calendars ADD COLUMN link_attendees BOOLEAN DEFAULT 1")
            conn.execute("UPDATE calendars SET link_attendees=1 WHERE link_attendees IS NULL")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'link_attendees' column to calendars")
    except Exception as e:
        logging.getLogger(__name__).warning(f"calendars.link_attendees migration failed: {e}")
    finally:
        if conn:
            conn.close()
```

In `core/database.py` `init_db()`, register both right after the `_migrate_add_calendar_account_id()` call:

```python
    _migrate_add_person_source()
    _migrate_add_calendar_link_attendees()
```

In `routes/people_routes.py` `_person_to_dict`, add `source` to the returned dict (next to `email`):

```python
        "source": getattr(p, "source", "manual"),
```

In `src/constants.py` (near the other env flags like `CLEANUP_ENABLED`):

```python
CALENDAR_ATTENDEE_LINKING = os.getenv("ODYSSEUS_CALENDAR_ATTENDEE_LINKING", "False").lower() in ("1", "true", "yes")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_attendee_schema.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-attendee-foundation
git add core/hub_models.py core/database.py routes/people_routes.py src/constants.py tests/test_attendee_schema.py
git commit -m "$(cat <<'EOF'
feat(people): Person.source tier + CalendarCal.link_attendees + attendee-linking flag

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVkd2jDLGNwYBu7ZNMCJZt
EOF
)"
```

---

### Task 2: Email + attendee parsing (pure, no DB)

**Files:**
- Create: `src/contacts.py`
- Test: `tests/test_contacts_parse.py` (new)

**Interfaces:**
- Produces:
  - `normalize_email(raw: str) -> str | None` — strip leading `mailto:` (case-insensitive), trim, lowercase; `None` if empty or lacks `@`.
  - `parse_attendees(vevent) -> list[tuple[str, str]]` — `[(email, display_name)]` from `ATTENDEE` + `ORGANIZER`; name = `CN` param else email local-part; skip `CUTYPE` in `{ROOM, RESOURCE}`; de-duped within the event by normalized email; raw (un-normalized) email returned as element 0 but only valid (normalizable) entries are included.

- [ ] **Step 1: Write the failing test**

Create `tests/test_contacts_parse.py`:

```python
"""Pure parsing: normalize_email + parse_attendees (icalendar, no DB)."""
from icalendar import Event, vCalAddress, vText

from src.contacts import normalize_email, parse_attendees


def test_normalize_email():
    assert normalize_email("MAILTO:Foo@Bar.com") == "foo@bar.com"
    assert normalize_email("mailto:a@b.io") == "a@b.io"
    assert normalize_email("  C@D.com ") == "c@d.com"
    assert normalize_email("not-an-email") is None
    assert normalize_email("") is None
    assert normalize_email("mailto:") is None


def _attendee(addr, cn=None, cutype=None):
    a = vCalAddress(addr)
    if cn:
        a.params["CN"] = vText(cn)
    if cutype:
        a.params["CUTYPE"] = vText(cutype)
    return a


def test_parse_attendees_cn_and_fallback():
    ev = Event()
    ev.add("attendee", _attendee("mailto:wiggert@firm.nl", cn="Wiggert Loonstra"))
    ev.add("attendee", _attendee("mailto:npd@gmail.com"))  # no CN -> local-part
    out = parse_attendees(ev)
    assert ("wiggert@firm.nl", "Wiggert Loonstra") in out
    assert ("npd@gmail.com", "npd") in out


def test_parse_attendees_skips_rooms_and_dedupes():
    ev = Event()
    ev.add("attendee", _attendee("mailto:room1@firm.nl", cn="Room 1", cutype="ROOM"))
    ev.add("attendee", _attendee("mailto:dup@firm.nl", cn="Dup"))
    ev.add("attendee", _attendee("MAILTO:DUP@firm.nl", cn="Dup2"))  # same email, different case
    ev.add("organizer", _attendee("mailto:org@firm.nl", cn="Org"))
    out = parse_attendees(ev)
    emails = [e for e, _ in out]
    assert "room1@firm.nl" not in emails          # room skipped
    assert emails.count("dup@firm.nl") == 1       # deduped case-insensitively
    assert "org@firm.nl" in emails                # organizer included


def test_parse_attendees_empty():
    assert parse_attendees(Event()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_contacts_parse.py -v`
Expected: FAIL — `No module named 'src.contacts'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/contacts.py`:

```python
"""Calendar-attendee → Person resolution (Slice 1 of the relationship-memory layer).

Pure parsing here; DB-touching resolution/linking lives in the same module but is
split into separate functions so the parse layer is unit-testable without a DB.
"""
from __future__ import annotations

from typing import List, Optional, Tuple


def normalize_email(raw: object) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower().startswith("mailto:"):
        s = s[len("mailto:"):]
    s = s.strip().lower()
    if not s or "@" not in s:
        return None
    return s


def _name_from(addr_value: str, cn: Optional[str]) -> str:
    if cn:
        return str(cn).strip()
    norm = normalize_email(addr_value) or str(addr_value)
    return norm.split("@", 1)[0]


def parse_attendees(vevent) -> List[Tuple[str, str]]:
    """[(normalized_email, display_name)] from ATTENDEE + ORGANIZER. Skips
    ROOM/RESOURCE; de-dupes by normalized email (first occurrence wins)."""
    out: List[Tuple[str, str]] = []
    seen = set()
    for prop_name in ("attendee", "organizer"):
        prop = vevent.get(prop_name)
        if prop is None:
            continue
        items = prop if isinstance(prop, list) else [prop]
        for a in items:
            params = getattr(a, "params", {}) or {}
            cutype = str(params.get("CUTYPE", "")).upper()
            if cutype in ("ROOM", "RESOURCE"):
                continue
            email = normalize_email(a)
            if not email or email in seen:
                continue
            seen.add(email)
            out.append((email, _name_from(str(a), params.get("CN"))))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_contacts_parse.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-attendee-foundation
git add src/contacts.py tests/test_contacts_parse.py
git commit -m "$(cat <<'EOF'
feat(contacts): normalize_email + parse_attendees (pure icalendar parsing)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVkd2jDLGNwYBu7ZNMCJZt
EOF
)"
```

---

### Task 3: Contacts DB layer — self-addresses + resolve/create

**Files:**
- Modify: `src/contacts.py` (add two DB functions)
- Test: `tests/test_contacts_resolve.py` (new)

**Interfaces:**
- Consumes: `normalize_email` (Task 2); `Person`, `next_person_seq` from `core.hub_models`; `EmailAccount` from `core.database`.
- Produces:
  - `owner_self_addresses(db, owner: str) -> set[str]` — normalized owner addresses: every `EmailAccount.imap_user` for the owner, plus the owner string itself if it normalizes to an email, plus any in env `ODYSSEUS_OWNER_EMAILS` (comma-sep).
  - `resolve_or_create_person_by_email(db, owner, email, name, *, cache: dict) -> str` — returns a `Person.id`. Dedup: `cache[email]` → live Person whose normalized email matches → else create `Person(source="calendar")` with `next_person_seq`. Existing person's `source` is never changed. Caller commits.

- [ ] **Step 1: Write the failing test**

Create `tests/test_contacts_resolve.py`:

```python
"""DB layer: owner_self_addresses + resolve_or_create_person_by_email."""
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, EmailAccount
from core.hub_models import Person, next_person_seq
from src.contacts import owner_self_addresses, resolve_or_create_person_by_email


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _person(db, owner, name, email, source="manual"):
    p = Person(id=str(uuid.uuid4()), owner=owner, name=name, email=email, source=source)
    p.seq = next_person_seq(db, owner)
    db.add(p); db.commit()
    return p


def test_self_addresses_from_email_accounts(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_OWNER_EMAILS", raising=False)
    db = _db()
    db.add(EmailAccount(id="e1", owner="alice", imap_user="Alice@Gmail.com"))
    db.commit()
    addrs = owner_self_addresses(db, "alice")
    assert "alice@gmail.com" in addrs


def test_self_addresses_env_override(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OWNER_EMAILS", "me@work.com, alias@x.io")
    db = _db()
    addrs = owner_self_addresses(db, "alice")
    assert {"me@work.com", "alias@x.io"} <= addrs


def test_resolve_matches_existing_person_without_downgrade():
    db = _db()
    existing = _person(db, "alice", "Wiggert", "wiggert@firm.nl", source="manual")
    cache = {}
    pid = resolve_or_create_person_by_email(db, "alice", "WIGGERT@firm.nl", "Wiggert L", cache=cache)
    db.commit()
    assert pid == existing.id
    db.refresh(existing)
    assert existing.source == "manual"            # never downgraded
    assert cache["wiggert@firm.nl"] == existing.id


def test_resolve_creates_calendar_person_and_dedupes():
    db = _db()
    cache = {}
    a = resolve_or_create_person_by_email(db, "alice", "new@x.com", "New Person", cache=cache)
    db.commit()
    b = resolve_or_create_person_by_email(db, "alice", "NEW@x.com", "Dup", cache=cache)  # same email
    db.commit()
    assert a == b                                  # one person, deduped via cache
    p = db.query(Person).filter(Person.id == a).first()
    assert p.source == "calendar"
    assert p.email == "new@x.com" and p.name == "New Person"
    assert (p.seq or 0) > 0                         # seq stamped


def test_resolve_owner_scoped():
    db = _db()
    _person(db, "bob", "Bob's Wiggert", "wiggert@firm.nl")   # different owner
    cache = {}
    pid = resolve_or_create_person_by_email(db, "alice", "wiggert@firm.nl", "W", cache=cache)
    db.commit()
    p = db.query(Person).filter(Person.id == pid).first()
    assert p.owner == "alice"                       # created fresh for alice, not bob's
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_contacts_resolve.py -v`
Expected: FAIL — `cannot import name 'owner_self_addresses'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/contacts.py`:

```python
import os
import uuid as _uuid


def owner_self_addresses(db, owner: str) -> set:
    """Normalized set of the owner's OWN addresses, to self-skip."""
    from core.database import EmailAccount  # local import avoids import cycle at module load
    addrs = set()
    for acc in db.query(EmailAccount).filter(EmailAccount.owner == owner).all():
        e = normalize_email(getattr(acc, "imap_user", None))
        if e:
            addrs.add(e)
    owner_as_email = normalize_email(owner)
    if owner_as_email:
        addrs.add(owner_as_email)
    for extra in os.getenv("ODYSSEUS_OWNER_EMAILS", "").split(","):
        e = normalize_email(extra)
        if e:
            addrs.add(e)
    return addrs


def resolve_or_create_person_by_email(db, owner, email, name, *, cache: dict) -> str:
    """Return a Person id for `email`, creating a calendar-sourced Person if none
    exists for this owner. Dedup via cache (this run) then a live email match.
    Caller owns the commit."""
    from core.hub_models import Person, next_person_seq
    key = normalize_email(email)
    if key is None:
        raise ValueError(f"un-normalizable email: {email!r}")
    if key in cache:
        return cache[key]
    existing = (
        db.query(Person)
        .filter(Person.owner == owner, Person.deleted_at.is_(None))
        .filter(func.lower(Person.email) == key)
        .first()
    )
    if existing is not None:
        cache[key] = existing.id          # do NOT touch existing.source
        return existing.id
    p = Person(id=str(_uuid.uuid4()), owner=owner, name=(name or key), email=key, source="calendar")
    p.seq = next_person_seq(db, owner)
    db.add(p)
    cache[key] = p.id
    return p.id
```

Add the `func` import at the top of `src/contacts.py` (with the other imports):

```python
from sqlalchemy import func
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_contacts_resolve.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-attendee-foundation
git add src/contacts.py tests/test_contacts_resolve.py
git commit -m "$(cat <<'EOF'
feat(contacts): owner_self_addresses + resolve_or_create_person_by_email

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVkd2jDLGNwYBu7ZNMCJZt
EOF
)"
```

---

### Task 4: `link_event_attendees` orchestration

**Files:**
- Modify: `src/contacts.py` (add the orchestration function)
- Test: `tests/test_contacts_link.py` (new)

**Interfaces:**
- Consumes: `parse_attendees`, `resolve_or_create_person_by_email` (Tasks 2-3); `src.links` constants/`add_link`.
- Produces: `link_event_attendees(db, owner, event_uid, vevent, *, self_addrs: set, cache: dict) -> int` — for each parsed attendee whose email ∉ `self_addrs`, resolve→Person and `add_link(db, owner, NODE_MEETING, event_uid, REL_ATTENDED_BY, NODE_PERSON, pid)`. Returns the count linked. No commit (the sync batch owns the transaction). Never raises out for a single bad attendee.

- [ ] **Step 1: Write the failing test**

Create `tests/test_contacts_link.py`:

```python
"""Orchestration: link_event_attendees writes attended_by edges, skips self."""
import uuid
from icalendar import Event, vCalAddress, vText
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.hub_models import Person, Link
import src.links as L
from src.contacts import link_event_attendees


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _att(addr, cn=None):
    a = vCalAddress(addr)
    if cn:
        a.params["CN"] = vText(cn)
    return a


def _event(*attendees):
    ev = Event()
    for a in attendees:
        ev.add("attendee", a)
    return ev


def test_links_non_self_attendees_and_skips_self():
    db = _db()
    ev = _event(_att("mailto:wiggert@firm.nl", "Wiggert"),
                _att("mailto:me@work.com", "Me"))
    n = link_event_attendees(db, "alice", "evt-1", ev,
                             self_addrs={"me@work.com"}, cache={})
    db.commit()
    assert n == 1                                   # self skipped
    links = db.query(Link).filter(Link.rel == L.REL_ATTENDED_BY,
                                  Link.from_id == "evt-1",
                                  Link.deleted_at.is_(None)).all()
    assert len(links) == 1
    pid = links[0].to_id
    p = db.query(Person).filter(Person.id == pid).first()
    assert p.email == "wiggert@firm.nl" and p.source == "calendar"
    assert links[0].from_type == L.NODE_MEETING and links[0].to_type == L.NODE_PERSON


def test_idempotent_on_resync():
    db = _db()
    ev = _event(_att("mailto:a@x.com", "A"))
    link_event_attendees(db, "alice", "evt-2", ev, self_addrs=set(), cache={}); db.commit()
    link_event_attendees(db, "alice", "evt-2", ev, self_addrs=set(), cache={}); db.commit()
    links = db.query(Link).filter(Link.rel == L.REL_ATTENDED_BY,
                                  Link.from_id == "evt-2",
                                  Link.deleted_at.is_(None)).all()
    assert len(links) == 1                          # no duplicate edge
    people = db.query(Person).filter(Person.email == "a@x.com").all()
    assert len(people) == 1                         # no duplicate person


def test_empty_event_no_writes():
    db = _db()
    n = link_event_attendees(db, "alice", "evt-3", Event(), self_addrs=set(), cache={})
    db.commit()
    assert n == 0
    assert db.query(Link).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_contacts_link.py -v`
Expected: FAIL — `cannot import name 'link_event_attendees'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/contacts.py`:

```python
import logging as _logging

_log = _logging.getLogger(__name__)


def link_event_attendees(db, owner, event_uid, vevent, *, self_addrs: set, cache: dict) -> int:
    """Resolve each non-self attendee to a Person and add a Meeting→Person
    attended_by edge. Idempotent (add_link dedups). Caller commits."""
    import src.links as L
    linked = 0
    for email, name in parse_attendees(vevent):
        if email in self_addrs:
            continue
        try:
            pid = resolve_or_create_person_by_email(db, owner, email, name, cache=cache)
            L.add_link(db, owner, L.NODE_MEETING, event_uid, L.REL_ATTENDED_BY, L.NODE_PERSON, pid)
            linked += 1
        except Exception as e:   # one bad attendee must not abort the whole sync
            _log.warning("attendee link failed for %s on %s: %s", email, event_uid, e)
    return linked
```

> Note: `add_link` may `commit()` internally (it revives/inserts with a fresh seq). That is fine here — the surrounding sync already commits per batch; an extra commit mid-batch does not break correctness.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_contacts_link.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-attendee-foundation
git add src/contacts.py tests/test_contacts_link.py
git commit -m "$(cat <<'EOF'
feat(contacts): link_event_attendees — Meeting→Person attended_by edges

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVkd2jDLGNwYBu7ZNMCJZt
EOF
)"
```

---

### Task 5: Wire into CalDAV sync (gated)

**Files:**
- Modify: `src/caldav_sync.py` (`_sync_blocking`: compute self-addrs + cache once, call `link_event_attendees` per event behind the flag + per-calendar opt-out)
- Test: `tests/test_caldav_attendee_hook.py` (new)

**Interfaces:**
- Consumes: `CALENDAR_ATTENDEE_LINKING` (`src.constants`), `link_event_attendees`/`owner_self_addresses` (`src.contacts`).
- Produces: during sync, attendee edges for events on calendars with `link_attendees=True` — **only when the flag is on**.

- [ ] **Step 1: Write the failing test**

Create `tests/test_caldav_attendee_hook.py`. (We don't spin up real CalDAV; we test the gate + that the hook calls `link_event_attendees` when enabled, via a tiny extracted guard helper so the wiring is unit-testable.)

```python
"""The sync hook is gated by the flag AND the calendar's link_attendees."""
from types import SimpleNamespace
from icalendar import Event
import src.caldav_sync as cs


def test_maybe_link_attendees_respects_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "CALENDAR_ATTENDEE_LINKING", False)
    monkeypatch.setattr(cs, "link_event_attendees",
                        lambda *a, **k: calls.append(a) or 0)
    cal = SimpleNamespace(link_attendees=True)
    cs._maybe_link_attendees(None, "alice", "evt", Event(), cal, self_addrs=set(), cache={})
    assert calls == []                              # flag off -> no call


def test_maybe_link_attendees_respects_calendar_optout(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "CALENDAR_ATTENDEE_LINKING", True)
    monkeypatch.setattr(cs, "link_event_attendees",
                        lambda *a, **k: calls.append(a) or 0)
    cal = SimpleNamespace(link_attendees=False)
    cs._maybe_link_attendees(None, "alice", "evt", Event(), cal, self_addrs=set(), cache={})
    assert calls == []                              # calendar opted out -> no call


def test_maybe_link_attendees_calls_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "CALENDAR_ATTENDEE_LINKING", True)
    monkeypatch.setattr(cs, "link_event_attendees",
                        lambda db, owner, uid, ev, **k: calls.append((owner, uid)) or 1)
    cal = SimpleNamespace(link_attendees=True)
    cs._maybe_link_attendees(None, "alice", "evt-9", Event(), cal, self_addrs=set(), cache={})
    assert calls == [("alice", "evt-9")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_caldav_attendee_hook.py -v`
Expected: FAIL — `module 'src.caldav_sync' has no attribute '_maybe_link_attendees'`.

- [ ] **Step 3: Write minimal implementation**

At the top of `src/caldav_sync.py` (with the other imports), add:

```python
from src.constants import CALENDAR_ATTENDEE_LINKING
from src.contacts import link_event_attendees, owner_self_addresses
```

Add a module-level guard helper (so the gate is unit-testable and the loop stays readable):

```python
def _maybe_link_attendees(db, owner, event_uid, vevent, cal, *, self_addrs, cache):
    """Link calendar attendees to People iff the global flag is on AND this
    calendar isn't opted out. Best-effort: never raises into the sync loop."""
    if not CALENDAR_ATTENDEE_LINKING:
        return
    if not getattr(cal, "link_attendees", True):
        return
    try:
        link_event_attendees(db, owner, event_uid, vevent, self_addrs=self_addrs, cache=cache)
    except Exception as e:   # belt-and-suspenders; the inner fn already guards per-attendee
        logging.getLogger(__name__).warning("attendee linking failed for %s: %s", event_uid, e)
```

In `_sync_blocking`, BEFORE the per-VEVENT loop begins (once per call), compute:

```python
        attendee_self = owner_self_addresses(db, owner) if CALENDAR_ATTENDEE_LINKING else set()
        attendee_cache: dict = {}
```

Inside the per-VEVENT loop, AFTER the event upsert (`existing`/`new_ev` block, right before the surrounding `db.commit()`), add:

```python
                    _maybe_link_attendees(db, owner, uid_val, comp, local_cal,
                                          self_addrs=attendee_self, cache=attendee_cache)
```

(`owner`, `uid_val`, `comp`, and `local_cal` are all already in scope at that point in `_sync_blocking`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_caldav_attendee_hook.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-attendee-foundation
git add src/caldav_sync.py tests/test_caldav_attendee_hook.py
git commit -m "$(cat <<'EOF'
feat(caldav): link calendar attendees to People during sync (flag-gated, opt-out)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVkd2jDLGNwYBu7ZNMCJZt
EOF
)"
```

---

### Task 6: Expose attendees on the meeting-detail endpoint

**Files:**
- Modify: `routes/meeting_notes_routes.py` (`get_meeting_detail`: attach `attendees`; add `_attendees_for_meeting` helper)
- Test: `tests/test_meeting_detail_attendees.py` (new)

**Interfaces:**
- Consumes: `attended_by` edges (Tasks 4-5); `Person`, `Link`, `src.links` constants.
- Produces: `GET /api/meeting-notes/meeting/{uid}` response now includes `"attendees": [{"id","name","email","source"}]` (owner-scoped, live only, name-sorted; `[]` when none).

- [ ] **Step 1: Write the failing test**

Create `tests/test_meeting_detail_attendees.py` (drive the endpoint fn directly + an in-memory DB, mirroring the existing meeting-notes test style):

```python
"""GET /meeting/{uid} returns the attendee roster from attended_by edges."""
import uuid
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, CalendarCal, CalendarEvent
from core.hub_models import Person, next_person_seq
import src.links as L
import routes.meeting_notes_routes as mnr
from datetime import datetime


def _sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    cal = CalendarCal(id="cal1", owner="alice", name="Work")
    db.add(cal)
    ev = CalendarEvent(uid="evt-1", calendar_id="cal1", summary="Team Tea Time",
                       dtstart=datetime(2026, 7, 24, 13, 0), dtend=datetime(2026, 7, 24, 13, 30),
                       all_day=False, is_utc=True)
    db.add(ev)
    p = Person(id=str(uuid.uuid4()), owner="alice", name="Wiggert", email="w@firm.nl", source="calendar")
    p.seq = next_person_seq(db, "alice"); db.add(p); db.commit()
    L.add_link(db, "alice", L.NODE_MEETING, "evt-1", L.REL_ATTENDED_BY, L.NODE_PERSON, p.id)
    db.commit()
    return p


def _token_req(owner, scopes):
    return SimpleNamespace(state=SimpleNamespace(current_user="api", api_token=True,
                                                 api_token_scopes=list(scopes), api_token_owner=owner))


def _endpoint(mod):
    router = mod.setup_meeting_notes_routes()
    for r in router.routes:
        if r.path == "/api/meeting-notes/meeting/{uid}" and "GET" in r.methods:
            return r.endpoint
    raise AssertionError("route not found")


def test_detail_includes_attendees(monkeypatch):
    SF = _sf(); db = SF(); p = _seed(db)
    monkeypatch.setattr(mnr, "SessionLocal", SF)
    get_detail = _endpoint(mnr)
    result = get_detail(_token_req("alice", ["notes:read"]), uid="evt-1")
    assert [a["email"] for a in result["attendees"]] == ["w@firm.nl"]
    assert result["attendees"][0]["source"] == "calendar"
    assert result["attendees"][0]["name"] == "Wiggert"


def test_detail_empty_attendees(monkeypatch):
    SF = _sf(); db = SF()
    cal = CalendarCal(id="cal1", owner="alice", name="Work"); db.add(cal)
    db.add(CalendarEvent(uid="evt-2", calendar_id="cal1", summary="Solo",
                         dtstart=datetime(2026, 7, 1, 9, 0), dtend=datetime(2026, 7, 1, 9, 30),
                         all_day=False, is_utc=True)); db.commit()
    monkeypatch.setattr(mnr, "SessionLocal", SF)
    get_detail = _endpoint(mnr)
    result = get_detail(_token_req("alice", ["notes:read"]), uid="evt-2")
    assert result["attendees"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_meeting_detail_attendees.py -v`
Expected: FAIL — `KeyError: 'attendees'`.

- [ ] **Step 3: Write minimal implementation**

In `routes/meeting_notes_routes.py`, add a module-level helper (near `_meeting_event_dict`):

```python
def _attendees_for_meeting(db, owner, uid) -> List[Dict[str, Any]]:
    """Live People linked to this meeting via attended_by, owner-scoped, name-sorted."""
    from core.hub_models import Person, Link
    from src import links as L
    rows = (db.query(Link)
              .filter(Link.owner == owner, Link.deleted_at.is_(None),
                      Link.from_type == L.NODE_MEETING, Link.from_id == uid,
                      Link.rel == L.REL_ATTENDED_BY)
              .all())
    pids = [r.to_id for r in rows]
    if not pids:
        return []
    people = (db.query(Person)
                .filter(Person.owner == owner, Person.deleted_at.is_(None),
                        Person.id.in_(pids))
                .all())
    people.sort(key=lambda p: (p.name or "").lower())
    return [{"id": p.id, "name": p.name, "email": p.email,
             "source": getattr(p, "source", "manual")} for p in people]
```

In the `get_meeting_detail` route, replace `return _meeting_event_dict(ev)` with:

```python
            d = _meeting_event_dict(ev)
            d["attendees"] = _attendees_for_meeting(db, owner, uid)
            return d
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_meeting_detail_attendees.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full new-test set + commit**

```bash
cd /Users/kganpat/Projects/odysseus-attendee-foundation
/Users/kganpat/Projects/odysseus/venv/bin/python -m pytest tests/test_attendee_schema.py tests/test_contacts_parse.py tests/test_contacts_resolve.py tests/test_contacts_link.py tests/test_caldav_attendee_hook.py tests/test_meeting_detail_attendees.py tests/test_meeting_detail_endpoint.py -q
git add routes/meeting_notes_routes.py tests/test_meeting_detail_attendees.py
git commit -m "$(cat <<'EOF'
feat(meeting-notes): include attendee roster in GET /meeting/{uid}

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PVkd2jDLGNwYBu7ZNMCJZt
EOF
)"
```

Expected: all listed test files pass (incl. the prior `test_meeting_detail_endpoint.py` still green — the response now has an extra `attendees` key, which those tests don't assert against).

---

## Self-Review

**Spec coverage:**
- `Person.source` migration + tier → Task 1. ✓
- `CalendarCal.link_attendees` migration → Task 1. ✓
- `ODYSSEUS_CALENDAR_ATTENDEE_LINKING` flag (default off) → Task 1. ✓
- `source` in Person sync dict → Task 1. ✓
- `normalize_email` / `parse_attendees` (CN, rooms, dedupe, organizer) → Task 2. ✓
- `owner_self_addresses` (EmailAccount + admin + env) → Task 3. ✓
- `resolve_or_create_person_by_email` (dedup, match-no-downgrade, create source=calendar+seq, owner-scope) → Task 3. ✓
- `link_event_attendees` (self-skip, idempotent, additive, per-attendee error isolation) → Task 4. ✓
- CalDAV sync hook (flag + per-calendar opt-out, self-addrs/cache once) → Task 5. ✓
- Endpoint `attendees` (owner-scoped, live, sorted, empty) → Task 6. ✓
- Self-skip uses owner addresses → Tasks 3+4. ✓
- Manual person not downgraded → Task 3 (asserted). ✓

**Placeholder scan:** none — every code step shows full code; every command has expected output.

**Type consistency:** `resolve_or_create_person_by_email(db, owner, email, name, *, cache) -> str (id)` identical in Tasks 3, 4. `link_event_attendees(db, owner, event_uid, vevent, *, self_addrs, cache) -> int` identical in Tasks 4, 5. `parse_attendees(vevent) -> list[(email,name)]` identical in Tasks 2, 4. `Person.source`, `CalendarCal.link_attendees`, `CALENDAR_ATTENDEE_LINKING` consistent across tasks. `add_link(db, owner, NODE_MEETING, uid, REL_ATTENDED_BY, NODE_PERSON, pid)` argument order matches `src/meeting_notes.py:122` and Task 6's reverse query (`from_type=NODE_MEETING, from_id=uid, rel=REL_ATTENDED_BY`). ✓
