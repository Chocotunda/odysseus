# Tide Notes + `.md` Vault (Slice A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sync the existing Keep-style `Note` to Tide and project every note to a portable Obsidian-compatible `.md` vault, with a single-pane markdown notes editor in Tide.

**Architecture:** Markdown body is the on-disk source of truth; Odysseus is the sole writer (one-way DB→files). `Note` gets the proven `PlanItem` delta contract (per-owner `seq` + `deleted_at` tombstones + `/changes` feed). Tide adds a `Note` entity to its generic multi-entity sync engine — no coordinator rework. The editor is a single live-preview markdown pane (slash menu, `@`/`[[` linking, checklist task-lines); the Craft-grade per-block editor is Slice B.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / SQLite (server); Swift 6 / SwiftUI / GRDB via Point-Free SQLiteData (Tide).

**Spec:** `docs/superpowers/specs/2026-06-24-tide-notes-vault-design.md`. **Research:** `docs/ai-context/2026-06-24-craft-notes-research.md`.

## Global Constraints

- **Paths via constants only** — add `VAULT_DIR` to `src/constants.py`; never build writable paths from `Path(__file__)` or literals. Guard dir creation (degrade gracefully if unwritable).
- **Owner scoping is a security boundary** — every note query stays owner-scoped; the changes feed + scope gate must not leak across owners.
- **ALL-WRITERS-SET-SEQ** — every `Note` write assigns `seq` via `core.hub_models.next_note_seq` AND projects to the vault, routed through the single `persist_note` chokepoint (Task 3). No writer may bypass it.
- **Additive, forward-only migrations** — guarded ALTERs registered in `run_hub_migrations()` (create_all won't ALTER an existing table); Tide GRDB migrations are additive (next is **v9**).
- **Single-writer / one-way vault** — Tide never writes vault files; it edits via REST. No reading external `.md` edits back in Slice A.
- **Backend has NO auto-reload** — restart uvicorn after server changes before any live smoke.
- **Tide `Sources/TideCore/` files auto-discover** (no xcodegen); files added under the app target `Tide/` require `xcodegen generate` + `xcodebuild` to prove app-target compilation.
- **Commits:** Conventional Commits; server scope `notes`/`vault`, Tide scope `sync`/`note`/`ui`.

---

# PART 1 — Odysseus server (the vault + the sync contract)

Land Part 1 first; Tide (Part 2) consumes the feed it produces.

---

### Task 1: `VAULT_DIR` constant + `note_vault` serializer

Pure markdown/frontmatter serialization to a vault directory. No route logic.

**Files:**
- Modify: `src/constants.py` (add `VAULT_DIR`)
- Create: `src/note_vault.py`
- Test: `tests/test_note_vault.py`

**Interfaces:**
- Produces: `note_vault.vault_path_for(note) -> Path`, `note_vault.write_note(note, links: list[str]) -> Path`, `note_vault.remove_note(note) -> None`, `note_vault.slugify(text) -> str`. `note` is a `core.database.Note`; `links` is a list of `"[[Type/Name]]"` strings.

- [ ] **Step 1: Add the constant**

In `src/constants.py`, next to the other `DATA_DIR`-derived dirs (e.g. `CHROMA_DIR`), add:

```python
VAULT_DIR = os.path.join(DATA_DIR, "vault")
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_note_vault.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_note_vault.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.note_vault'`

- [ ] **Step 4: Implement `src/note_vault.py`**

```python
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
    fm.append(f"title: {getattr(note, 'title', '') or ''}")
    fm.append(f"created: {_iso(getattr(note, 'created_at', None))}")
    fm.append(f"updated: {_iso(getattr(note, 'updated_at', None))}")
    tag = getattr(note, "label", None)
    fm.append("tags:")
    if tag:
        fm.append(f"- {tag}")
    fm.append(f"color: {getattr(note, 'color', None) or ''}")
    fm.append(f"pinned: {'true' if getattr(note, 'pinned', False) else 'false'}")
    fm.append(f"archived: {'true' if getattr(note, 'archived', False) else 'false'}")
    fm.append(f"note_type: {getattr(note, 'note_type', 'note') or 'note'}")
    fm.append("links:")
    for link in sorted(links or []):
        fm.append(f'- "{link}"')
    fm.append("---")
    return "\n".join(fm)


def _body(note) -> str:
    body = getattr(note, "content", None) or ""
    checklist = _checklist_to_md(getattr(note, "items", None))
    if checklist and checklist not in body:
        body = (body + "\n\n" + checklist).strip() if body else checklist
    return body


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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_note_vault.py -q`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add src/constants.py src/note_vault.py tests/test_note_vault.py
git commit -m "feat(vault): add VAULT_DIR + note_vault .md serializer"
```

---

### Task 2: `Note.seq` + `Note.deleted_at` columns, migrations, backfill, `next_note_seq`

Mirror the `PlanItem` 1b-0 contract on `Note`.

**Files:**
- Modify: `core/database.py` (`Note` model, ~line 1611)
- Modify: `core/hub_models.py` (add `next_note_seq`, two `_migrate_*` funcs, register in `run_hub_migrations`)
- Test: `tests/test_note_sync_contract.py`

**Interfaces:**
- Consumes: pattern of `next_plan_item_seq(db, owner)` (core/hub_models.py:244-250), `_migrate_add_plan_item_seq_column` (210-241), `_migrate_add_plan_item_deleted_at_column` (156-178), `run_hub_migrations` (461-472).
- Produces: `core.hub_models.next_note_seq(db, owner) -> int`; `Note.seq: int`, `Note.deleted_at: datetime|None`.

- [ ] **Step 1: Add columns to the `Note` model**

In `core/database.py`, inside `class Note`, add after `agent_session_id`:

```python
    seq        = Column(Integer, default=0, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_note_sync_contract.py`:

```python
from datetime import datetime, timezone

from core.database import SessionLocal, Note, init_db
from core import hub_models


def _new_note(db, owner, title):
    n = Note(id=f"n-{title}", owner=owner, title=title, content="x")
    n.seq = hub_models.next_note_seq(db, owner)
    db.add(n)
    db.commit()
    return n


def test_next_note_seq_is_monotonic_per_owner():
    init_db()
    db = SessionLocal()
    try:
        a1 = _new_note(db, "owner-seq-a", "a1").seq
        a2 = _new_note(db, "owner-seq-a", "a2").seq
        b1 = _new_note(db, "owner-seq-b", "b1").seq
        assert a2 > a1
        assert b1 >= 1            # per-owner stream starts independently
    finally:
        db.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_note_sync_contract.py -q`
Expected: FAIL with `AttributeError: module 'core.hub_models' has no attribute 'next_note_seq'`

- [ ] **Step 4: Implement allocator + migrations in `core/hub_models.py`**

Add the allocator (mirror `next_plan_item_seq`):

```python
def next_note_seq(db, owner):
    """Per-owner monotonic seq for Note (mirrors next_plan_item_seq)."""
    from core.database import Note
    current = (
        db.query(func.max(Note.seq))
        .filter(Note.owner == owner)
        .scalar()
    )
    return (current or 0) + 1
```

Add two guarded migrations (mirror the PlanItem ones, table `notes`, columns `seq`/`deleted_at`):

```python
def _migrate_add_note_seq_column(engine):
    """Add notes.seq (guarded) + per-owner 1..N backfill ordered by created_at."""
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("notes")]
    if "seq" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE notes ADD COLUMN seq INTEGER DEFAULT 0"))
        owners = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT owner FROM notes"))]
        for owner in owners:
            rows = conn.execute(text(
                "SELECT id FROM notes WHERE owner IS :o OR owner = :o "
                "ORDER BY created_at ASC, id ASC"), {"o": owner}).fetchall()
            for i, (nid,) in enumerate(rows, start=1):
                conn.execute(text("UPDATE notes SET seq = :s WHERE id = :id"),
                             {"s": i, "id": nid})


def _migrate_add_note_deleted_at_column(engine):
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("notes")]
    if "deleted_at" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE notes ADD COLUMN deleted_at DATETIME"))
