from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import SessionLocal, Note, init_db, Base
from core import hub_models
import src.notes_service as notes_service
import routes.note_routes as note_routes


# ---------------------------------------------------------------------------
# Shared in-memory DB helpers (mirror planner_changes test pattern)
# ---------------------------------------------------------------------------

def _sf():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _token_req(owner: str, scopes: list):
    """Simulate an API-token-authed request (mirrors planner scope tests)."""
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user=owner,
            api_token=True,
            api_token_owner=owner,
            api_token_scopes=scopes,
        )
    )


def _endpoint(router, path, method):
    full = f"/api/notes{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def _new_note(db, owner, title, monkeypatch, tmp_path):
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    n = Note(id=f"n-{title}", owner=owner, title=title, content="x")
    db.add(n)
    notes_service.persist_note(db, n, links=[])
    db.commit()
    return n


# ---------------------------------------------------------------------------
# Original DB-level tests (unchanged)
# ---------------------------------------------------------------------------

def test_next_note_seq_is_monotonic_per_owner():
    init_db()
    db = SessionLocal()
    try:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        SF = sessionmaker(bind=engine)
        db2 = SF()
        try:
            n1 = Note(id="seq-a1", owner="owner-seq-a", title="a1", content="x")
            db2.add(n1)
            n1.seq = hub_models.next_note_seq(db2, "owner-seq-a")
            db2.commit()
            n2 = Note(id="seq-a2", owner="owner-seq-a", title="a2", content="x")
            db2.add(n2)
            n2.seq = hub_models.next_note_seq(db2, "owner-seq-a")
            db2.commit()
            n3 = Note(id="seq-b1", owner="owner-seq-b", title="b1", content="x")
            db2.add(n3)
            n3.seq = hub_models.next_note_seq(db2, "owner-seq-b")
            db2.commit()
            assert n2.seq > n1.seq
            assert n3.seq >= 1
        finally:
            db2.close()
    finally:
        db.close()


def test_soft_deleted_note_excluded_from_list(monkeypatch, tmp_path):
    import src.note_vault as note_vault
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


# ---------------------------------------------------------------------------
# Changes endpoint tests (HTTP-shape, mirror planner_changes pattern)
# ---------------------------------------------------------------------------

def test_changes_no_since_returns_all_with_int_cursor(monkeypatch, tmp_path):
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    create = _endpoint(router, "", "POST")
    changes = _endpoint(router, "/changes", "GET")

    a = create(_req("alice"), body=note_routes.NoteCreate(title="A", content="x"))
    out = changes(_req("alice"), since=0)
    assert {i["id"] for i in out["items"]} == {a["id"]}
    assert out["cursor"] == a["seq"]


def test_changes_since_is_exclusive_and_includes_tombstones(monkeypatch, tmp_path):
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    create = _endpoint(router, "", "POST")
    delete = _endpoint(router, "/{note_id}", "DELETE")
    changes = _endpoint(router, "/changes", "GET")

    a = create(_req("alice"), body=note_routes.NoteCreate(title="A", content="x"))
    b = create(_req("alice"), body=note_routes.NoteCreate(title="B", content="y"))
    delete(_req("alice"), note_id=b["id"])

    out = changes(_req("alice"), since=a["seq"])   # since=seq_a -> seq > seq_a
    ids = {i["id"]: i for i in out["items"]}
    assert a["id"] not in ids                      # seq_a not > seq_a
    assert b["id"] in ids                          # tombstoned b is included
    assert ids[b["id"]]["deleted"] is True
    assert ids[b["id"]]["deleted_at"] is not None


def test_changes_cursor_empty_batch_echoes_since(monkeypatch, tmp_path):
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    create = _endpoint(router, "", "POST")
    changes = _endpoint(router, "/changes", "GET")

    a = create(_req("alice"), body=note_routes.NoteCreate(title="A", content="x"))
    out = changes(_req("alice"), since=a["seq"])   # nothing newer
    assert out["items"] == []
    assert out["cursor"] == a["seq"]


