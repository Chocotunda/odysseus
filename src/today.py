"""The /today daily day-planner read model.

Pure, owner-scoped logic that gathers a single day's tasks + meetings for the
/today surface. Tasks use the local-string convention (planned_day/planned_start
'HH:MM', due_date 'YYYY-MM-DD'); only meetings need real datetime math, for which
we reuse the calendar feature's rrule expansion. Areas are resolved via the
reverse-Link (in_area) spine, never a per-row column.
"""
from datetime import datetime, timedelta
from typing import Optional

from core.hub_models import PlanItem, Area, Link
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


def _meetings_for_day(db, owner, day):
    # Reuse the calendar feature's range + rrule expansion so recurring
    # meetings (standups, 1:1s) correctly land on the day. tz handling matches
    # the calendar's own list_events (naive-local windows; is_utc honored on
    # serialization). This is the one place we use real datetime math.
    from routes.calendar_routes import _expand_rrule
    start_dt = datetime.strptime(f"{day} 00:00", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(days=1)

    from src.hub_calendar import owner_calendar_ids, events_in_window
    cal_ids = owner_calendar_ids(db, owner)
    if not cal_ids:
        return []
    rows = events_in_window(db, cal_ids, start_dt, end_dt)
    out = []
    for ev in rows:
        for d in _expand_rrule(ev, start_dt, end_dt):
            d.setdefault("series_uid", ev.uid)
            d["area_id"] = None
            d["area_name"] = None
            d["area_color"] = None
            out.append(d)
    out.sort(key=lambda m: (not m.get("all_day", False), str(m.get("dtstart") or "")))
    return out


def _attach_areas(db, owner, task_dicts, meeting_dicts):
    pairs = [(NODE_TASK, t["id"]) for t in task_dicts]
    pairs += [(NODE_MEETING, m.get("series_uid") or m.get("uid")) for m in meeting_dicts]
    if not pairs:
        return
    from_ids = list({pid for _, pid in pairs})
    edges = (db.query(Link)
             .filter(Link.owner == owner, Link.rel == REL_IN_AREA,
                     Link.from_type.in_([NODE_TASK, NODE_MEETING]),
                     Link.from_id.in_(from_ids))
             .all())
    area_of = {(e.from_type, e.from_id): e.to_id for e in edges}
    aids = set(area_of.values())
    areas = {a.id: a for a in db.query(Area).filter(Area.id.in_(aids)).all()} if aids else {}

    def _stamp(d, node_type, node_id):
        aid = area_of.get((node_type, node_id))
        a = areas.get(aid) if aid else None
        if a:
            d["area_id"], d["area_name"], d["area_color"] = a.id, a.name, a.color

    for t in task_dicts:
        _stamp(t, NODE_TASK, t["id"])
    for m in meeting_dicts:
        _stamp(m, NODE_MEETING, m.get("series_uid") or m.get("uid"))
