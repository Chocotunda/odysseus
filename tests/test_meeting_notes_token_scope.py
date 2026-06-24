"""Pin that meeting-notes endpoints accept API tokens with notes:read/notes:write scopes.

Five cases (from the implementation spec):
  (a) notes:write token -> POST /api/meeting-notes -> succeeds
  (b) notes:read token -> GET /api/meeting-notes/{id} and GET /api/meeting-notes/meetings -> 200
  (c) notes:read token -> POST /api/meeting-notes -> 403
  (d) token with no notes scope -> 403 on all meeting-notes endpoints
  (e) cookie session -> still works (unchanged behaviour)

All tests drive the endpoint functions directly via the same
_FakeQuery/_FakeSession stub pattern established in test_calendar_token_scope.py.
"""
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


# ── lightweight DB stubs ──────────────────────────────────────────────────────

class _Column:
    def __init__(self, field):
        self.field = field

    def __eq__(self, v):    return True
    def in_(self, v):       return True
    def ilike(self, v):     return True
    def desc(self):         return self
    def asc(self):          return self


class _CalendarCal:
    id    = _Column("CalendarCal.id")
    owner = _Column("CalendarCal.owner")


class _CalendarEvent:
    calendar_id = _Column("CalendarEvent.calendar_id")
    summary     = _Column("CalendarEvent.summary")
    dtstart     = _Column("CalendarEvent.dtstart")


class _Note:
    id    = _Column("Note.id")
    owner = _Column("Note.owner")


class _FakeQuery:
    def filter(self, *_a, **_k):   return self
    def order_by(self, *_a, **_k): return self
    def limit(self, *_a, **_k):    return self
    def all(self):                  return []
    def first(self):                return None


class _FakeSession:
    def query(self, _model): return _FakeQuery()
    def close(self):         pass


# ── stub installers ───────────────────────────────────────────────────────────

def _install_core_db_stub(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock(return_value=_FakeSession())
    db.CalendarCal = _CalendarCal
    db.CalendarEvent = _CalendarEvent
    db.Note = _Note
    for name in [
        "Base", "Document", "DocumentVersion", "Session", "ChatMessage",
        "GalleryImage", "GalleryAlbum", "ScheduledTask", "TaskRun",
        "ModelEndpoint", "Webhook",
    ]:
        setattr(db, name, MagicMock())
    monkeypatch.setitem(sys.modules, "core.database", db)
    return db


def _install_meeting_notes_src_stub(monkeypatch):
    """Stub src.meeting_notes so we don't need the full dependency chain.

    We also remove any previously-cached real module so the fresh import of
    routes.meeting_notes_routes picks up our stub rather than the real module.
    """
    mn = types.ModuleType("src.meeting_notes")
    mn.save_meeting_note = MagicMock(return_value={
        "note": {"id": "note-123", "title": "Test", "content": "", "action_items": []}
    })
    mn.enrich_meeting_note = MagicMock()
    mn.promote_action_item = MagicMock(return_value={"task": {"id": "task-456"}})
    mn._note_dict = MagicMock(return_value={"id": "note-123", "title": "Test"})
    # Remove real module from cache so the fresh route import binds our stub.
    monkeypatch.delitem(sys.modules, "src.meeting_notes", raising=False)
    monkeypatch.setitem(sys.modules, "src.meeting_notes", mn)
    # Also stub the 'src' package if not present.
    if "src" not in sys.modules:
        src_pkg = types.ModuleType("src")
        monkeypatch.setitem(sys.modules, "src", src_pkg)
    return mn


def _install_auth_helpers_stub(monkeypatch):
    ah = types.ModuleType("src.auth_helpers")
    ah.require_user = MagicMock(return_value="alice")
    monkeypatch.setitem(sys.modules, "src.auth_helpers", ah)
    return ah


def _import_meeting_notes_routes(monkeypatch):
    _install_core_db_stub(monkeypatch)
    mn_stub = _install_meeting_notes_src_stub(monkeypatch)
    _install_auth_helpers_stub(monkeypatch)
    monkeypatch.delitem(sys.modules, "routes.meeting_notes_routes", raising=False)
    mod = __import__("routes.meeting_notes_routes", fromlist=["setup_meeting_notes_routes"])
    # Patch the module-level MN reference in case the real src.meeting_notes was
    # already bound before our stub was installed (ordering artefact in full-suite runs).
    monkeypatch.setattr(mod, "MN", mn_stub)
    return mod


def _route_endpoint(mod, path, method):
    router = mod.setup_meeting_notes_routes()
    full_path = f"/api/meeting-notes{path}"
    for route in router.routes:
        if route.path == full_path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full_path}")


# ── request factories ─────────────────────────────────────────────────────────

def _cookie_request(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _token_request(owner, scopes):
    return SimpleNamespace(state=SimpleNamespace(
        current_user="api",
        api_token=True,
        api_token_scopes=list(scopes),
        api_token_owner=owner,
    ))


# ── (a) notes:write token -> POST succeeds ────────────────────────────────────

def test_notes_write_token_can_post(monkeypatch):
    """(a) notes:write token must be allowed to create a meeting note."""
    mod = _import_meeting_notes_routes(monkeypatch)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSession())

    save = _route_endpoint(mod, "", "POST")

    from routes.meeting_notes_routes import MeetingNoteSave
    body = MeetingNoteSave(title="Stand-up", content="discussed things")

    # BackgroundTasks stub
    bg = MagicMock()
    bg.add_task = MagicMock()

    result = save(_token_request("alice", ["notes:write"]), body, bg)
    assert "note" in result


