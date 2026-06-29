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
