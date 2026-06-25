"""Single-writer projection of Note rows to an Obsidian-compatible .md vault.

The DB stays canonical; this module writes the body as canonical Markdown and
all other fields as deterministic YAML frontmatter. Filenames are
`<title-slug>-<short id>.md`. One-way (DB -> files); reading external edits
back is deliberately out of scope (multi-writer risk).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Optional

from src.constants import VAULT_DIR


def _ensure_dir() -> Optional[Path]:
    try:
        Path(VAULT_DIR).mkdir(parents=True, exist_ok=True)
        return Path(VAULT_DIR)
    except OSError:
        return None  # degrade gracefully; never crash a note write


def slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text or "untitled"


def _short_id(note) -> str:
    return str(note.id).replace("-", "")[:4]


def _filename(note) -> str:
    return f"{slugify(getattr(note, 'title', ''))}-{_short_id(note)}.md"


def vault_path_for(note) -> Path:
    return Path(VAULT_DIR) / _filename(note)


def _yaml_scalar(value: str) -> str:
    """Return *value* as a plain YAML scalar when it's safe, otherwise as a
    double-quoted, escaped string.

    "Plain-safe" means:
    - no newline
    - no leading or trailing whitespace
    - does not start with a YAML indicator character
      (: - # [ ] { } " ' > | @ & * ! % ?)
    - does not contain the substring ': ' (key-colon-space)
    - does not contain ' #' (inline comment marker)

    Simple titles like 'Q3 Planning' remain unquoted.
    """
    _INDICATOR_STARTS = frozenset(":- #[]{}\"'>|@&*!%?")
    if (
        "\n" in value
        or value != value.strip()
        or (value and value[0] in _INDICATOR_STARTS)
        or ": " in value
        or " #" in value
    ):
        # Double-quoted form: escape backslash, double-quote, newline.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return value


def _iso(dt) -> str:
    if dt is None:
        return ""
    try:
        return dt.replace(microsecond=0).isoformat()
    except Exception:
        return str(dt)


def _checklist_to_md(items_json: Optional[str]) -> str:
    if not items_json:
        return ""
    try:
        items = json.loads(items_json)
    except (ValueError, TypeError):
        return ""
    lines = []
    for it in items or []:
        mark = "x" if it.get("done") else " "
        lines.append(f"- [{mark}] {it.get('text', '')}")
    return "\n".join(lines)


def _frontmatter(note, links: List[str]) -> str:
    # Deterministic key order; deterministic list order (sorted links).
    fm: list[str] = ["---"]
    fm.append(f"id: {note.id}")
    fm.append(f"title: {_yaml_scalar(getattr(note, 'title', '') or '')}")
    fm.append(f"created: {_iso(getattr(note, 'created_at', None))}")
    fm.append(f"updated: {_iso(getattr(note, 'updated_at', None))}")
    tag = getattr(note, "label", None)
    if tag:
        fm.append("tags:")
        fm.append(f"- {_yaml_scalar(tag)}")
    else:
        fm.append("tags: []")
    fm.append(f"color: {_yaml_scalar(getattr(note, 'color', None) or '')}")
    fm.append(f"pinned: {'true' if getattr(note, 'pinned', False) else 'false'}")
    fm.append(f"archived: {'true' if getattr(note, 'archived', False) else 'false'}")
    fm.append(f"note_type: {_yaml_scalar(getattr(note, 'note_type', 'note') or 'note')}")
    sorted_links = sorted(links or [])
    if sorted_links:
        fm.append("links:")
        for link in sorted_links:
            fm.append(f'- "{link}"')
    else:
        fm.append("links: []")
    fm.append("---")
    return "\n".join(fm)


def fold_checklist_into_body(content: Optional[str], items_json: Optional[str]) -> str:
    """Merge structured checklist `items` into the markdown `content` body.

    This is the single source of truth for how `items` (the JSON checklist
    column) get rendered as canonical `- [ ] ` / `- [x] ` markdown task lines
    and appended to the body. Used by BOTH the .md vault writer (`_body`) and
    the Tide sync serializer (`_note_to_dict(fold_items=True)`), so the file on
    disk and the synced body are byte-identical. The `checklist not in body`
    guard keeps re-folding idempotent (a body that already contains the task
    lines is not appended to a second time).
    """
    body = content or ""
    checklist = _checklist_to_md(items_json)
    if checklist and checklist not in body:
        body = (body + "\n\n" + checklist).strip() if body.strip() else checklist
    return body


def _body(note) -> str:
    return fold_checklist_into_body(
        getattr(note, "content", None), getattr(note, "items", None)
    )


def render(note, links: List[str]) -> str:
    return _frontmatter(note, links) + "\n\n" + _body(note) + "\n"


def write_note(note, links: Optional[List[str]] = None) -> Optional[Path]:
    base = _ensure_dir()
    if base is None:
        return None
    target = vault_path_for(note)
    # Rename-on-retitle: remove any stale file for this id with a different slug.
    short = _short_id(note)
    for existing in base.glob(f"*-{short}.md"):
        if existing.name != target.name:
            try:
                existing.unlink()
            except OSError:
                pass
    target.write_text(render(note, links or []), encoding="utf-8")
    return target


def remove_note(note) -> None:
    base = _ensure_dir()
    if base is None:
        return
    short = _short_id(note)
    for existing in base.glob(f"*-{short}.md"):
        try:
            existing.unlink()
        except OSError:
            pass
