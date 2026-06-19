# routes/today_routes.py
"""The /today daily day-planner API — HTTP-thin. Logic lives in src/today.py."""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, PlanItem, Person, Note, Area
from src.auth_helpers import require_user
from src import today as today_logic
from src import links as L

logger = logging.getLogger(__name__)


def _valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _valid_time(s: str) -> bool:
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except (ValueError, TypeError):
        return False


class ScheduleBody(BaseModel):
    planned_day: str
    planned_start: Optional[str] = None


def setup_today_routes() -> APIRouter:
    router = APIRouter(prefix="/api/today", tags=["today"])

    def _owner(request: Request) -> Optional[str]:
        return require_user(request) or None

    def _load_task(db, request: Request, task_id: str) -> PlanItem:
        owner = _owner(request)
        t = db.query(PlanItem).filter(PlanItem.id == task_id).first()
        if not t or (owner is not None and t.owner != owner):
            raise HTTPException(status_code=404, detail="task not found")
        return t

    @router.get("")
    def get_today(request: Request, day: str = ""):
        owner = _owner(request)
        if day and not _valid_date(day):
            raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
        db = SessionLocal()
        try:
            return today_logic.day_view(db, owner, day or today_logic._local_today())
        finally:
            db.close()

    @router.get("/task/{task_id}")
    def task_detail(request: Request, task_id: str):
        db = SessionLocal()
        try:
            t = _load_task(db, request, task_id)
            owner = t.owner
            people: List[Dict[str, Any]] = []
            for e in L.links_from(db, owner, L.NODE_TASK, t.id, rel=L.REL_ABOUT):
                if e.to_type == L.NODE_PERSON:
                    p = db.query(Person).filter(Person.id == e.to_id).first()
                    if p:
                        people.append({"id": p.id, "name": p.name, "role": p.role})
            source_note = None
            for e in L.links_from(db, owner, L.NODE_TASK, t.id, rel=L.REL_FROM_NOTE):
                n = db.query(Note).filter(Note.id == e.to_id).first()
                if n:
                    source_note = {"id": n.id, "title": n.title}
                    break
            area = None
            for e in L.links_from(db, owner, L.NODE_TASK, t.id, rel=L.REL_IN_AREA):
                a = db.query(Area).filter(Area.id == e.to_id).first()
                if a:
                    area = {"id": a.id, "name": a.name, "color": a.color}
                    break
            return {
                "task": {"id": t.id, "title": t.title, "notes": t.notes,
                         "planned_day": t.planned_day, "planned_start": t.planned_start,
                         "due_date": t.due_date, "priority": t.priority,
                         "estimate_minutes": t.estimate_minutes, "status": t.status},
                "people": people, "source_note": source_note, "area": area,
            }
        finally:
            db.close()

    @router.post("/task/{task_id}/schedule")
    def schedule_task(request: Request, task_id: str, body: ScheduleBody):
        if not _valid_date(body.planned_day):
            raise HTTPException(status_code=400, detail="planned_day must be YYYY-MM-DD")
        if body.planned_start is not None and not _valid_time(body.planned_start):
            raise HTTPException(status_code=400, detail="planned_start must be HH:MM")
        db = SessionLocal()
        try:
            t = _load_task(db, request, task_id)
            t.planned_day = body.planned_day
            t.planned_start = body.planned_start
            db.commit()
            db.refresh(t)
            return {"id": t.id, "planned_day": t.planned_day, "planned_start": t.planned_start}
        finally:
            db.close()

    return router
