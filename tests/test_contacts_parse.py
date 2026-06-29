"""Pure parsing: normalize_email + parse_attendees (icalendar, no DB)."""
from icalendar import Event, vCalAddress, vText

from src.contacts import normalize_email, parse_attendees


def test_normalize_email():
    assert normalize_email("MAILTO:Foo@Bar.com") == "foo@bar.com"
    assert normalize_email("mailto:a@b.io") == "a@b.io"
    assert normalize_email("  C@D.com ") == "c@d.com"
    assert normalize_email("not-an-email") is None
    assert normalize_email("") is None
    assert normalize_email("mailto:") is None


def _attendee(addr, cn=None, cutype=None):
    a = vCalAddress(addr)
    if cn:
        a.params["CN"] = vText(cn)
    if cutype:
        a.params["CUTYPE"] = vText(cutype)
    return a


def test_parse_attendees_cn_and_fallback():
    ev = Event()
    ev.add("attendee", _attendee("mailto:wiggert@firm.nl", cn="Wiggert Loonstra"))
    ev.add("attendee", _attendee("mailto:npd@gmail.com"))  # no CN -> local-part
    out = parse_attendees(ev)
    assert ("wiggert@firm.nl", "Wiggert Loonstra") in out
    assert ("npd@gmail.com", "npd") in out


def test_parse_attendees_skips_rooms_and_dedupes():
    ev = Event()
    ev.add("attendee", _attendee("mailto:room1@firm.nl", cn="Room 1", cutype="ROOM"))
    ev.add("attendee", _attendee("mailto:dup@firm.nl", cn="Dup"))
    ev.add("attendee", _attendee("MAILTO:DUP@firm.nl", cn="Dup2"))  # same email, different case
    ev.add("organizer", _attendee("mailto:org@firm.nl", cn="Org"))
    out = parse_attendees(ev)
    emails = [e for e, _ in out]
    assert "room1@firm.nl" not in emails          # room skipped
    assert emails.count("dup@firm.nl") == 1       # deduped case-insensitively
    assert "org@firm.nl" in emails                # organizer included


def test_parse_attendees_empty():
    assert parse_attendees(Event()) == []
