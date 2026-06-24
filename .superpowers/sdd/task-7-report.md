# Task 7 Report: client-UUID upsert on POST + partial PATCH route

## Status: COMPLETE

---

## POST upsert logic (how it mirrors planner)

Added `id: Optional[str] = None` to `NoteCreate`.

In `create_note`, before constructing `Note(...)`:
1. If `body.id` is set, query for an existing row by that id.
2. Found + same owner → return `_note_to_dict(existing)` (idempotent, no duplicate or overwrite).
3. Found under a **different owner** → `raise HTTPException(404)` (no cross-owner existence leak).
4. Not found → create with `id=body.id or str(uuid.uuid4())`.

Mirrors `planner_routes.py` `create_item` (commit `2da69c5`) exactly.

---

## PATCH partial semantics + DRY decision

The existing PUT handler (`update_note`) uses `NoteUpdate` with all-Optional fields but applies them with `if body.field is not None:` guards. This cannot clear a field to null — explicit `content: null` in PUT is silently ignored. Semantically wrong for PATCH.

Decision: **did NOT reuse the PUT handler**. Added separate `NotePatch` Pydantic model and `patch_note` handler using `body.model_dump(exclude_unset=True)`. Mirrors `PlanItemPatch` / `patch_item` semantics from planner_routes.py (commit `8982775`) exactly:
- Absent key → field untouched.
- Explicit null → field cleared to None.

The `items` field gets special handling (stored as JSON string; `json.dumps(value) if value is not None else None` + `flag_modified()`).

---

## TDD evidence

### RED (before implementation)
```
FAILED test_post_accepts_client_id_idempotently
FAILED test_post_client_id_cross_owner_404
FAILED test_patch_absent_key_is_not_null
FAILED test_patch_explicit_null_clears_field
FAILED test_patch_cross_owner_404
5 failed, 14 passed
```

### GREEN (after implementation)
```
19 passed, 1 warning in 0.28s
```

---

## Broad note sweep

```
85 passed, 3951 deselected, 1 warning in 2.82s
```
0 failures.

---

## compileall

```
python -m compileall -q routes/note_routes.py → CLEAN
```

---

## Files changed

- `routes/note_routes.py`: added `id` field to `NoteCreate`, added `NotePatch` model, updated `create_note` with client-id upsert logic, added `patch_note` handler at `PATCH /{note_id}`.
- `tests/test_note_sync_contract.py`: 6 new tests added (upsert idempotent, cross-owner 404, server-generates id, patch absent key, patch explicit null, patch cross-owner).

---

## Self-review

- Owner-scoping enforced on both POST upsert (cross-owner id → 404) and PATCH (cross-owner → 404).
- `flag_modified` applied for the `items` JSON column in the PATCH handler.
- PUT handler unchanged — no regressions to existing callers.
- `NotePatch` exported at module level so tests can use `note_routes.NotePatch.model_validate(...)`.

## Concerns

None.

---

## Fix: revive-on-re-POST

### Bug fixed

`POST /api/notes` with a client-supplied id that matched a soft-deleted (tombstoned) row was returning the tombstone dict (`deleted: true`) instead of recreating the note. The upsert check `db.query(Note).filter(Note.id == body.id).first()` finds all rows regardless of `deleted_at`, but the original code returned the first match unconditionally — so a revived id just got the dead row back.

### Logic applied

The `create_note` handler's client-id upsert branch in `routes/note_routes.py` now has four cases instead of two:

1. `existing` found + cross-owner → `raise HTTPException(404)` (unchanged).
2. `existing` found + same-owner + `deleted_at is None` → return `_note_to_dict(existing)` (idempotent, unchanged).
3. **NEW** `existing` found + same-owner + `deleted_at is not None` (tombstone) → **REVIVE**: clear `deleted_at`, re-apply all body fields (title, content, items, note_type, color, label, pinned, due_date, source, session_id, image_url, repeat, sort_order), call `notes_service.persist_note(db, existing, links=[])` (bumps seq + rewrites vault file), `db.commit()`, return `_note_to_dict(existing)`.
4. No existing row → create new (unchanged).

Also removed the redundant inline `from sqlalchemy.orm.attributes import flag_modified` import inside `patch_note` (it was already imported at module top, line 16).

### New test

`test_post_revives_soft_deleted_note` in `tests/test_note_sync_contract.py`:
- Creates note id=X, soft-deletes it, confirms tombstone in DB.
- Re-POSTs id=X with new title/content.
- Asserts `deleted is False`, `deleted_at is None`, body reflects re-POST values.
- Asserts `db.query(Note).filter(Note.id==X).count() == 1` (revived in-place, no duplicate).

### Verify outputs

```
python -m pytest tests/test_note_sync_contract.py -q
20 passed, 1 warning in 0.33s
```

```
python -m pytest tests/ -q -k "note or Note"
86 passed, 3951 deselected, 1 warning in 2.88s
```

```
python -m compileall -q routes/note_routes.py → CLEAN
```
