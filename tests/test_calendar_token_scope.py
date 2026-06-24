"""Pin that GET /api/calendar/events accepts API tokens with calendar:read scope.

Three cases:
  (a) calendar:read token -> 200 (no events returned)
  (b) todos:read token (wrong scope) -> 403
  (c) cookie-session (no token) -> still works

All tests drive the endpoint function directly using the same
_Column/_Expr/_FakeQuery stub pattern established in test_calendar_owner_scope.py.
"""
import asyncio
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


# ── lightweight expression stubs (mirrors test_calendar_owner_scope.py) ──────

class _Expr:
    def __init__(self, op, field=None, value=None, children=()):
        self.op = op
        self.field = field
        self.value = value
        self.children = tuple(children)

    def __or__(self, other):  return _Expr("or",  children=(self, other))
    def __and__(self, other): return _Expr("and", children=(self, other))
    def __bool__(self):       return True  # so `if q.filter(...)` works


class _Column:
    def __init__(self, field):
        self.field = field

    def __eq__(self, v):    return _Expr("eq",    self.field, v)
    def __ne__(self, v):    return _Expr("ne",    self.field, v)
    def __lt__(self, v):    return _Expr("lt",    self.field, v)
    def __gt__(self, v):    return _Expr("gt",    self.field, v)
    def is_(self, v):       return _Expr("is",    self.field, v)
    def isnot(self, v):     return _Expr("isnot", self.field, v)
    # allow bitwise OR used in the `(CalendarEvent.calendar_id == x) | ...` path
    def __or__(self, other): return _Expr("or",   children=(self, other))


class _CalendarCal:
    id          = _Column("CalendarCal.id")
    owner       = _Column("CalendarCal.owner")
    name        = _Column("CalendarCal.name")


class _CalendarEvent:
    uid         = _Column("CalendarEvent.uid")
    status      = _Column("CalendarEvent.status")
    rrule       = _Column("CalendarEvent.rrule")
    dtstart     = _Column("CalendarEvent.dtstart")
    dtend       = _Column("CalendarEvent.dtend")
    calendar_id = _Column("CalendarEvent.calendar_id")


class _FakeQuery:
    def join(self, *_a, **_k):    return self
    def filter(self, *_a, **_k):  return self
    def order_by(self, *_a, **_k):return self
    def all(self):                 return []
    def first(self):               return None


class _FakeSession:
    def query(self, _model): return _FakeQuery()
    def close(self):         pass


# ── stub installers ──────────────────────────────────────────────────────────

def _install_calendar_db_stub(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.CalendarCal = _CalendarCal
    db.CalendarDeletedEvent = MagicMock()
    db.CalendarEvent = _CalendarEvent
    for name in [
        "Base", "Document", "DocumentVersion", "Session", "ChatMessage",
        "GalleryImage", "GalleryAlbum", "Note", "ScheduledTask", "TaskRun",
        "ModelEndpoint", "Webhook",
    ]:
        setattr(db, name, MagicMock())
    monkeypatch.setitem(sys.modules, "core.database", db)
    return db


def _install_multipart_stub(monkeypatch):
    m = types.ModuleType("python_multipart")
    m.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", m)


def _import_calendar_routes(monkeypatch):
    _install_calendar_db_stub(monkeypatch)
    _install_multipart_stub(monkeypatch)
    monkeypatch.delitem(sys.modules, "routes.calendar_routes", raising=False)
    mod = __import__("routes.calendar_routes", fromlist=["setup_calendar_routes"])
    # Patch or_/and_ to return _Expr-style objects so filter() succeeds.
    monkeypatch.setattr(mod, "or_",  lambda *a: _Expr("or",  children=a))
    monkeypatch.setattr(mod, "and_", lambda *a: _Expr("and", children=a))
    return mod


def _route_endpoint(cal_routes, path, method):
    router = cal_routes.setup_calendar_routes()
    full_path = f"/api/calendar{path}"
    for route in router.routes:
        if route.path == full_path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {full_path}")


# ── request factories ────────────────────────────────────────────────────────

def _cookie_request(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user, api_token=False))


def _token_request(owner, scopes):
    return SimpleNamespace(state=SimpleNamespace(
        current_user="api",
        api_token=True,
        api_token_scopes=list(scopes),
        api_token_owner=owner,
    ))


# ── tests ────────────────────────────────────────────────────────────────────

def test_calendar_read_token_gets_200(monkeypatch):
    """(a) calendar:read token must reach the query layer and return events."""
    cal_routes = _import_calendar_routes(monkeypatch)
    monkeypatch.setattr(cal_routes, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(cal_routes, "_expand_rrule", lambda e, s, en: [])

    list_events = _route_endpoint(cal_routes, "/events", "GET")
    result = asyncio.run(list_events(
        _token_request("alice", ["calendar:read"]),
        start="2026-06-01T00:00:00",
        end="2026-06-30T00:00:00",
    ))

    assert result == {"events": []}


def test_calendar_write_token_also_gets_200(monkeypatch):
    """calendar:write should also satisfy the scope check on GET /events."""
    cal_routes = _import_calendar_routes(monkeypatch)
    monkeypatch.setattr(cal_routes, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(cal_routes, "_expand_rrule", lambda e, s, en: [])

    list_events = _route_endpoint(cal_routes, "/events", "GET")
    result = asyncio.run(list_events(
        _token_request("alice", ["calendar:write"]),
        start="2026-06-01T00:00:00",
        end="2026-06-30T00:00:00",
    ))

    assert result == {"events": []}


def test_wrong_scope_token_gets_403(monkeypatch):
    """(b) A token with only todos:read must be rejected with 403."""
    cal_routes = _import_calendar_routes(monkeypatch)
    # No DB/session needed — the 403 fires before any DB access.

    list_events = _route_endpoint(cal_routes, "/events", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(list_events(
            _token_request("alice", ["todos:read"]),
            start="2026-06-01T00:00:00",
            end="2026-06-30T00:00:00",
        ))

    assert exc.value.status_code == 403
    # Error message should mention the allowed scopes.
    assert "calendar" in exc.value.detail


def test_no_scope_token_gets_403(monkeypatch):
    """A token with an empty scope list must also be rejected with 403."""
    cal_routes = _import_calendar_routes(monkeypatch)

    list_events = _route_endpoint(cal_routes, "/events", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(list_events(
            _token_request("alice", []),
            start="2026-06-01T00:00:00",
            end="2026-06-30T00:00:00",
        ))

    assert exc.value.status_code == 403


def test_cookie_session_still_works(monkeypatch):
    """(c) Cookie-session callers (api_token=False) must continue to work."""
    cal_routes = _import_calendar_routes(monkeypatch)
    monkeypatch.setattr(cal_routes, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(cal_routes, "_expand_rrule", lambda e, s, en: [])
    # Stub require_user so the cookie path resolves without a real auth stack.
    monkeypatch.setattr(cal_routes, "require_user", lambda req: req.state.current_user)

    list_events = _route_endpoint(cal_routes, "/events", "GET")
    result = asyncio.run(list_events(
        _cookie_request("alice"),
        start="2026-06-01T00:00:00",
        end="2026-06-30T00:00:00",
    ))

    assert result == {"events": []}
