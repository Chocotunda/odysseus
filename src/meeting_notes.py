"""Meeting-note save + action-item promotion — the hub's connective glue.

A meeting note is a Note linked to a Meeting (CalendarEvent.uid) and a Person.
Its action items can be promoted into PlanItem tasks that carry the same graph
edges PLUS the existing source_* soft fields (a denormalized cache, so existing
planner code keeps working). The Link table is canonical; the soft fields mirror
it for the single most common back-reference.
"""
import hashlib
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from core.database import Note, SessionLocal
from core.hub_models import PlanItem, next_plan_item_seq
from src import links as L

logger = logging.getLogger(__name__)


def _note_dict(note: Note) -> Dict[str, Any]:
    try:
        items = json.loads(note.items) if note.items else []
    except (ValueError, TypeError):
        items = []
    suggested = []
    try:
        cls = json.loads(note.ai_classification) if note.ai_classification else {}
        suggested = cls.get("suggested_action_items", []) if isinstance(cls, dict) else []
    except (ValueError, TypeError):
        pass
    return {
        "id": note.id, "title": note.title, "content": note.content,
        "items": items, "suggested_action_items": suggested,
        "ai_enriched": note.ai_content_hash is not None,
    }


def _task_dict(item: PlanItem) -> Dict[str, Any]:
    return {
        "id": item.id, "title": item.title, "status": item.status,
        "due_date": item.due_date, "priority": item.priority,
        "source_note_id": item.source_note_id, "source_event_id": item.source_event_id,
        "person_id": item.person_id,
    }


def _max_ordinal(db, owner: Optional[str]) -> int:
    from sqlalchemy import func
    val = db.query(func.max(PlanItem.ordinal)).filter(
        PlanItem.owner == owner, PlanItem.deleted_at.is_(None)
    ).scalar()
    return (val or 0)


def promote_action_item(db, owner: Optional[str], note_id: str, title: str,
                        *, person_id: Optional[str] = None,
                        due_date: Optional[str] = None,
                        area_id: Optional[str] = None) -> Dict[str, Any]:
    """Create a PlanItem from an action item, idempotent on (note, title)."""
    title = (title or "").strip()
    # idempotency: a task already promoted from this note with this title?
    existing_ids = {e.from_id for e in L.links_to(db, owner, L.NODE_NOTE, note_id, rel=L.REL_FROM_NOTE)}
    if existing_ids:
        dup = (db.query(PlanItem)
               .filter(PlanItem.id.in_(existing_ids), PlanItem.title == title,
                       PlanItem.deleted_at.is_(None)).first())
        if dup:
            return _task_dict(dup)

    # look up the originating event uid off the note's note_of edge (for the soft cache)
    event_uid = None
    note_edges = L.links_from(db, owner, L.NODE_NOTE, note_id, rel=L.REL_NOTE_OF)
    if note_edges:
        event_uid = note_edges[0].to_id

    item = PlanItem(id=str(uuid.uuid4()), owner=owner, title=title, status="open",
                    due_date=due_date, ordinal=_max_ordinal(db, owner) + 1024,
                    source="meeting_note", source_note_id=note_id,
                    source_event_id=event_uid, person_id=person_id)
    item.seq = next_plan_item_seq(db, owner)
    db.add(item)
    db.commit()
    L.add_link(db, owner, L.NODE_TASK, item.id, L.REL_FROM_NOTE, L.NODE_NOTE, note_id)
    if person_id:
        L.add_link(db, owner, L.NODE_TASK, item.id, L.REL_ABOUT, L.NODE_PERSON, person_id)
    if area_id:
        L.set_area(db, owner, L.NODE_TASK, item.id, area_id)
    return _task_dict(item)


def save_meeting_note(db, owner: Optional[str], *, title: str = "", content: str = "",
                      action_items: Optional[List[Dict[str, Any]]] = None,
                      person_id: Optional[str] = None, event_uid: Optional[str] = None,
                      make_tasks: bool = False, note_id: Optional[str] = None,
                      area_id: Optional[str] = None) -> Dict[str, Any]:
    action_items = action_items or []
    if note_id:
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note or (owner is not None and note.owner != owner):
            raise ValueError("note not found")
        # TODO(next-slice): when note editing ships, clean stale note_of/about/attended_by
        # edges here (remove_links_for the note) before re-adding, or changing the linked
        # person/meeting on re-save will orphan the old edges.
        note.title, note.content = title, content
        note.items = json.dumps(action_items)
    else:
        note = Note(id=str(uuid.uuid4()), owner=owner, title=title, content=content,
                    items=json.dumps(action_items), note_type="note", source="user")
        db.add(note)
    db.commit()

    if event_uid:
        L.add_link(db, owner, L.NODE_NOTE, note.id, L.REL_NOTE_OF, L.NODE_MEETING, event_uid)
    if person_id:
        L.add_link(db, owner, L.NODE_NOTE, note.id, L.REL_ABOUT, L.NODE_PERSON, person_id)
    if event_uid and person_id:
        L.add_link(db, owner, L.NODE_MEETING, event_uid, L.REL_ATTENDED_BY, L.NODE_PERSON, person_id)
    if area_id:
        L.set_area(db, owner, L.NODE_NOTE, note.id, area_id)

    tasks = []
    if make_tasks:
        for it in action_items:
            if not it.get("done") and (it.get("text") or "").strip():
                tasks.append(promote_action_item(db, owner, note.id, it["text"],
                                                 person_id=person_id, area_id=area_id))
    return {"note": _note_dict(note), "tasks": tasks}


async def enrich_meeting_note(note_id: str, owner: Optional[str]) -> None:
    """Background: extract candidate action items via AI, store them on the Note
    for the UI to confirm. Best-effort; always stamps ai_content_hash so the
    client poll terminates."""
    from src import planner_ai
    from datetime import datetime
    db = SessionLocal()
    try:
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note or (owner is not None and note.owner != owner):
            return
        person_name = ""
        for e in L.links_from(db, owner, L.NODE_NOTE, note_id, rel=L.REL_ABOUT):
            from core.hub_models import Person
            p = db.query(Person).filter(Person.id == e.to_id).first()
            if p:
                person_name = p.name
                break
        try:
            existing = [it.get("text", "") for it in (json.loads(note.items) if note.items else [])]
            raw = await planner_ai.extract_action_items(note.content or "", person_name, owner=owner)
            suggested = planner_ai.coerce_action_items(
                raw, person_name, datetime.now().strftime("%Y-%m-%d"), existing)
            note.ai_classification = json.dumps({"suggested_action_items": suggested})
        except Exception:
            logger.exception("meeting-note enrichment failed; keeping note as-is")
        note.ai_content_hash = hashlib.sha256((note.content or "").encode("utf-8")).hexdigest()
        db.commit()
    finally:
        db.close()