```

Register both in `run_hub_migrations(engine)` (match the existing call style there):

```python
    _migrate_add_note_seq_column(engine)
    _migrate_add_note_deleted_at_column(engine)
```

> Use the exact `inspect`/`text`/`func` imports already present at the top of `core/hub_models.py`. If the PlanItem migrations there use a different backfill idiom (e.g. via the ORM session), copy that idiom verbatim rather than the raw-SQL sketch above.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_note_sync_contract.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/database.py core/hub_models.py tests/test_note_sync_contract.py
git commit -m "feat(notes): add per-owner seq + deleted_at tombstone to Note"
```

---

### Task 3: `persist_note` chokepoint — set seq + write vault; route ALL writers through it

The single defense against a writer skipping seq or vault projection.

**Files:**
- Create: `src/notes_service.py`
- Modify: `routes/note_routes.py` (create/update/pin/archive/toggle/reorder writers)
- Modify: `src/meeting_notes.py` (`save_meeting_note`, `promote_action_item`)
- Test: `tests/test_notes_service.py`

**Interfaces:**
- Consumes: `next_note_seq` (Task 2), `note_vault.write_note`/`remove_note` (Task 1), the `_note_to_dict`/`_resolve_links` helpers (Task 8 provides links; until then pass `[]`).
- Produces: `notes_service.persist_note(db, note, *, links=None) -> Note` (assigns `note.seq`, commits is caller's job OR documented here), `notes_service.delete_note(db, note) -> None` (soft-delete + vault remove).

- [ ] **Step 1: Write the failing test**

Create `tests/test_notes_service.py`:

```python
import os
from datetime import datetime, timezone

from core.database import SessionLocal, Note, init_db
import src.note_vault as note_vault
import src.notes_service as notes_service


def test_persist_note_sets_seq_and_writes_vault(tmp_path, monkeypatch):
    init_db()
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    db = SessionLocal()
    try:
        n = Note(id="svc-1", owner="svc-owner", title="Hello", content="body")
        db.add(n)
        notes_service.persist_note(db, n, links=[])
        db.commit()
        assert n.seq >= 1
        assert note_vault.vault_path_for(n).exists()
    finally:
        db.close()


def test_delete_note_soft_deletes_and_removes_vault(tmp_path, monkeypatch):
    init_db()
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    db = SessionLocal()
    try:
        n = Note(id="svc-2", owner="svc-owner", title="Bye", content="body")
        db.add(n)
        notes_service.persist_note(db, n, links=[])
        db.commit()
        path = note_vault.vault_path_for(n)
        assert path.exists()
        notes_service.delete_note(db, n)
        db.commit()
        assert n.deleted_at is not None
        assert not path.exists()
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_notes_service.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.notes_service'`

- [ ] **Step 3: Implement `src/notes_service.py`**

```python
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
```

- [ ] **Step 4: Route note_routes writers through it**

In `routes/note_routes.py`, after each successful note create/update/pin/archive/toggle and reorder (immediately before the existing `db.commit()`), replace ad-hoc commits with a `persist_note` call. Example for CREATE (adapt to the real handler body):

```python
        # was: db.add(note); db.commit()
        db.add(note)
        notes_service.persist_note(db, note, links=[])  # links filled in Task 8
        db.commit()
```

For UPDATE / pin / archive / toggle: after mutating the note fields, call `notes_service.persist_note(db, note, links=[])` before commit. For reorder (sort_order only): call `persist_note` for each touched note so seq advances and the frontmatter `updated` re-stamps.

Add the import at the top of `routes/note_routes.py`:

```python
import src.notes_service as notes_service
```

- [ ] **Step 5: Route meeting_notes writers through it**

In `src/meeting_notes.py`, `save_meeting_note` currently builds a `Note(...)` and commits. Replace its seq/commit tail with `notes_service.persist_note(db, note, links=<resolved links if available else []>)`. `promote_action_item` edits a note + creates a PlanItem — call `persist_note` on the note after the edit. (Import `src.notes_service as notes_service`.)

> **Sweep:** run `grep -rn "Note(" src/ routes/ mcp_servers/` and `grep -rn "\.seq" routes/note_routes.py src/meeting_notes.py` to confirm NO note writer commits without `persist_note`. Any agent/MCP note creator (`mcp_servers/`) must also route through it.

- [ ] **Step 6: Run tests + writer regression**

Run: `python -m pytest tests/test_notes_service.py tests/test_note_sync_contract.py -q`
Expected: PASS. Then `python -m compileall -q routes/note_routes.py src/meeting_notes.py src/notes_service.py` → clean.

- [ ] **Step 7: Commit**

```bash
git add src/notes_service.py routes/note_routes.py src/meeting_notes.py tests/test_notes_service.py
git commit -m "feat(notes): route all Note writers through persist_note (seq + vault)"
```

---

### Task 4: Soft-delete `DELETE` + exclude tombstones at all read sites

**Files:**
- Modify: `routes/note_routes.py` (DELETE handler + every live read)
- Modify: `src/meeting_notes.py` (dup/max scans), `routes/people_routes.py` / `routes/area_routes.py` (if they surface notes)
- Test: `tests/test_note_sync_contract.py` (extend)

**Interfaces:**
- Consumes: `notes_service.delete_note` (Task 3).

- [ ] **Step 1: Write the failing test (extend)**

Append to `tests/test_note_sync_contract.py`:

```python
def test_soft_deleted_note_excluded_from_list(monkeypatch, tmp_path):
    import src.note_vault as note_vault
    import src.notes_service as notes_service
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    init_db()
    db = SessionLocal()
    try:
        n = Note(id="del-list-1", owner="del-owner", title="t", content="c")
        db.add(n)
        notes_service.persist_note(db, n, links=[])
        db.commit()
        notes_service.delete_note(db, n)
        db.commit()
        live = (db.query(Note)
                  .filter(Note.owner == "del-owner", Note.deleted_at.is_(None))
                  .all())
        assert all(x.id != "del-list-1" for x in live)
    finally:
        db.close()
```

- [ ] **Step 2: Run to verify it passes already at the query level**

Run: `python -m pytest tests/test_note_sync_contract.py -q`
Expected: PASS (the test asserts the query idiom; next steps enforce it in routes).

- [ ] **Step 3: Convert DELETE to soft-delete**

In `routes/note_routes.py` DELETE handler, replace the hard `db.delete(note)` with:

```python
        notes_service.delete_note(db, note)
        db.commit()
        return {"ok": True}
```

- [ ] **Step 4: Add `deleted_at IS NULL` to every live read**

Add `Note.deleted_at.is_(None)` to the filter on: the LIST query, the single-note GET lookup, the pin/archive/toggle/reorder lookups, and any `meeting_notes` duplicate/max-ordinal scan, and any people/area page note aggregation. (Grep `db.query(Note)` and `\.filter(.*Note` across `routes/` + `src/`.)

- [ ] **Step 5: Run + compile**

Run: `python -m pytest tests/test_note_sync_contract.py -q` → PASS. `python -m compileall -q routes/note_routes.py` → clean.

- [ ] **Step 6: Commit**

```bash
git add routes/note_routes.py src/meeting_notes.py tests/test_note_sync_contract.py
git commit -m "feat(notes): soft-delete DELETE + exclude tombstones at read sites"
```

---

### Task 5: `GET /api/notes/changes?since=<seq>` delta feed

**Files:**
- Modify: `routes/note_routes.py` (new route + extend `_note_to_dict`)
- Test: `tests/test_note_sync_contract.py` (extend)

**Interfaces:**
- Consumes: `planner_routes` `GET /items/changes` shape (planner_routes.py:242-257).
- Produces: `GET /api/notes/changes?since=N` → `{"items": [...], "cursor": int}` where each item dict has `seq`, `deleted`, `deleted_at`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_note_sync_contract.py`:

```python
from fastapi.testclient import TestClient
# Use the project's existing test client/auth fixtures if present; otherwise
# call the handler function directly. Prefer the established pattern in
# tests/test_planner_*.py for the changes endpoint.

def test_changes_feed_returns_items_and_cursor_with_tombstones(monkeypatch, tmp_path):
    import src.note_vault as note_vault, src.notes_service as notes_service
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    init_db()
    db = SessionLocal()
    try:
        a = Note(id="chg-a", owner="chg-owner", title="a", content="x")
        db.add(a); notes_service.persist_note(db, a, links=[]); db.commit()
        since_after_a = a.seq
        b = Note(id="chg-b", owner="chg-owner", title="b", content="y")
        db.add(b); notes_service.persist_note(db, b, links=[]); db.commit()
        notes_service.delete_note(db, b); db.commit()
        # changes since `since_after_a` must include b (live then tombstoned)
        rows = (db.query(Note)
                  .filter(Note.owner == "chg-owner", Note.seq > since_after_a)
                  .order_by(Note.seq.asc()).all())
        assert any(r.id == "chg-b" and r.deleted_at is not None for r in rows)
    finally:
        db.close()
```

> Match the actual changes-endpoint test idiom in `tests/test_planner_*` (it likely mints a token / uses a client fixture). Replace the direct-query assertion with a real HTTP `GET /api/notes/changes?since=` assertion mirroring the planner test.

- [ ] **Step 2: Run to verify it fails / drives the endpoint**

Run: `python -m pytest tests/test_note_sync_contract.py -q`
Expected: PASS at query level (next step adds the HTTP route, then convert the test to hit it).

- [ ] **Step 3: Implement the endpoint**

In `routes/note_routes.py`, mirror `planner_routes` changes handler:

```python
    @router.get("/changes")
    def notes_changes(request: Request, since: int = 0):
        owner = _owner(request, allowed="notes:read")   # Task 6 gate
        db = SessionLocal()
        try:
            q = (db.query(Note)
                   .filter((Note.owner == owner) if owner else (Note.owner.is_(None)))
                   .filter(Note.seq > since)
                   .order_by(Note.seq.asc()))
            rows = q.all()
            items = [_note_to_dict(n) for n in rows]   # includes tombstones
            cursor = max((n.seq for n in rows), default=since)
            return {"items": items, "cursor": cursor}
        finally:
            db.close()
```

Extend `_note_to_dict` to include:

```python
        "seq": note.seq or 0,
        "deleted": note.deleted_at is not None,
        "deleted_at": note.deleted_at.isoformat() if note.deleted_at else None,
```

> **Route order:** register `/changes` BEFORE `/{note_id}` so it isn't captured as a note id (FastAPI matches in declaration order).

- [ ] **Step 4: Run the test (HTTP form) to verify it passes**

Run: `python -m pytest tests/test_note_sync_contract.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add routes/note_routes.py tests/test_note_sync_contract.py
git commit -m "feat(notes): add /api/notes/changes seq-cursor delta feed (incl. tombstones)"
```

---

### Task 6: Scope-aware owner gate on note endpoints

`notes:read`/`notes:write` already exist in `ALLOWED_SCOPES` and the `tide` profile (`api_token_routes.py`). Make the note endpoints honor them (today they're cookie-only via `require_user`).

**Files:**
- Modify: `routes/note_routes.py` (add `_owner`/`_load` scope-aware helpers; apply at each handler)
- Test: `tests/test_note_sync_contract.py` (extend — scope 403)

**Interfaces:**
- Consumes: planner's scope gate (`planner_routes.py:170-186`) — `request.state.api_token_scopes`.
- Produces: `_owner(request, allowed: str) -> owner`, `_load(request, note_id, allowed: str) -> Note`.

- [ ] **Step 1: Add the scope-aware helpers** (copy planner's `_owner`/`_load`, swap scope names to `notes:read`/`notes:write`). Reads require `notes:read`; writes require `notes:write`. Browser/cookie sessions (no api_token on `request.state`) keep working via the existing `require_user` fallback inside `_owner`.

- [ ] **Step 2: Apply at every handler** — pass `allowed="notes:read"` to LIST/GET/changes, `allowed="notes:write"` to POST/PUT/PATCH/DELETE/pin/archive/toggle/reorder.

- [ ] **Step 3: Write + run the 403 test**

```python
def test_notes_endpoints_reject_token_without_notes_scope():
    # Mint a todos:read-only token via the project's token-create helper,
    # GET /api/notes -> expect 403. Mirror the equivalent planner/people test.
    ...
```

Run: `python -m pytest tests/test_note_sync_contract.py -q` → PASS.

- [ ] **Step 4: Confirm the `tide` profile** already grants `notes:read`+`notes:write` (it does per `api_token_routes.py:43`). No change needed; assert in a quick test if a profile test exists.

- [ ] **Step 5: Commit**

```bash
git add routes/note_routes.py tests/test_note_sync_contract.py
git commit -m "feat(notes): scope-aware owner gate (notes:read/write) on note endpoints"
```

---

### Task 7: Client-UUID upsert on `POST` + partial `PATCH /api/notes/{id}`

The contract Tide's optimistic push needs (PATCH→404→POST with a client-supplied id).

**Files:**
- Modify: `routes/note_routes.py` (POST accepts optional client `id`; add PATCH route)
- Test: `tests/test_note_sync_contract.py` (extend)

**Interfaces:**
- Consumes: planner 1b-0 client-id upsert (`2da69c5`) + `PlanItemPatch` `exclude_unset` semantics (`8982775`).
- Produces: `POST /api/notes` honoring an optional body `id` (same-owner re-POST returns existing, no dup; cross-owner id → 404); `PATCH /api/notes/{id}` partial update (absent key ≠ null).

- [ ] **Step 1: Write the failing tests**

```python
def test_post_accepts_client_id_idempotently():
    # POST /api/notes {id: <uuid>, title:"x"} twice -> same row, no duplicate.
    ...

def test_patch_absent_key_is_not_null():
    # PATCH /api/notes/{id} {title:"new"} leaves content unchanged; explicit
    # {planned/content:null} clears. Mirror tests/test_planner_*patch*.
    ...
```

- [ ] **Step 2: POST — accept optional client id**

In the CREATE handler, before constructing `Note(...)`: if the body has `id`, look it up for this owner; if found return it (idempotent); if an id exists under a different owner → `raise HTTPException(404)`; else use the supplied id. Route through `persist_note`.

- [ ] **Step 3: Add PATCH route**

```python
    @router.patch("/{note_id}")
    def patch_note(note_id: str, patch: dict, request: Request):
        note = _load(request, note_id, allowed="notes:write")
        db = SessionLocal()
        try:
            note = db.merge(note)
            for key in ("title", "content", "color", "label", "pinned",
                        "archived", "note_type", "items"):
                if key in patch:                      # absent key != null
                    setattr(note, key, patch[key])
            notes_service.persist_note(db, note, links=[])  # links: Task 8
            db.commit()
            return _note_to_dict(note)
        finally:
            db.close()
```

> If the existing PUT/`NoteUpdate` is already all-Optional partial, you may instead register PATCH to the same handler. Read the handler first; reuse, don't duplicate.

- [ ] **Step 4: Run tests** → PASS. **Step 5: Commit**

```bash
git add routes/note_routes.py tests/test_note_sync_contract.py
git commit -m "feat(notes): client-UUID upsert on POST + partial PATCH route"
```

---

### Task 8: Wikilink ↔ Link reconciliation + frontmatter mirror + stale-edge cleanup

Server-owned (single writer). For Slice A, links inserted by Tide's picker are guaranteed-resolvable `[[Type/Name]]`.

**Files:**
- Create: `src/note_links.py` (parse + resolve + mirror)
- Modify: `src/notes_service.py` (`persist_note` resolves links when body changes)
- Modify: `src/meeting_notes.py` (reuse for the stale-edge cleanup TODO)
- Test: `tests/test_note_links.py`

**Interfaces:**
- Consumes: the hub `Link` model + link helpers (`core/hub_models.py` / wherever `set_area`/link upserts live), `Person`/`Area` lookups by name.
- Produces: `note_links.resolve_body_links(db, note) -> list[str]` (upserts Note→entity `Link` rows for `[[Type/Name]]` found in body; removes stale note-origin links; returns the `["[[Type/Name]]"]` list for frontmatter).

- [ ] **Step 1: Write the failing test**

```python
def test_resolve_body_links_creates_and_mirrors(tmp_path, monkeypatch):
    # Given a Person "Krishen" and Area "Work" exist for the owner, a note body
    # containing [[Person/Krishen]] and [[Area/Work]] -> two Note-origin Link
    # rows + returns ["[[Area/Work]]","[[Person/Krishen]]"] (sorted).
    ...

def test_resolve_removes_stale_links(tmp_path, monkeypatch):
    # Re-resolving a body that dropped [[Area/Work]] removes that Link row.
    ...
```

- [ ] **Step 2: Implement `src/note_links.py`**

Parse `\[\[(?P<type>Person|Area|Note|Meeting)/(?P<name>[^\]]+)\]\]` from the body; resolve each to an entity id by name (owner-scoped); upsert a Note→entity `Link` (rel by type: person→`about`, area→`in_area`, note→`from_note`, meeting→`note_of`); delete Note-origin links no longer present in the body; return the sorted `[[Type/Name]]` list. Use the SAME `Link` upsert/delete helpers the hub already exposes (do not write `Link` rows ad-hoc — find them via `grep -rn "class Link" core/ ; grep -rn "def set_area\|def .*link" core/hub_models.py src/`).

- [ ] **Step 3: Wire into `persist_note`**

In `notes_service.persist_note`, when `links is None`, compute them: `links = note_links.resolve_body_links(db, note)`. (Callers that already know links can pass them; the default path resolves from the body.) Pass the resolved list to `note_vault.write_note`.

- [ ] **Step 4: Reuse for meeting-note stale cleanup**

Replace the `src/meeting_notes.py` stale `note_of`/`about`/`attended_by` TODO with a call into `note_links` (or factor the stale-edge removal so both share it).

- [ ] **Step 5: Run + commit**

Run: `python -m pytest tests/test_note_links.py -q` → PASS.

```bash
git add src/note_links.py src/notes_service.py src/meeting_notes.py tests/test_note_links.py
git commit -m "feat(notes): resolve [[wikilinks]] to Link rows + frontmatter mirror + stale cleanup"
```

- [ ] **Step 6: Live smoke (server half)**

Restart uvicorn on `:7860` (no auto-reload). Mint a `tide` token. `curl` create a note with `id` + a `[[Area/<real area>]]` body → confirm: 200; a `.md` file appears under `data/vault/` with correct frontmatter + the wikilink; `GET /api/notes/changes?since=0` returns it with `seq`/`deleted:false`; `DELETE` → file gone + tombstone in changes; the Area page shows the note via reverse-Link.

---

# PART 2 — Tide client (consume the feed; notes UI)

Tide `Sources/TideCore/` files auto-discover; new files under `Tide/` (the app target) need `xcodegen generate` + `xcodebuild`.

---

### Task 9: Promote `TideNote` + full `NoteDTO` + mappers (+ fix composer call sites)

**Files:**
- Modify: `Sources/TideCore/Models/TideNote.swift`
- Modify: `Sources/TideCore/Sync/NoteDTO.swift` (add a sync DTO + changes response)
- Modify: composer call sites that construct `TideNote(id: String, …)`
- Test: `Tests/TideCoreTests/NoteSyncMappingTests.swift`

**Interfaces:**
- Consumes: `TidePerson`/`PersonDTO` pattern (no-arg init + `from(_:existing:)`).
- Produces: `TideNote` with `id: UUID`, `title`, `content`, `noteType`, `tags: String?`, `color: String?`, `pinned: Bool`, `archived: Bool`, `sortOrder: Int`, `createdAt: Date`, `updatedAt: Date`, `seq: Int`; `NoteSyncDTO` (Decodable, incl. `deleted`/`seq`) + `NotesChangesResponse`; `TideNote.from(_ dto: NoteSyncDTO, existing: TideNote?)`; `TideNote.patchBody()`/`createBody()` dicts for push.

- [ ] **Step 1: Write the failing test**

`Tests/TideCoreTests/NoteSyncMappingTests.swift`:

```swift
import XCTest
@testable import TideCore

