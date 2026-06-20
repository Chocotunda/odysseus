"""The /today daily day-planner: day_view aggregation + routes."""
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, CalendarCal, CalendarEvent, utcnow_naive
from core.hub_models import PlanItem, Area
from src import links as L
from src import today as T


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_day_view_partitions_tasks():
    db = _db()
    db.add(PlanItem(id="s1", owner="alice", title="Blocked", status="open",
                    planned_day="2026-06-20", planned_start="09:00", estimate_minutes=45))
    db.add(PlanItem(id="u1", owner="alice", title="Anytime", status="open",
                    planned_day="2026-06-20"))                       # no planned_start -> unscheduled
    db.add(PlanItem(id="o1", owner="alice", title="Late", status="open",
                    due_date="2026-06-18"))                          # overdue
    db.add(PlanItem(id="d1", owner="alice", title="Done", status="done",
                    planned_day="2026-06-20", planned_start="10:00"))  # excluded
    db.commit()

    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert [t["id"] for t in v["scheduled_tasks"]] == ["s1"]
    assert [t["id"] for t in v["unscheduled_tasks"]] == ["u1"]
    assert [t["id"] for t in v["overdue_tasks"]] == ["o1"]
    assert v["is_today"] is True
    # capacity = 45 (s1) + 30 default (u1); overdue not counted toward the day
    assert v["capacity_minutes"] == 75


def test_overdue_only_when_viewing_today():
    db = _db()
    db.add(PlanItem(id="o1", owner="alice", title="Late", status="open", due_date="2026-06-18"))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-21", today="2026-06-20")   # viewing a future day
    assert v["overdue_tasks"] == []
    assert v["is_today"] is False


def test_day_view_owner_isolated():
    db = _db()
    db.add(PlanItem(id="s1", owner="alice", title="A", status="open",
                    planned_day="2026-06-20", planned_start="09:00"))
    db.add(PlanItem(id="s2", owner="bob", title="B", status="open",
                    planned_day="2026-06-20", planned_start="09:00"))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert [t["id"] for t in v["scheduled_tasks"]] == ["s1"]


def _on(day, hour):
    return datetime.strptime(f"{day} {hour:02d}:00", "%Y-%m-%d %H:%M")


def test_day_view_includes_meetings_on_day():
    db = _db()
    db.add(CalendarCal(id="c1", owner="alice", name="Cal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Standup",
                         dtstart=_on("2026-06-20", 9), dtend=_on("2026-06-20", 10)))
    db.add(CalendarEvent(uid="m2", calendar_id="c1", summary="Other day",
                         dtstart=_on("2026-06-21", 9), dtend=_on("2026-06-21", 10)))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert [m["summary"] for m in v["meetings"]] == ["Standup"]


def test_day_view_meetings_owner_isolated():
    db = _db()
    db.add(CalendarCal(id="c1", owner="bob", name="Bob cal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Bob mtg",
                         dtstart=_on("2026-06-20", 9), dtend=_on("2026-06-20", 10)))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert v["meetings"] == []


def test_day_view_attaches_area_to_task_and_meeting():
    db = _db()
    db.add(Area(id="aw", owner="alice", name="Work", color="#a60717"))
    db.add(PlanItem(id="s1", owner="alice", title="T", status="open",
                    planned_day="2026-06-20", planned_start="09:00"))
    db.add(CalendarCal(id="c1", owner="alice", name="Cal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Mtg",
                         dtstart=_on("2026-06-20", 11), dtend=_on("2026-06-20", 12)))
    db.commit()
    L.set_area(db, "alice", L.NODE_TASK, "s1", "aw")
    L.set_area(db, "alice", L.NODE_MEETING, "m1", "aw")

    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert v["scheduled_tasks"][0]["area_name"] == "Work"
    assert v["scheduled_tasks"][0]["area_color"] == "#a60717"
    assert v["meetings"][0]["area_name"] == "Work"


def test_day_view_includes_recurring_meeting_from_past():
    db = _db()
    db.add(CalendarCal(id="c1", owner="alice", name="Cal"))
    # weekly series whose BASE dtstart is a week before the viewed day
    db.add(CalendarEvent(uid="rec1", calendar_id="c1", summary="Weekly standup",
                         dtstart=_on("2026-06-13", 9), dtend=_on("2026-06-13", 10),
                         rrule="FREQ=WEEKLY"))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")  # 7 days later = an occurrence
    assert any(m["summary"] == "Weekly standup" for m in v["meetings"])


def test_day_view_excludes_cancelled_meeting():
    db = _db()
    db.add(CalendarCal(id="c1", owner="alice", name="Cal"))
    db.add(CalendarEvent(uid="m1", calendar_id="c1", summary="Cancelled mtg",
                         dtstart=_on("2026-06-20", 9), dtend=_on("2026-06-20", 10),
                         status="cancelled"))
    db.commit()
    v = T.day_view(db, "alice", "2026-06-20", today="2026-06-20")
    assert all(m["summary"] != "Cancelled mtg" for m in v["meetings"])
