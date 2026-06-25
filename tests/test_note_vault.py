import os
import yaml
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


def _extract_frontmatter(text: str) -> dict:
    """Parse the YAML frontmatter block from a rendered .md file."""
    # Strip the opening '---\n', find the closing '---\n' and parse between.
    assert text.startswith("---\n"), "Expected frontmatter to start with ---"
    rest = text[4:]  # skip opening '---\n'
    end = rest.index("---\n")
    fm_text = rest[:end]
    return yaml.safe_load(fm_text)


def test_yaml_safe_title_with_injection(tmp_path, monkeypatch):
    """A title containing a newline + YAML must not inject extra top-level keys."""
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    evil_title = "Legit Title\n---\nmalicious: true"
    p = note_vault.write_note(_note(title=evil_title), links=[])
    text = p.read_text(encoding="utf-8")
    fm = _extract_frontmatter(text)
    # The whole evil string must be a single 'title' value — NOT a top-level key.
    assert "malicious" not in fm, (
        f"YAML injection succeeded: 'malicious' appeared as a top-level key. fm={fm!r}"
    )
    assert isinstance(fm.get("title"), str), "title must be a plain string"
    assert "\n---\nmalicious: true" in fm["title"], (
        "The newline-containing title should be preserved inside the quoted scalar"
    )


def test_empty_label_emits_empty_tags_list(tmp_path, monkeypatch):
    """A note with no label must emit 'tags: []' (not null) in frontmatter."""
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    p = note_vault.write_note(_note(label=None), links=[])
    text = p.read_text(encoding="utf-8")
    # Verify the raw text contains the empty flow-sequence form.
    assert "tags: []" in text, f"Expected 'tags: []' in frontmatter, got:\n{text}"
    fm = _extract_frontmatter(text)
    # yaml.safe_load must produce an empty list, not None.
    assert fm.get("tags") == [], (
        f"Expected tags==[], got {fm.get('tags')!r}. Full fm: {fm!r}"
    )
