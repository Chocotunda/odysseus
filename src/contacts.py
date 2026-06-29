"""Calendar-attendee → Person resolution (Slice 1 of the relationship-memory layer).

Pure parsing here; DB-touching resolution/linking lives in the same module but is
split into separate functions so the parse layer is unit-testable without a DB.
"""
from __future__ import annotations

import os
import uuid as _uuid
from typing import List, Optional, Tuple

from sqlalchemy import func


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
