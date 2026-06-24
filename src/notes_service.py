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
    `links` is the note's outbound ["[[Type/Name]]"] list (Task 8); [] until then."""
    note.seq = hub_models.next_note_seq(db, note.owner)
    db.flush()  # ensure updated_at/created_at populated for the vault frontmatter
    try:
        note_vault.write_note(note, links=links or [])
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
