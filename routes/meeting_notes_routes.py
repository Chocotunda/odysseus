# routes/meeting_notes_routes.py
"""Meeting-notes API — the hub composer surface. HTTP-thin over src/meeting_notes."""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, CalendarCal, CalendarEvent
from src.auth_helpers import require_user
from src import meeting_notes as MN

logger = logging.getLogger(__name__)


class MeetingNoteSave(BaseModel):
    title: str = ""
    content: str = ""
    action_items: List[Dict[str, Any]] = []
    person_id: Optional[str] = None
    event_uid: Optional[str] = None
    make_tasks: bool = False
    note_id: Optional[str] = None
    area_id: Optional[str] = None


class PromoteBody(BaseModel):
    title: str = ""
    person_id: Optional[str] = None
    due_date: Optional[str] = None
    area_id: Optional[str] = None


def setup_meeting_notes_routes():
    router = APIRouter(prefix="/api/meeting-notes", tags=["meeting-notes"])

    def _scope_owner(request: Request, allowed: set) -> Optional[str]:
        """Resolve owner, honouring API-token scopes.

        Bearer-token callers must carry one of the scopes in `allowed`; any
        other token gets 403. Cookie-session callers fall through to
        require_user so existing browser behaviour is unchanged.
        """
        if getattr(request.state, "api_token", False):
            scopes = set(getattr(request.state, "api_token_scopes", []) or [])
            if not scopes.intersection(allowed):
                required = " or ".join(sorted(allowed))
                raise HTTPException(403, f"API token missing required scope: {required}")
            owner = getattr(request.state, "api_token_owner", None)
            if not owner:
                raise HTTPException(403, "API token has no owner")
            return owner
        return require_user(request) or None

    @router.get("/meetings")
    def list_meetings(request: Request, q: str = ""):
        """Owner-scoped recent calendar events for the meeting picker."""
        owner = _scope_owner(request, {"notes:read", "notes:write"})
        db = SessionLocal()
        try:
            cal_ids = [c.id for c in db.query(CalendarCal).filter(
                (CalendarCal.owner == owner) if owner is not None else True).all()]
            query = db.query(CalendarEvent).filter(CalendarEvent.calendar_id.in_(cal_ids))
            if q:
                query = query.filter(CalendarEvent.summary.ilike(f"%{q}%"))
            rows = query.order_by(CalendarEvent.dtstart.desc()).limit(30).all()
            return {"meetings": [{"uid": e.uid, "summary": e.summary,
                                  "dtstart": e.dtstart.isoformat() if e.dtstart else None} for e in rows]}
        finally:
            db.close()

    @router.post("")
    def save(request: Request, body: MeetingNoteSave, background: BackgroundTasks):
        owner = _scope_owner(request, {"notes:write"})
        db = SessionLocal()
        try:
            result = MN.save_meeting_note(
                db, owner, title=body.title, content=body.content,
                action_items=body.action_items, person_id=body.person_id,
                event_uid=body.event_uid, make_tasks=body.make_tasks, note_id=body.note_id,
                area_id=body.area_id)
        finally:
            db.close()
        background.add_task(MN.enrich_meeting_note, result["note"]["id"], owner)
        return result

    @router.get("/{note_id}")
    def get_note(request: Request, note_id: str):
        from core.database import Note
        owner = _scope_owner(request, {"notes:read", "notes:write"})
        db = SessionLocal()
        try:
            note = db.query(Note).filter(Note.id == note_id, Note.deleted_at.is_(None)).first()
            if not note or (owner is not None and note.owner != owner):
                raise HTTPException(status_code=404, detail="note not found")
            return MN._note_dict(note)
        finally:
            db.close()

    @router.post("/{note_id}/promote")
    def promote(request: Request, note_id: str, body: PromoteBody):
        owner = _scope_owner(request, {"notes:write"})
        db = SessionLocal()
        try:
            return MN.promote_action_item(db, owner, note_id, body.title,
                                          person_id=body.person_id, due_date=body.due_date,
                                          area_id=body.area_id)
        finally:
            db.close()

    return router