# ── (b) notes:read token -> GET meetings list and GET /{id} succeed ──────────

def test_notes_read_token_can_list_meetings(monkeypatch):
    """(b) notes:read token must be allowed to list meetings."""
    mod = _import_meeting_notes_routes(monkeypatch)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSession())

    list_meetings = _route_endpoint(mod, "/meetings", "GET")
    result = list_meetings(_token_request("alice", ["notes:read"]), q="")
    assert result == {"meetings": []}


def test_notes_write_token_can_also_list_meetings(monkeypatch):
    """notes:write satisfies the notes:read scope check on GET /meetings."""
    mod = _import_meeting_notes_routes(monkeypatch)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSession())

    list_meetings = _route_endpoint(mod, "/meetings", "GET")
    result = list_meetings(_token_request("alice", ["notes:write"]), q="")
    assert result == {"meetings": []}


def test_notes_read_token_can_get_note(monkeypatch):
    """(b) notes:read token must be allowed to fetch a note by id."""
    mod = _import_meeting_notes_routes(monkeypatch)
    # Patch SessionLocal to return a session where Note.first() returns a fake note.
    fake_note = SimpleNamespace(id="note-123", owner="alice")

    class _NoteQuery(_FakeQuery):
        def first(self): return fake_note

    class _NoteSession(_FakeSession):
        def query(self, model):
            # Return a query that yields the fake note for Note lookups.
            return _NoteQuery()

    monkeypatch.setattr(mod, "SessionLocal", lambda: _NoteSession())
    # Patch _note_dict directly on the MN module reference inside the route module.
    mod.MN._note_dict = MagicMock(return_value={"id": "note-123", "title": "Test"})

    get_note = _route_endpoint(mod, "/{note_id}", "GET")
    result = get_note(_token_request("alice", ["notes:read"]), note_id="note-123")
    assert result["id"] == "note-123"


# ── (c) notes:read token -> POST -> 403 ──────────────────────────────────────

def test_notes_read_token_cannot_post(monkeypatch):
    """(c) notes:read token must be rejected with 403 on POST."""
    mod = _import_meeting_notes_routes(monkeypatch)

    save = _route_endpoint(mod, "", "POST")

    from routes.meeting_notes_routes import MeetingNoteSave
    body = MeetingNoteSave(title="Stand-up", content="")
    bg = MagicMock()

    with pytest.raises(HTTPException) as exc:
        save(_token_request("alice", ["notes:read"]), body, bg)

    assert exc.value.status_code == 403
    assert "notes" in exc.value.detail


def test_notes_read_token_cannot_promote(monkeypatch):
    """notes:read token must be rejected with 403 on POST /{id}/promote."""
    mod = _import_meeting_notes_routes(monkeypatch)

    promote = _route_endpoint(mod, "/{note_id}/promote", "POST")

    from routes.meeting_notes_routes import PromoteBody
    body = PromoteBody(title="Action item")

    with pytest.raises(HTTPException) as exc:
        promote(_token_request("alice", ["notes:read"]), note_id="note-123", body=body)

    assert exc.value.status_code == 403


# ── (d) token with no notes scope -> 403 everywhere ──────────────────────────

def test_wrong_scope_token_cannot_list_meetings(monkeypatch):
    """(d) A token without notes scope must be rejected on GET /meetings."""
    mod = _import_meeting_notes_routes(monkeypatch)

    list_meetings = _route_endpoint(mod, "/meetings", "GET")
    with pytest.raises(HTTPException) as exc:
        list_meetings(_token_request("alice", ["calendar:read"]), q="")

    assert exc.value.status_code == 403
    assert "notes" in exc.value.detail


def test_no_scope_token_cannot_get_note(monkeypatch):
    """(d) A token with empty scopes must be rejected on GET /{id}."""
    mod = _import_meeting_notes_routes(monkeypatch)

    get_note = _route_endpoint(mod, "/{note_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        get_note(_token_request("alice", []), note_id="note-123")

    assert exc.value.status_code == 403


def test_no_scope_token_cannot_post(monkeypatch):
    """(d) A token with empty scopes must be rejected on POST."""
    mod = _import_meeting_notes_routes(monkeypatch)

    save = _route_endpoint(mod, "", "POST")

    from routes.meeting_notes_routes import MeetingNoteSave
    body = MeetingNoteSave(title="Test", content="")
    bg = MagicMock()

    with pytest.raises(HTTPException) as exc:
        save(_token_request("alice", []), body, bg)

    assert exc.value.status_code == 403


# ── (e) cookie session -> still works ────────────────────────────────────────

def test_cookie_session_can_list_meetings(monkeypatch):
    """(e) Cookie-session callers must continue to work on GET /meetings."""
    mod = _import_meeting_notes_routes(monkeypatch)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(mod, "require_user", lambda req: req.state.current_user)

    list_meetings = _route_endpoint(mod, "/meetings", "GET")
    result = list_meetings(_cookie_request("alice"), q="")
    assert result == {"meetings": []}


def test_cookie_session_can_post(monkeypatch):
    """(e) Cookie-session callers must continue to work on POST."""
    mod = _import_meeting_notes_routes(monkeypatch)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(mod, "require_user", lambda req: req.state.current_user)

    save = _route_endpoint(mod, "", "POST")

    from routes.meeting_notes_routes import MeetingNoteSave
    body = MeetingNoteSave(title="Weekly sync", content="")
    bg = MagicMock()
    bg.add_task = MagicMock()

    result = save(_cookie_request("alice"), body, bg)
    assert "note" in result
