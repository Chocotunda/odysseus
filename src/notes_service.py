"""The single chokepoint every Note writer routes through: assigns the
per-owner `seq` and projects the note to the .md vault. Centralising this is
the structural guarantee of the ALL-WRITERS-SET-SEQ + vault invariants."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from core import hub_models
import src.note_vault as note_vault


def persist_note(db, note, *, links: Optional[List[str]] = None):
    """Assign seq + write the vault file. Caller is responsible for db.commit().

    ``links`` controls wikilink resolution:
      - None (default): auto-resolve [[Type/Name]] from the note body via
        ``src.note_links.resolve_body_links`` and write the resolved list to
        the vault frontmatter.
      - []: skip resolution entirely (for tests or callers that have already
        handled link wiring externally).
      - [...]: use the supplied list verbatim (frontmatter only, no DB upserts).
    """
    note.seq = hub_models.next_note_seq(db, note.owner)
    db.flush()  # ensure updated_at/created_at populated for the vault frontmatter

    if links is None:
        try:
            import src.note_links as note_links
            links = note_links.resolve_body_links(db, note)
        except Exception:
            links = []  # resolution failure must never break the API write

    try:
        note_vault.write_note(note, links=links)
    except Exception:
        pass  # vault projection must never break the API write
    return note


def delete_note(db, note) -> None:
    """Soft-delete (tombstone + seq bump) and remove the vault file."""
    note.deleted_at = datetime.now(timezone.utc)
    note.seq = hub_models.next_note_seq(db, note.owner)
    db.flush()
    try:
        note_vault.remove_note(note)
    except Exception:
        pass
