"""The sync hook is gated by the flag AND the calendar's link_attendees."""
from types import SimpleNamespace
from icalendar import Event
import src.caldav_sync as cs


def test_maybe_link_attendees_respects_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "CALENDAR_ATTENDEE_LINKING", False)
    monkeypatch.setattr(cs, "link_event_attendees",
                        lambda *a, **k: calls.append(a) or 0)
    cal = SimpleNamespace(link_attendees=True)
    cs._maybe_link_attendees(None, "alice", "evt", Event(), cal, self_addrs=set(), cache={})
    assert calls == []                              # flag off -> no call


def test_maybe_link_attendees_respects_calendar_optout(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "CALENDAR_ATTENDEE_LINKING", True)
    monkeypatch.setattr(cs, "link_event_attendees",
                        lambda *a, **k: calls.append(a) or 0)
    cal = SimpleNamespace(link_attendees=False)
    cs._maybe_link_attendees(None, "alice", "evt", Event(), cal, self_addrs=set(), cache={})
    assert calls == []                              # calendar opted out -> no call


def test_maybe_link_attendees_calls_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(cs, "CALENDAR_ATTENDEE_LINKING", True)
    monkeypatch.setattr(cs, "link_event_attendees",
                        lambda db, owner, uid, ev, **k: calls.append((owner, uid)) or 1)
    cal = SimpleNamespace(link_attendees=True)
    cs._maybe_link_attendees(None, "alice", "evt-9", Event(), cal, self_addrs=set(), cache={})
    assert calls == [("alice", "evt-9")]
