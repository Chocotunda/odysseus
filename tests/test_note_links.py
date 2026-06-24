"""Tests for src/note_links.py — wikilink resolution, stale-edge cleanup, backfill.

TDD: these tests were written BEFORE the implementation.  Run them first to
confirm RED, then implement src/note_links.py to turn them GREEN.
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.database import Note, SessionLocal, init_db
from core.hub_models import Person, Area, Link, run_hub_migrations


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _note(db, owner, content="", title="Test Note") -> Note:
    """Create and flush a Note (no commit yet)."""
    n = Note(id=_uid(), owner=owner, title=title, content=content,
             note_type="note", source="user")
    db.add(n)
    db.flush()
    return n


def _person(db, owner, name) -> Person:
    p = Person(id=_uid(), owner=owner, name=name)
    db.add(p)
    db.flush()
    return p


def _area(db, owner, name) -> Area:
    a = Area(id=_uid(), owner=owner, name=name)
    db.add(a)
    db.flush()
    return a


# ---------------------------------------------------------------------------
# basic fixture — fresh DB + vault dir per test
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path / "vault"))
    init_db()
    run_hub_migrations()
    session = SessionLocal()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Test 1: basic resolution creates Link rows + returns sorted list
# ---------------------------------------------------------------------------

def test_resolve_body_links_creates_links_and_returns_sorted(db):
    """A body with [[Person/<name>]] and [[Area/<name>]] creates two Note->entity
    Link rows and returns the sorted [[Type/Name]] list."""
    import src.note_links as note_links
    from src.links import NODE_NOTE, NODE_PERSON, NODE_AREA, REL_ABOUT, REL_IN_AREA

    owner = "alice-" + _uid()[:8]
    p = _person(db, owner, "Krishen")
    a = _area(db, owner, "Work")
    note = _note(db, owner, content="Met with [[Person/Krishen]] about [[Area/Work]] plans.")
    db.commit()

    result = note_links.resolve_body_links(db, note)

    assert result == ["[[Area/Work]]", "[[Person/Krishen]]"]

    # Link rows must exist: Note --about--> Person, Note --in_area--> Area
    person_links = (db.query(Link)
                    .filter(Link.owner == owner, Link.from_type == NODE_NOTE,
                            Link.from_id == note.id, Link.rel == REL_ABOUT,
                            Link.to_type == NODE_PERSON, Link.to_id == p.id,
                            Link.deleted_at.is_(None))
                    .all())
    assert len(person_links) == 1, "Expected one Note->Person 'about' Link"

    area_links = (db.query(Link)
                  .filter(Link.owner == owner, Link.from_type == NODE_NOTE,
                          Link.from_id == note.id, Link.rel == REL_IN_AREA,
                          Link.to_type == NODE_AREA, Link.to_id == a.id,
                          Link.deleted_at.is_(None))
                  .all())
    assert len(area_links) == 1, "Expected one Note->Area 'in_area' Link"


# ---------------------------------------------------------------------------
# Test 2: idempotent — re-resolving same body doesn't duplicate rows
# ---------------------------------------------------------------------------

def test_resolve_body_links_is_idempotent(db):
    """Calling resolve_body_links twice keeps exactly one live Link row per edge."""
    import src.note_links as note_links
    from src.links import NODE_NOTE, NODE_PERSON, REL_ABOUT

    owner = "bob-" + _uid()[:8]
    _person(db, owner, "Alice")
    note = _note(db, owner, content="Spoke with [[Person/Alice]] today.")
    db.commit()

    note_links.resolve_body_links(db, note)
    note_links.resolve_body_links(db, note)

    rows = (db.query(Link)
            .filter(Link.owner == owner, Link.from_type == NODE_NOTE,
                    Link.from_id == note.id, Link.rel == REL_ABOUT,
                    Link.deleted_at.is_(None))
            .all())
    assert len(rows) == 1, "Expected exactly one live about-Link after two resolves"


# ---------------------------------------------------------------------------
# Test 3: stale links are soft-deleted when the wikilink is removed from body
# ---------------------------------------------------------------------------

def test_resolve_removes_stale_links(db):
    """Re-resolving a body that dropped [[Area/Work]] soft-deletes that Link row."""
    import src.note_links as note_links
    from src.links import NODE_NOTE, NODE_AREA, REL_IN_AREA

    owner = "carol-" + _uid()[:8]
    a = _area(db, owner, "Work")
    note = _note(db, owner, content="Worked on [[Area/Work]] stuff.")
    db.commit()

    # First pass — creates the link
    note_links.resolve_body_links(db, note)

    live_before = (db.query(Link)
                   .filter(Link.owner == owner, Link.from_type == NODE_NOTE,
                           Link.from_id == note.id, Link.rel == REL_IN_AREA,
                           Link.to_id == a.id, Link.deleted_at.is_(None))
                   .count())
    assert live_before == 1

    # Drop the wikilink from body, re-resolve
    note.content = "Just some random text with no wikilinks."
    db.flush()
    result = note_links.resolve_body_links(db, note)

    assert result == []

    # The old Link should now be tombstoned
    live_after = (db.query(Link)
                  .filter(Link.owner == owner, Link.from_type == NODE_NOTE,
                          Link.from_id == note.id, Link.rel == REL_IN_AREA,
                          Link.to_id == a.id, Link.deleted_at.is_(None))
                  .count())
    assert live_after == 0, "Expected stale Link to be soft-deleted"

    # Physical row still exists (soft-delete, not hard)
    tombstone = (db.query(Link)
                 .filter(Link.owner == owner, Link.from_type == NODE_NOTE,
                         Link.from_id == note.id, Link.rel == REL_IN_AREA,
                         Link.to_id == a.id, Link.deleted_at.isnot(None))
                 .count())
    assert tombstone == 1, "Expected tombstone row to remain"


# ---------------------------------------------------------------------------
# Test 4: unresolvable [[Person/Nobody]] is skipped, no crash
# ---------------------------------------------------------------------------

def test_resolve_skips_unresolvable_links(db):
    """An unknown [[Person/Nobody]] in the body is silently skipped."""
    import src.note_links as note_links

    owner = "dave-" + _uid()[:8]
    note = _note(db, owner, content="Saw [[Person/Nobody]] today.")
    db.commit()

    result = note_links.resolve_body_links(db, note)
    assert result == [], "Unresolvable wikilink should be skipped"

    # No Link rows created
    rows = (db.query(Link)
            .filter(Link.owner == owner, Link.from_id == note.id,
                    Link.deleted_at.is_(None))
            .all())
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Test 5: owner isolation — same name under a different owner is not resolved
# ---------------------------------------------------------------------------

def test_resolve_does_not_cross_owners(db):
    """A Person named 'Shared' owned by owner2 is NOT resolved for owner1."""
    import src.note_links as note_links

    owner1 = "o1-" + _uid()[:8]
    owner2 = "o2-" + _uid()[:8]
    _person(db, owner2, "Shared")   # exists, but wrong owner
    note = _note(db, owner1, content="Spoke with [[Person/Shared]].")
    db.commit()

    result = note_links.resolve_body_links(db, note)
    assert result == [], "Cross-owner resolution must be rejected"


# ---------------------------------------------------------------------------
# Test 6: Note wikilinks ([[Note/Other]]) create from_note edges
# ---------------------------------------------------------------------------

def test_resolve_note_wikilink_creates_from_note_edge(db):
    """[[Note/Other Title]] in body creates Note --from_note--> Note edge."""
    import src.note_links as note_links
    from src.links import NODE_NOTE, REL_FROM_NOTE

    owner = "erin-" + _uid()[:8]
    target = _note(db, owner, content="", title="Other Title")
    note = _note(db, owner, content="See also [[Note/Other Title]] for context.")
    db.commit()

    result = note_links.resolve_body_links(db, note)

    assert "[[Note/Other Title]]" in result

    rows = (db.query(Link)
            .filter(Link.owner == owner, Link.from_type == NODE_NOTE,
                    Link.from_id == note.id, Link.rel == REL_FROM_NOTE,
                    Link.to_type == NODE_NOTE, Link.to_id == target.id,
                    Link.deleted_at.is_(None))
            .all())
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Test 7: backfill writes a .md for an existing note missing one
# ---------------------------------------------------------------------------

def test_backfill_vault_writes_missing_files(tmp_path, monkeypatch):
    """backfill_vault_for_owner writes .md files for notes that have none."""
    import src.note_vault as note_vault
    import src.note_links as note_links

    vault_dir = tmp_path / "vault"
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(vault_dir))
    init_db()
    run_hub_migrations()

    db = SessionLocal()
    try:
        owner = "ff-" + _uid()[:8]
        n = Note(id=_uid(), owner=owner, title="Backfill Me", content="No wikilinks here.")
        db.add(n)
        db.flush()
        from core import hub_models
        n.seq = hub_models.next_note_seq(db, owner)
        db.commit()

        # Ensure vault file does NOT exist before backfill
        expected = note_vault.vault_path_for(n)
        assert not expected.exists(), "File should not exist before backfill"

        # Run backfill
        count = note_links.backfill_vault_for_owner(db, owner)

        assert count == 1, f"Expected 1 backfilled note, got {count}"
        assert expected.exists(), "Vault file should exist after backfill"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Test 8: regression — metadata-only persist_note must NOT wipe frontmatter links
# ---------------------------------------------------------------------------

def test_persist_note_metadata_edit_preserves_frontmatter_links(tmp_path, monkeypatch):
    """A metadata-only call to persist_note (no links arg) must NOT wipe the
    vault frontmatter ``links:`` block that was written during note creation.

    Regression for the bug where writers such as pin/archive/toggle-item passed
    ``links=[]`` explicitly, wiping the frontmatter links even though the body
    still contained wikilinks and the DB Link rows were intact.

    This test would FAIL if those writers still passed ``links=[]``.
    """
    import src.note_vault as note_vault
    import src.notes_service as notes_service
    from core.hub_models import run_hub_migrations

    vault_dir = tmp_path / "vault"
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(vault_dir))
    init_db()
    run_hub_migrations()

    db = SessionLocal()
    try:
        owner = "reg-" + _uid()[:8]

        # Seed an Area so the wikilink resolves
        area = _area(db, owner, "MyProject")
        db.commit()

        # Create the note with a wikilink in the body
        note = Note(
            id=_uid(),
            owner=owner,
            title="Regression Note",
            content="Working on [[Area/MyProject]] this week.",
            note_type="note",
            source="user",
        )
        db.add(note)
        # persist_note with default links=None: resolves body links and writes vault
        notes_service.persist_note(db, note)
        db.commit()

        # Confirm the vault file was written and contains the link
        vault_path = note_vault.vault_path_for(note)
        assert vault_path.exists(), "Vault file should exist after initial persist"
        initial_text = vault_path.read_text()
        assert "[[Area/MyProject]]" in initial_text, (
            f"Initial vault should contain wikilink; got:\n{initial_text}"
        )

        # Simulate a metadata-only edit (pin) without passing links arg
        note.pinned = True
        notes_service.persist_note(db, note)  # must NOT pass links=[]
        db.commit()

        # The vault frontmatter links must NOT be wiped
        after_text = vault_path.read_text()
        assert "[[Area/MyProject]]" in after_text, (
            "Metadata-only persist_note wiped frontmatter links — regression!\n"
            f"Vault after pinned=True:\n{after_text}"
        )
    finally:
        db.close()
