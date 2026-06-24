import os
from types import SimpleNamespace
from datetime import datetime, timezone

import src.note_vault as note_vault


def _note(**kw):
    base = dict(
        id="9f3c2a1b-0000-4000-8000-000000000001",
        owner="alice",
        title="Q3 Planning",
        content="Body line one.\n\n- [ ] follow up\n- [x] done",
        items=None,
        note_type="note",
        color="blue",
        label="work",
        pinned=True,
        archived=False,
        created_at=datetime(2026, 6, 24, 18, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 24, 18, 30, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_slugify_basic():
    assert note_vault.slugify("Q3 Planning!") == "q3-planning"
    assert note_vault.slugify("   ") == "untitled"


def test_filename_is_slug_plus_short_id(tmp_path, monkeypatch):
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    p = note_vault.vault_path_for(_note())
    assert p.name == "q3-planning-9f3c.md"


def test_write_note_emits_frontmatter_and_body(tmp_path, monkeypatch):
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    p = note_vault.write_note(_note(), links=["[[Person/Krishen Ganpat]]", "[[Area/Work]]"])
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "id: 9f3c2a1b-0000-4000-8000-000000000001" in text
    assert "title: Q3 Planning" in text
    assert "pinned: true" in text
    assert "tags:\n- work" in text or "tags: [work]" in text
    assert "[[Person/Krishen Ganpat]]" in text
    assert "Body line one." in text
    assert "- [x] done" in text


def test_write_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    n = _note()
    p1 = note_vault.write_note(n, links=["[[Area/Work]]"])
    first = p1.read_bytes()
    p2 = note_vault.write_note(n, links=["[[Area/Work]]"])
    assert p2.read_bytes() == first  # byte-identical re-write


def test_remove_note_deletes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    n = _note()
    p = note_vault.write_note(n, links=[])
    assert p.exists()
    note_vault.remove_note(n)
    assert not p.exists()


def test_rename_on_title_change_removes_old(tmp_path, monkeypatch):
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    n = _note(title="Old Title")
    old = note_vault.write_note(n, links=[])
    n.title = "New Title"
    new = note_vault.write_note(n, links=[])
    assert new.exists()
    assert not old.exists()  # old slug file removed
