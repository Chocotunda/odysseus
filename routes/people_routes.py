# routes/people_routes.py
"""People API — first-class Person nodes in the management hub.

HTTP-thin; owner-scoping lives here, link/aggregation logic in src/. People are
single-user entities the owner tracks (no logins)."""
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, Person
from src.auth_helpers import require_user

logger = logging.getLogger(__name__)


class PersonCreate(BaseModel):
    name: str = ""
    email: Optional[str] = None
    role: Optional[str] = None
    contact_uid: Optional[str] = None


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    contact_uid: Optional[str] = None
    archived: Optional[bool] = None


def _person_to_dict(p: Person) -> Dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "email": p.email, "role": p.role,
        "contact_uid": p.contact_uid, "archived": bool(p.archived),
    }


def setup_people_routes():
    router = APIRouter(prefix="/api/people", tags=["people"])

    def _owner(request: Request) -> Optional[str]:
        return require_user(request) or None

    def _load(db, request: Request, person_id: str) -> Person:
        owner = _owner(request)
        p = db.query(Person).filter(Person.id == person_id).first()
        if not p or (owner is not None and p.owner != owner):
            raise HTTPException(status_code=404, detail="person not found")
        return p

    @router.get("")
    def list_people(request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            q = db.query(Person).filter(Person.archived == False)  # noqa: E712
            if owner is not None:
                q = q.filter(Person.owner == owner)
            rows = q.order_by(Person.name).all()
            return {"people": [_person_to_dict(p) for p in rows]}
        finally:
            db.close()

    @router.post("")
    def create_person(request: Request, body: PersonCreate):
        owner = _owner(request)
        db = SessionLocal()
        try:
            p = Person(id=str(uuid.uuid4()), owner=owner, name=(body.name or "").strip(),
                       email=body.email, role=body.role, contact_uid=body.contact_uid)
            db.add(p)
            db.commit()
            return _person_to_dict(p)
        finally:
            db.close()

    @router.get("/{person_id}")
    def get_person(request: Request, person_id: str):
        db = SessionLocal()
        try:
            return _person_to_dict(_load(db, request, person_id))
        finally:
            db.close()

    @router.put("/{person_id}")
    def update_person(request: Request, person_id: str, body: PersonUpdate):
        db = SessionLocal()
        try:
            p = _load(db, request, person_id)
            for field in ("name", "email", "role", "contact_uid", "archived"):
                val = getattr(body, field)
                if val is not None:
                    setattr(p, field, val)
            db.commit()
            return _person_to_dict(p)
        finally:
            db.close()

    @router.delete("/{person_id}")
    def delete_person(request: Request, person_id: str):
        from src.links import remove_links_for, NODE_PERSON
        db = SessionLocal()
        try:
            p = _load(db, request, person_id)
            remove_links_for(db, p.owner, NODE_PERSON, p.id)
            db.delete(p)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.get("/{person_id}/page")
    def person_page(request: Request, person_id: str):
        from src.links import links_to, NODE_PERSON, NODE_TASK, NODE_NOTE, NODE_MEETING, REL_ABOUT, REL_NOTE_OF, REL_ATTENDED_BY
        from core.database import PlanItem, Note, CalendarEvent
        db = SessionLocal()
        try:
            person = _load(db, request, person_id)
            owner = person.owner

            task_ids = [e.from_id for e in links_to(db, owner, NODE_PERSON, person_id, rel=REL_ABOUT)
                        if e.from_type == NODE_TASK]
            tasks = (db.query(PlanItem)
                     .filter(PlanItem.id.in_(task_ids), PlanItem.status == "open").all()) if task_ids else []
            # overdue-first: items with a due_date sort before those without, ascending
            tasks.sort(key=lambda t: (t.due_date is None, t.due_date or ""))

            note_ids = [e.from_id for e in links_to(db, owner, NODE_PERSON, person_id, rel=REL_ABOUT)
                        if e.from_type == NODE_NOTE]
            notes = db.query(Note).filter(Note.id.in_(note_ids)).all() if note_ids else []

            mtg_ids = [e.from_id for e in links_to(db, owner, NODE_PERSON, person_id, rel=REL_ATTENDED_BY)
                       if e.from_type == NODE_MEETING]
            meetings = db.query(CalendarEvent).filter(CalendarEvent.uid.in_(mtg_ids)).all() if mtg_ids else []

            return {
                "person": _person_to_dict(person),
                "open_tasks": [{"id": t.id, "title": t.title, "due_date": t.due_date,
                                "priority": t.priority, "source_note_id": t.source_note_id} for t in tasks],
                "meetings": [{"uid": m.uid, "summary": m.summary,
                              "dtstart": m.dtstart.isoformat() if m.dtstart else None} for m in meetings],
                "notes": [{"id": n.id, "title": n.title} for n in notes],
            }
        finally:
            db.close()

    return router
