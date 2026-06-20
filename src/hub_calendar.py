"""Calendar read-seam for hub code.

Centralizes every hub reference to upstream's CalendarCal / CalendarEvent models
and their fields, so an upstream schema change to the calendar models hits ONE
hub file instead of every hub query. (B2 / agnostic-core boundary; see
docs/superpowers/specs/2026-06-20-hermes-odysseus-hybrid-direction.md)
"""
from sqlalchemy import or_, and_

from core.database import CalendarCal, CalendarEvent


def owner_calendar_ids(db, owner):
    """Calendar ids visible to `owner` (all calendars when owner is None)."""
    cq = db.query(CalendarCal)
    if owner is not None:
        cq = cq.filter(CalendarCal.owner == owner)
    return [c.id for c in cq.all()]


def events_in_window(db, cal_ids, start_dt, end_dt):
    """Non-cancelled events touching [start_dt, end_dt). Mirrors
    calendar_routes.list_events: non-recurring events must overlap the window;
    recurring rows match on dtstart < end alone (caller expands the rrule to the
    in-window occurrences)."""
    if not cal_ids:
        return []
    return (db.query(CalendarEvent)
            .filter(
                CalendarEvent.calendar_id.in_(cal_ids),
                CalendarEvent.status != "cancelled",
                or_(
                    and_(or_(CalendarEvent.rrule == "", CalendarEvent.rrule.is_(None)),
                         CalendarEvent.dtstart < end_dt,
                         CalendarEvent.dtend > start_dt),
                    and_(CalendarEvent.rrule.isnot(None),
                         CalendarEvent.rrule != "",
                         CalendarEvent.dtstart < end_dt),
                ))
            .all())


def events_by_uids(db, uids):
    """Calendar events matching a set of uids — used by reverse-Link entity pages
    (Person / Area dashboards) to resolve their linked meetings."""
    if not uids:
        return []
    return db.query(CalendarEvent).filter(CalendarEvent.uid.in_(uids)).all()


def expand_event_in_window(ev, start_dt, end_dt):
    """Expand a CalendarEvent into its in-window occurrence dicts. The single hub
    dependency on the calendar feature's rrule expansion (routes.calendar_routes)."""
    from routes.calendar_routes import _expand_rrule
    return _expand_rrule(ev, start_dt, end_dt)