final class NoteSyncMappingTests: XCTestCase {
    func testDTOMapsToTideNote() throws {
        let dto = NoteSyncDTO(id: "9F3C2A1B-0000-4000-8000-000000000001",
                              title: "Q3", content: "body",
                              noteType: "note", label: "work", color: "blue",
                              pinned: true, archived: false, sortOrder: 0,
                              createdAt: "2026-06-24T18:00:00Z",
                              updatedAt: "2026-06-24T18:30:00Z",
                              deleted: false, seq: 5)
        let note = TideNote.from(dto, existing: nil)
        XCTAssertEqual(note.title, "Q3")
        XCTAssertTrue(note.pinned)
        XCTAssertEqual(note.seq, 5)
    }
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `swift test --filter NoteSyncMappingTests`
Expected: FAIL (types not defined).

- [ ] **Step 3: Promote the model + DTO + mappers**

Rewrite `TideNote` to the new shape (id `UUID`, the fields above, no-arg `init()`, `from(_:existing:)` preserving local-only fields). Add `NoteSyncDTO`/`NotesChangesResponse` to `NoteDTO.swift` (keep the existing meeting-note `NoteDTO`/`ActionItemDTO` — they serve the composer; the new sync DTO is separate). Add `createBody()`/`patchBody()` returning `[String: Any]` (naive-UTC `Z` ISO; `patchBody` only includes changed fields per the planner pattern; preserve a server status shadow if applicable — N/A for notes).

- [ ] **Step 4: Fix composer call sites**

The meeting-note composer constructs `TideNote(id: String, …)`. Update to `UUID` (parse the server id string → `UUID`). Search: `rg "TideNote\(" Sources Tide`.

- [ ] **Step 5: Run test + build**

Run: `swift test --filter NoteSyncMappingTests` → PASS.

- [ ] **Step 6: Commit**

```bash
git add Sources/TideCore/Models/TideNote.swift Sources/TideCore/Sync/NoteDTO.swift Tests/TideCoreTests/NoteSyncMappingTests.swift
git commit -m "feat(note): promote TideNote to a synced entity + NoteSyncDTO + mappers"
```

---

### Task 10: GRDB v9 migration — rebuild `tideNotes` with the new columns

The v8 stub table (`id TEXT, title, content, createdAt`) must gain the sync columns and an id that round-trips as UUID.

**Files:**
- Modify: `Sources/TideCore/Store/TideDatabase.swift`
- Test: `Tests/TideCoreTests/NoteOnDiskMigrationTests.swift`

- [ ] **Step 1: Write the failing on-disk test**

```swift
func testV9AddsNoteSyncColumns() throws {
    let db = try DatabaseQueue(path: tmpPath)
    try TideDatabase.migrator.migrate(db)
    let cols = try db.read { try $0.columns(in: "tideNotes").map(\.name) }
    XCTAssertTrue(cols.contains("seq"))
    XCTAssertTrue(cols.contains("pinned"))
    XCTAssertTrue(cols.contains("updatedAt"))
}
```

- [ ] **Step 2: Run → fail. Step 3: Add migration v9**

```swift
        migrator.registerMigration("v9: tideNotes sync columns") { db in
            try db.alter(table: "tideNotes") { t in
                t.add(column: "noteType", .text).notNull().defaults(to: "note")
                t.add(column: "tags", .text)
                t.add(column: "color", .text)
                t.add(column: "pinned", .boolean).notNull().defaults(to: false)
                t.add(column: "archived", .boolean).notNull().defaults(to: false)
                t.add(column: "sortOrder", .integer).notNull().defaults(to: 0)
                t.add(column: "updatedAt", .datetime).notNull().defaults(to: Date(timeIntervalSince1970: 0))
                t.add(column: "seq", .integer).notNull().defaults(to: 0)
            }
        }
```

> `id` stays TEXT; the model maps TEXT↔UUID at the boundary (the stub already stored a UUID string). If the column must change affinity, instead drop+recreate `tideNotes` in v9 (the stub holds only transient meeting-note rows; acceptable). Confirm the latest registered version becomes **v9**.

- [ ] **Step 4: Run → PASS. Step 5: Commit**

```bash
git add Sources/TideCore/Store/TideDatabase.swift Tests/TideCoreTests/NoteOnDiskMigrationTests.swift
git commit -m "feat(db): v9 migration — tideNotes sync columns"
```

---

### Task 11: `EntitySync.note` descriptor + register + push wiring

**Files:**
- Create: `Sources/TideCore/Sync/EntitySync+Note.swift`
- Modify: the SyncCoordinator/SyncManager registration site (the `entities:` array)
- Modify: the TaskService/note write path to enqueue the note outbox op (or a `NoteService`)
- Test: `Tests/TideCoreTests/NoteSyncDescriptorTests.swift`

**Interfaces:**
- Consumes: `EntitySync` (SyncCoordinator.swift:18-59), `SyncCursor.noteEntity` (already defined), `SyncOutbox.enqueue(_:op:entity:in:)`, `OdysseusClient` (`resource: "notes"`).
- Produces: `EntitySync.note` (pull + push via outbox), registered in the coordinator's `entities` array.

- [ ] **Step 1: Write the failing test** — feed a `NotesChangesResponse` JSON into `EntitySync.note.applyChanges` against an in-memory DB; assert upsert + tombstone-delete + cursor return (mirror any existing `EntitySync` test for person/area).

- [ ] **Step 2: Run → fail. Step 3: Implement the descriptor**

Mirror `EntitySync+Person.swift`, but push is real (notes are read-write):

```swift
public extension EntitySync {
    static var note: EntitySync {
        EntitySync(
            entity: SyncCursor.noteEntity,
            resource: "notes",
            applyChanges: { data, db, _, _ in
                guard let resp = try? JSONDecoder().decode(NotesChangesResponse.self, from: data)
                else { return nil }
                for dto in resp.items {
                    guard let id = UUID(uuidString: dto.id) else { continue }
                    if dto.deleted {
                        try? db.write { try TideNote.where { $0.id.eq(id) }.delete().execute($0) }
                    } else {
                        let existing = try? db.read { try TideNote.where { $0.id.eq(id) }.fetchOne($0) }
                        let mapped = TideNote.from(dto, existing: existing)
                        try? db.write { try TideNote.upsert { mapped }.execute($0) }
                    }
                }
                return resp.cursor
            },
            pushUpsert: { id, db, client, _ in
                guard let note = try? db.read({ try TideNote.where { $0.id.eq(id) }.fetchOne($0) })
                else { return }
                do {
                    try await client.patch(resource: "notes", id: id.uuidString, note.patchBody())
                } catch OdysseusError.notFound {
                    try await client.create(resource: "notes", note.createBody())
                }
            },
            pushDelete: { id, client in
                try await client.delete(resource: "notes", id: id.uuidString)
            },
            liveExists: { id, db in
                (try? db.read { try TideNote.where { $0.id.eq(id) }.fetchOne($0) }) != nil
            }
        )
    }
}
```

- [ ] **Step 4: Register it** — add `.note` to the `entities` array at the coordinator construction site (`SyncCoordinator(..., entities: [.planItem, .area, .person, .link, .note])`). Find it via `rg "entities: \[" Sources Tide`.

- [ ] **Step 5: Enqueue on local note edits** — wherever Tide mutates a `TideNote` (the editor's save path / a `NoteService`), call `SyncOutbox.enqueue(id, op: SyncOutbox.Op.upsert, entity: SyncCursor.noteEntity, in: db)` (and `.delete` on delete). Mirror how task edits enqueue.

- [ ] **Step 6: Run → PASS. Step 7: Commit**

```bash
git add Sources/TideCore/Sync/EntitySync+Note.swift Tests/TideCoreTests/NoteSyncDescriptorTests.swift
git commit -m "feat(sync): EntitySync.note pull+push descriptor + register on the spine"
```

---

### Task 12: Notes list UI (macOS sidebar + iOS tab)

**Files:**
- Create: `Tide/Views/Notes/MacNotesView.swift`, `Tide/Views/Notes/NotesListView.swift` (iOS)
- Modify: the macOS sidebar + iOS tab roots; `project.yml` is unaffected (still under `Tide/`)
- Test: manual (UI) — covered by the live smoke (Task 14)

- [ ] **Step 1: Build the list** — `@FetchAll` `TideNote` where `archived == false`, sorted pinned-first then `updatedAt` desc. macOS: a sidebar "Notes" section + a list pane (mirror `MacPeopleView`). iOS: a "Notes" tab (mirror `PeopleListView`). Match existing card/row styling.

- [ ] **Step 2: Add nav entries** — macOS sidebar route `.notes`; iOS tab. Mirror the People wiring exactly.

- [ ] **Step 3: `xcodegen generate` + build**

Run: `xcodegen generate && xcodebuild -project Tide.xcodeproj -scheme Tide -configuration Debug -destination 'platform=macOS' -skipMacroValidation build`
Expected: BUILD SUCCEEDED.

- [ ] **Step 4: Commit**

```bash
git add Tide/ project.yml
git commit -m "feat(ui): Notes list — macOS sidebar + iOS tab"
```

---

### Task 13: Note detail + single-pane markdown editor + backlinks

**Files:**
- Create: `Tide/Views/Notes/MacNoteDetailView.swift`, `Tide/Views/Notes/NoteDetailView.swift` (iOS), `Tide/Views/Notes/NoteEditor.swift`, `Tide/Views/Notes/LinkPicker.swift`
- Test: pure-logic helpers unit-tested in `Tests/TideCoreTests/`; UI manual via live smoke

**Interfaces:**
- Consumes: `TideNote`, `SyncOutbox.enqueue` (Task 11), reverse-Link helpers (`linksTo(.note, id)`).

- [ ] **Step 1: Editor** — a single `TextEditor`-based markdown pane with live styling (or a lightweight markdown render-on-save). Title field + body. On edit, debounce → update `TideNote` (set `updatedAt`) → `SyncOutbox.enqueue(...note...)`.

- [ ] **Step 2: Slash menu** — typing `/` at line start opens a small menu (Heading, Bullet, Numbered, Task, Quote, Code, Divider) that inserts the markdown prefix. Keyboard-navigable. Keep it a pure helper (`SlashCommand.insert(into:at:)`) with unit tests.

- [ ] **Step 3: `@`/`[[` link picker** — typing `@` or `[[` opens `LinkPicker` over the synced graph (`TidePerson`, `Area`, `TideNote`, meetings); selecting inserts `[[Type/Name]]`. Server resolves it to a Link on save (Task 8).

- [ ] **Step 4: Checklist toggle** — tapping a rendered `- [ ]`/`- [x]` line flips it in the body text. Pure helper `Checklist.toggle(line:)` + unit test.

- [ ] **Step 5: Backlinks panel** — "Linked from" = `linksTo(.note, note.id)` reverse-Link query (mirror the Person/Area detail backlinks). Show outbound links too.

- [ ] **Step 6: `xcodegen generate` + build + unit tests**

Run: `swift test --filter Note` then `xcodegen generate && xcodebuild ... -destination 'platform=macOS' -skipMacroValidation build` (and an iOS-sim destination build).
Expected: PASS / BUILD SUCCEEDED both platforms.

- [ ] **Step 7: Commit**

```bash
git add Tide/ Sources/TideCore/ Tests/TideCoreTests/
git commit -m "feat(ui): note detail + markdown editor (slash, @/[[ linking, checklists) + backlinks"
```

---

### Task 14: Gated live smoke + GUI verification

**Files:**
- Create: `Tests/TideCoreTests/NoteVaultLiveTests.swift`

- [ ] **Step 1: Gated live test** (env-gated like `MeetingNoteSpineLiveTests`): with the brain on `:7860` + a `tide` token, drive `LiveOdysseusClient` + the note descriptor: create note (client UUID) → pull `/notes/changes` into a temp on-disk DB → assert the row → PATCH title+body → pull → assert update → delete → assert tombstone removes the row. Add a wikilink assertion: create with `[[Person/<id-name>]]`, then `GET /api/people/<id>` page (or the person reverse-Link) shows the note.

- [ ] **Step 2: Run the gated smoke**

```bash
ODYSSEUS_SMOKE_URL=http://localhost:7860 ODYSSEUS_SMOKE_TOKEN=<tide-token> \
  swift test --filter NoteVaultLiveTests
```
Expected: PASS (round-trip green); confirm a `.md` file appeared under `data/vault/`.

- [ ] **Step 3: Full suite + builds**

Run: `swift test` (all green / skips only) + `xcodebuild` macOS & iOS BUILD SUCCEEDED.

- [ ] **Step 4: GUI verify (the stale-build lesson)**

Rebuild + reinstall the app, then open it:

```bash
xcodebuild -project Tide.xcodeproj -scheme Tide -configuration Debug -destination 'platform=macOS' -skipMacroValidation -derivedDataPath .build-app build
osascript -e 'tell application "Tide" to quit'; rm -rf /Applications/Tide.app
cp -R .build-app/Build/Products/Debug/Tide.app /Applications/Tide.app
open -n /Applications/Tide.app
```
Confirm the Notes list + editor work against the running brain (create a note in Tide → appears in the web back-office + a `.md` file lands in `data/vault/`).

- [ ] **Step 5: Commit**

```bash
git add Tests/TideCoreTests/NoteVaultLiveTests.swift
git commit -m "test(note): gated live vault+sync round-trip smoke"
```

---

## Self-Review

**Spec coverage:**
- §2 vault (location/format/filenames/triggers/backfill) → Tasks 1, 3 (+ backfill: see note below). §3 sync feed (seq/deleted_at/changes/scopes/wikilink) → Tasks 2,4,5,6,8. §3.4 wikilink↔Link → Task 8. §4 Tide spine → Tasks 9,10,11. §5 UI → Tasks 12,13. §6 testing → every task's TDD + Task 14. §7 Slice B / §8 deferred → explicitly out.
- **GAP found:** §2.5 vault **backfill** (`run_hub_migrations` one-time pass writing `.md` for existing notes) is not its own task. **Fix:** add it as Task 3 Step (or a small Task 3b). → Added inline: see addendum below.

**Placeholder scan:** UI Tasks 12–13 describe views with concrete structure + the pure helpers carry real code/tests; SwiftUI view bodies follow the named existing views (`MacPeopleView`/`PersonDetailView`) — acceptable "mirror this file" references since those files exist in-repo. Server tasks carry full code. The few `...` markers in test stubs are explicitly "mirror the existing planner/people test idiom" — the implementer reads that neighbor; flagged, not silent.

**Type consistency:** `next_note_seq(db, owner)`, `persist_note(db, note, *, links=None)`, `delete_note(db, note)`, `note_vault.write_note(note, links)`, `NoteSyncDTO`/`NotesChangesResponse`, `TideNote.from(_:existing:)`/`createBody()`/`patchBody()`, `EntitySync.note`, `SyncCursor.noteEntity` — names consistent across server + Tide tasks.

### Addendum — Task 3b: Vault backfill (folds into Task 3)

- [ ] In `run_hub_migrations()` add a guarded idempotent pass: for every `Note` with `deleted_at IS NULL`, if `note_vault.vault_path_for(note)` is absent, call `note_vault.write_note(note, links=note_links.resolve_body_links(db, note))`. Log the count; never fail startup. Test: seed 2 notes pre-migration, run, assert 2 `.md` files exist.

```bash
git add core/hub_models.py tests/test_note_vault.py
git commit -m "feat(vault): one-time backfill of .md files for existing notes"
```