def test_changes_owner_scoped(monkeypatch, tmp_path):
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    create = _endpoint(router, "", "POST")
    changes = _endpoint(router, "/changes", "GET")

    create(_req("alice"), body=note_routes.NoteCreate(title="Alice note"))
    create(_req("bob"), body=note_routes.NoteCreate(title="Bob note"))

    out = changes(_req("alice"), since=0)
    assert all(i["owner"] == "alice" for i in out["items"])
    assert len(out["items"]) == 1


# ---------------------------------------------------------------------------
# Scope gate tests (Task 6: API-token scope enforcement)
# Mirror the equivalent planner/people scope tests.
# ---------------------------------------------------------------------------

def test_notes_endpoints_reject_token_without_notes_scope(monkeypatch, tmp_path):
    """A token carrying only todos:read (no notes scope) must get 403 on GET /api/notes."""
    import src.note_vault as note_vault
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    list_ep = _endpoint(router, "", "GET")

    req = _token_req("alice", ["todos:read"])  # no notes scope
    with pytest.raises(HTTPException) as exc_info:
        list_ep(req)
    assert exc_info.value.status_code == 403


def test_notes_read_token_allows_list(monkeypatch, tmp_path):
    """A token with notes:read satisfies GET /api/notes."""
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    list_ep = _endpoint(router, "", "GET")

    req = _token_req("alice", ["notes:read"])
    result = list_ep(req)
    assert "notes" in result


def test_notes_write_token_allows_list(monkeypatch, tmp_path):
    """A token with notes:write also satisfies GET /api/notes (write implies read)."""
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    list_ep = _endpoint(router, "", "GET")

    req = _token_req("alice", ["notes:write"])
    result = list_ep(req)
    assert "notes" in result


def test_notes_read_token_rejects_write(monkeypatch, tmp_path):
    """A token with only notes:read must get 403 on write endpoints (POST /api/notes)."""
    import src.note_vault as note_vault
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    create_ep = _endpoint(router, "", "POST")

    req = _token_req("alice", ["notes:read"])
    with pytest.raises(HTTPException) as exc_info:
        create_ep(req, body=note_routes.NoteCreate(title="X"))
    assert exc_info.value.status_code == 403


def test_notes_write_token_allows_create(monkeypatch, tmp_path):
    """A token with notes:write can POST /api/notes."""
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    create_ep = _endpoint(router, "", "POST")

    req = _token_req("alice", ["notes:write"])
    result = create_ep(req, body=note_routes.NoteCreate(title="From token"))
    assert result["title"] == "From token"
    assert result["owner"] == "alice"


def test_notes_changes_endpoint_rejects_token_without_scope(monkeypatch, tmp_path):
    """GET /api/notes/changes must reject a token with no notes scope."""
    import src.note_vault as note_vault
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    changes_ep = _endpoint(router, "/changes", "GET")

    req = _token_req("alice", ["calendar:read"])
    with pytest.raises(HTTPException) as exc_info:
        changes_ep(req, since=0)
    assert exc_info.value.status_code == 403


def test_notes_tide_token_scopes_allow_list_and_create(monkeypatch, tmp_path):
    """The tide token profile includes notes:read + notes:write — both gates pass."""
    import src.note_vault as note_vault
    monkeypatch.setattr(note_vault, "VAULT_DIR", str(tmp_path))
    SF = _sf()
    monkeypatch.setattr(note_routes, "SessionLocal", SF)
    router = note_routes.setup_note_routes()
    list_ep = _endpoint(router, "", "GET")
    create_ep = _endpoint(router, "", "POST")

    tide_scopes = [
        "todos:read", "todos:write", "notes:read", "notes:write",
        "calendar:read", "people:read",
    ]
    req = _token_req("alice", tide_scopes)
    list_result = list_ep(req)
    assert "notes" in list_result

    create_result = create_ep(req, body=note_routes.NoteCreate(title="Tide note"))
    assert create_result["title"] == "Tide note"
