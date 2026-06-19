"""The /today daily day-planner read model.

Pure, owner-scoped logic that gathers a single day's tasks + meetings for the
/today surface. Tasks use the local-string convention (planned_day/planned_start
'HH:MM', due_date 'YYYY-MM-DD'); only meetings need real datetime math, for which
we reuse the calendar feature's rrule expansion. Areas are resolved via the
reverse-Link (in_area) spine, never a per-row column.
"""
from datetime import datetime
from typing import Optional

from core.database import PlanItem, CalendarCal, CalendarEvent, Area, Link
from src.links import REL_IN_AREA, NODE_TASK, NODE_MEETING

DEFAULT_ESTIMATE_MIN = 30


def _local_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _est(t: PlanItem) -> int:
    return t.estimate_minutes if t.estimate_minutes else DEFAULT_ESTIMATE_MIN


def _task_dict(t: PlanItem) -> dict:
    return {
        "id": t.id, "title": t.title, "planned_start": t.planned_start,
        "planned_day": t.planned_day, "due_date": t.due_date,
        "priority": t.priority, "estimate_minutes": t.estimate_minutes,
        "status": t.status,
        "area_id": None, "area_name": None, "area_color": None,
    }


def day_view(db, owner: Optional[str], day: str, *, today: Optional[str] = None) -> dict:
    today = today or _local_today()
    is_today = (day == today)

    q = db.query(PlanItem).filter(PlanItem.status == "open")
    if owner is not None:
        q = q.filter(PlanItem.owner == owner)
    open_tasks = q.all()

    scheduled, unscheduled, overdue = [], [], []
    for t in open_tasks:
        if t.planned_day == day and t.planned_start:
            scheduled.append(t)
        elif t.planned_day == day:
            unscheduled.append(t)
        elif is_today and t.due_date and t.due_date < today and t.planned_day != day:
            overdue.append(t)
    scheduled.sort(key=lambda t: (t.planned_start, t.ordinal or 0))
    unscheduled.sort(key=lambda t: (t.ordinal or 0))
    overdue.sort(key=lambda t: (t.due_date, t.ordinal or 0))

    day_tasks = scheduled + unscheduled
    capacity = sum(_est(t) for t in day_tasks)

    meetings = _meetings_for_day(db, owner, day)

    sched_d = [_task_dict(t) for t in scheduled]
    unsched_d = [_task_dict(t) for t in unscheduled]
    overdue_d = [_task_dict(t) for t in overdue]
    _attach_areas(db, owner, sched_d + unsched_d + overdue_d, meetings)

    return {
        "day": day, "is_today": is_today,
        "meetings": meetings,
        "scheduled_tasks": sched_d,
        "unscheduled_tasks": unsched_d,
        "overdue_tasks": overdue_d,
        "capacity_minutes": capacity,
    }


def _meetings_for_day(db, owner, day):     # filled in Task 2
    return []


def _attach_areas(db, owner, task_dicts, meeting_dicts):   # filled in Task 2
    return
