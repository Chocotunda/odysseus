"""Wikilink resolution — parse [[Type/Name]] from note bodies and reconcile
them with the hub Link graph.

Every note write that goes through ``notes_service.persist_note`` calls
``resolve_body_links`` (when links=None) so the Link table always mirrors
whatever wikilinks are currently in the note content.

Rel mapping (mirrors the conventions established in src/links.py):
  [[Person/Name]]  -> Note --about-->     Person
  [[Area/Name]]    -> Note --in_area-->   Area
  [[Note/Title]]   -> Note --from_note--> Note
  [[Meeting/uid]]  -> Note --note_of-->   Meeting

Owner-scoping is a security boundary: names are resolved only within the
note's owner scope; a typo or a cross-owner name resolves to nothing (no
Link created, no error raised).
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from core.database import utcnow_naive
from core.hub_models import Link, Person, Area, next_link_seq
from src.links import (
    NODE_NOTE, NODE_PERSON, NODE_AREA, NODE_MEETING,
    REL_ABOUT, REL_IN_AREA, REL_FROM_NOTE, REL_NOTE_OF,
    add_link, links_from,
)

logger = logging.getLogger(__name__)

# Supported wikilink types and their hub rel mappings
_WIKILINK_RE = re.compile(
    r"\[\[(?P<wtype>Person|Area|Note|Meeting)/(?P<name>[^\]]+)\]\]"
)

_TYPE_TO_NODE = {
    "Person": NODE_PERSON,
    "Area": NODE_AREA,
    "Note": NODE_NOTE,
    "Meeting": NODE_MEETING,
}

_TYPE_TO_REL = {
    "Person": REL_ABOUT,
    "Area": REL_IN_AREA,
    "Note": REL_FROM_NOTE,
    "Meeting": REL_NOTE_OF,
}


# ---------------------------------------------------------------------------
# Resolution helpers (owner-scoped, by name)
# ---------------------------------------------------------------------------

def _resolve_person(db, owner: Optional[str], name: str) -> Optional[str]:
    """Return the Person.id for ``name`` under ``owner``, or None."""
    row = (db.query(Person)
           .filter(Person.owner == owner, Person.name == name,
                   Person.deleted_at.is_(None))
           .first())
    return row.id if row else None


def _resolve_area(db, owner: Optional[str], name: str) -> Optional[str]:
    """Return the Area.id for ``name`` under ``owner``, or None."""
    row = (db.query(Area)
           .filter(Area.owner == owner, Area.name == name,
                   Area.deleted_at.is_(None))
           .first())
    return row.id if row else None


def _resolve_note_by_title(db, owner: Optional[str], title: str) -> Optional[str]:
    """Return a Note.id by title under ``owner``, or None."""
    from core.database import Note
    row = (db.query(Note)
           .filter(Note.owner == owner, Note.title == title,
                   Note.deleted_at.is_(None))
           .first())
    return row.id if row else None


def _resolve_meeting(db, owner: Optional[str], uid: str) -> Optional[str]:
    """Return the meeting uid if a CalendarEvent with that uid exists, or None.

    For Slice A only Person/Area/Note are picker-inserted; Meeting is kept for
    completeness but resolves by uid, not name (the uid is the natural key in
    CalendarEvent).  We accept the uid as-is — if a CalendarEvent table has it
    we confirm it; otherwise we skip.  Either way we return uid-or-None.
    """
    try:
        from core.database import CalendarEvent
        row = (db.query(CalendarEvent)
               .filter(CalendarEvent.uid == uid, CalendarEvent.owner == owner)
               .first())
        return row.uid if row else None
    except Exception:
        return None


_RESOLVER = {
    "Person": _resolve_person,
    "Area": _resolve_area,
    "Note": _resolve_note_by_title,
    "Meeting": _resolve_meeting,
}


# ---------------------------------------------------------------------------
# Stale-edge removal (note-origin links no longer present in the body)
# ---------------------------------------------------------------------------

def _remove_stale_note_links(db, note, keep_edges: set) -> None:
    """Soft-delete every live Note-origin Link whose (rel, to_type, to_id) is no
    longer in ``keep_edges`` (set of (rel, to_type, to_id) tuples).

    Mirrors the soft-delete pattern from ``src/links.remove_links_for``:
    stamp ``deleted_at`` + advance ``seq`` per row so the change feeds pick it up.
    """
    live_from_note = (db.query(Link)
                      .filter(Link.owner == note.owner,
                              Link.from_type == NODE_NOTE,
                              Link.from_id == note.id,
                              Link.deleted_at.is_(None))
                      .all())
    now = utcnow_naive()
    tombstoned = 0
    for row in live_from_note:
        edge_key = (row.rel, row.to_type, row.to_id)
        if edge_key not in keep_edges:
            row.deleted_at = now
            row.seq = next_link_seq(db, note.owner)
            tombstoned += 1
    if tombstoned:
        db.commit()
        logger.debug("note_links: tombstoned %d stale edges for note %s", tombstoned, note.id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_body_links(db, note) -> List[str]:
    """Parse ``[[Type/Name]]`` wikilinks from ``note.content``, upsert the
    corresponding hub ``Link`` rows, soft-delete stale edges that are no longer
    present, and return the **sorted** list of resolved ``[[Type/Name]]`` strings
    (for use as the frontmatter ``links:`` list).

    - Unresolvable names are silently skipped (defensive against typos).
    - All resolution is owner-scoped; cross-owner names are never resolved.
    - Never raises — failures are logged and return an empty list.
    """
    try:
        return _resolve(db, note)
    except Exception as exc:
        logger.warning("note_links.resolve_body_links failed for note %s: %s", note.id, exc)
        return []


def _resolve(db, note) -> List[str]:
    content = getattr(note, "content", None) or ""
    owner = note.owner

    # 1. Parse all wikilinks from body
    found: list[tuple[str, str, str]] = []  # (wtype, name, raw_token)
    for m in _WIKILINK_RE.finditer(content):
        wtype = m.group("wtype")
        name = m.group("name").strip()
        found.append((wtype, name, m.group(0)))

    # 2. Resolve each to an entity id (owner-scoped)
    keep_edges: set[tuple[str, str, str]] = set()  # (rel, to_type, to_id)
    resolved_tokens: list[str] = []

    for wtype, name, raw in found:
        resolver = _RESOLVER.get(wtype)
        if resolver is None:
            continue
        entity_id = resolver(db, owner, name)
        if not entity_id:
            logger.debug("note_links: unresolvable %s", raw)
            continue
        rel = _TYPE_TO_REL[wtype]
        to_type = _TYPE_TO_NODE[wtype]
        keep_edges.add((rel, to_type, entity_id))
        resolved_tokens.append(raw)

    # 3. Upsert Link rows for all resolved edges (add_link is idempotent)
    for (rel, to_type, entity_id) in keep_edges:
        add_link(db, owner, NODE_NOTE, note.id, rel, to_type, entity_id)

    # 4. Soft-delete stale edges (those no longer in keep_edges)
    _remove_stale_note_links(db, note, keep_edges)

    # 5. Return sorted list for frontmatter
    return sorted(set(resolved_tokens))


# ---------------------------------------------------------------------------
# Meeting-note stale-edge cleanup helper (reused by src/meeting_notes.py)
# ---------------------------------------------------------------------------

def remove_stale_meeting_note_links(db, note) -> None:
    """Remove stale hub edges for a meeting-note being re-saved.

    Meeting notes carry edges created by ``save_meeting_note`` (note_of, about,
    attended_by, in_area).  On re-save the caller rebuilds them, so any old edges
    from a previous save must be tombstoned first or changing the linked
    person/meeting will leave orphaned edges.

    Only removes edges where the NOTE is the ``from`` side (note_of, about,
    in_area) or the MEETING is the ``from`` side with attended_by pointing at
    the same person as before — specifically, we remove all live edges FROM
    this note so the caller can re-add the correct ones.

    Does NOT remove ``from_note`` edges on PlanItems (those belong to the tasks,
    not the note).
    """
    from src.links import NODE_NOTE, NODE_MEETING, REL_ATTENDED_BY
    try:
        live = (db.query(Link)
                .filter(Link.owner == note.owner,
                        Link.from_type == NODE_NOTE,
                        Link.from_id == note.id,
                        Link.deleted_at.is_(None))
                .all())
        now = utcnow_naive()
        for row in live:
            row.deleted_at = now
            row.seq = next_link_seq(db, note.owner)
        db.commit()
    except Exception as exc:
        logger.warning("note_links.remove_stale_meeting_note_links failed for note %s: %s",
                       note.id, exc)


# ---------------------------------------------------------------------------
# Vault backfill — called once at startup from run_hub_migrations path
# ---------------------------------------------------------------------------

def backfill_vault_for_owner(db, owner: Optional[str]) -> int:
    """Write vault .md files for any live note under ``owner`` that lacks one.

    Idempotent: skips notes that already have a vault file. Returns the count
    of notes backfilled. Never raises — errors are logged and skipped.
    """
    import src.note_vault as note_vault
    from core.database import Note

    notes = (db.query(Note)
             .filter(Note.owner == owner, Note.deleted_at.is_(None))
             .all())
    count = 0
    for n in notes:
        try:
            if not note_vault.vault_path_for(n).exists():
                links = resolve_body_links(db, n)
                note_vault.write_note(n, links=links)
                count += 1
        except Exception as exc:
            logger.warning("note_links.backfill_vault_for_owner: skip note %s: %s", n.id, exc)
    if count:
        logger.info("note_links.backfill_vault_for_owner: wrote %d vault files for owner=%s",
                    count, owner)
    return count
