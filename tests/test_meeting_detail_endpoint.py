"""GET /api/meeting-notes/meeting/{uid} — owner-scoped point lookup of a meeting.

Cases:
  (a) tide-scoped token (notes:read) -> 200 with detail dict for a known uid
  (b) unknown uid -> 404
  (c) owner isolation -> another owner's event uid -> 404
"""
import sys
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


class _Column:
    def __init__(self, field): self.field = field
    def __eq__(self, v): return True
    def in_(self, v): return True
    def ilike(self, v): return True
    def is_(self, v): return True
    def desc(self): return self


class _CalendarCal:
    id = _Column("CalendarCal.id"); owner = _Column("CalendarCal.owner")


class _CalendarEvent:
    uid = _Column("CalendarEvent.uid")
    calendar_id = _Column("CalendarEvent.calendar_id")
    summary = _Column("CalendarEvent.summary")
    dtstart = _Column("CalendarEvent.dtstart")


class _Note:
    id = _Column("Note.id"); owner = _Column("Note.owner"); deleted_at = _Column("Note.deleted_at")


class _FakeQuery:
    def filter(self, *_a, **_k): return self
    def order_by(self, *_a, **_k): return self
    def limit(self, *_a, **_k): return self
    def all(self): return []
    def first(self): return None


def _fake_event(owner="alice"):
    cal = SimpleNamespace(owner=owner, name="Work", color="#fff")
    return SimpleNamespace(
        uid="evt-1", summary="1:1 — Wiggert",
        dtstart=datetime(2026, 6, 24, 14, 0, 0),
        dtend=datetime(2026, 6, 24, 14, 30, 0),
        location="Room 3", all_day=False, is_utc=True, calendar=cal)


def _event_session(event):
    class _EvQuery(_FakeQuery):
        def first(self): return event
    class _EvSession:
        def query(self, model): return _EvQuery()
        def close(self): pass
    return _EvSession()


def _install_stubs(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.CalendarCal = _CalendarCal
    db.CalendarEvent = _CalendarEvent
    db.Note = _Note
    for name in ["Base", "Document", "DocumentVersion", "Session", "ChatMessage",
                 "GalleryImage", "GalleryAlbum", "ScheduledTask", "TaskRun",
                 "ModelEndpoint", "Webhook"]:
        setattr(db, name, MagicMock())
    monkeypatch.setitem(sys.modules, "core.database", db)

    mn = types.ModuleType("src.meeting_notes")
    mn.save_meeting_note = MagicMock(); mn.enrich_meeting_note = MagicMock()
    mn.promote_action_item = MagicMock(); mn._note_dict = MagicMock()
    monkeypatch.delitem(sys.modules, "src.meeting_notes", raising=False)
    monkeypatch.setitem(sys.modules, "src.meeting_notes", mn)
    if "src" not in sys.modules:
        monkeypatch.setitem(sys.modules, "src", types.ModuleType("src"))

    ah = types.ModuleType("src.auth_helpers")
    ah.require_user = MagicMock(return_value="alice")
    monkeypatch.setitem(sys.modules, "src.auth_helpers", ah)

    monkeypatch.delitem(sys.modules, "routes.meeting_notes_routes", raising=False)
    return __import__("routes.meeting_notes_routes",
                      fromlist=["setup_meeting_notes_routes"])


def _endpoint(mod, path, method):
    router = mod.setup_meeting_notes_routes()
    full = f"/api/meeting-notes{path}"
    for r in router.routes:
        if r.path == full and method in r.methods:
            return r.endpoint
    raise AssertionError(f"route not found: {method} {full}")


def _token_request(owner, scopes):
    return SimpleNamespace(state=SimpleNamespace(
        current_user="api", api_token=True,
        api_token_scopes=list(scopes), api_token_owner=owner))


def test_tide_token_can_get_meeting_detail(monkeypatch):
    mod = _install_stubs(monkeypatch)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _event_session(_fake_event("alice")))
    get_detail = _endpoint(mod, "/meeting/{uid}", "GET")
    result = get_detail(_token_request("alice", ["notes:read"]), uid="evt-1")
    assert result["uid"] == "evt-1"
    assert result["summary"] == "1:1 — Wiggert"
    assert result["dtstart"] == "2026-06-24T14:00:00Z"   # is_utc -> Z suffix
    assert result["dtend"] == "2026-06-24T14:30:00Z"
    assert result["location"] == "Room 3"
    assert result["all_day"] is False
    assert result["is_utc"] is True


def test_unknown_uid_404(monkeypatch):
    mod = _install_stubs(monkeypatch)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _event_session(None))
    get_detail = _endpoint(mod, "/meeting/{uid}", "GET")
    with pytest.raises(HTTPException) as exc:
        get_detail(_token_request("alice", ["notes:read"]), uid="nope")
    assert exc.value.status_code == 404


def test_owner_isolation_404(monkeypatch):
    mod = _install_stubs(monkeypatch)
    # Event belongs to bob; alice's token must get 404.
    monkeypatch.setattr(mod, "SessionLocal", lambda: _event_session(_fake_event("bob")))
    get_detail = _endpoint(mod, "/meeting/{uid}", "GET")
    with pytest.raises(HTTPException) as exc:
        get_detail(_token_request("alice", ["notes:read"]), uid="evt-1")
    assert exc.value.status_code == 404
