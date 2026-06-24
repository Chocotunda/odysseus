# routes/area_routes.py
"""Areas API — first-class life-area nodes for the management hub. HTTP-thin."""
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal
from core.hub_models import Area, next_area_seq
from src.auth_helpers import require_user
from src.areas import ensure_seeded_areas

logger = logging.getLogger(__name__)

AREA_READ_SCOPES = {"areas:read", "areas:write"}
AREA_WRITE_SCOPES = {"areas:write"}


class AreaCreate(BaseModel):
    name: str = ""
    color: Optional[str] = None


class AreaUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None
    archived: Optional[bool] = None


def _area_to_dict(a: Area) -> Dict[str, Any]:
    return {
        "id": a.id, "name": a.name, "color": a.color,
        "sort_order": a.sort_order, "archived": bool(a.archived),
        "seq": a.seq,
        "deleted": a.deleted_at is not None,
        "deleted_at": a.deleted_at.isoformat() if a.deleted_at else None,
    }


def setup_area_routes():
    router = APIRouter(prefix="/api/areas", tags=["areas"])

    def _owner(request: Request, allowed: set) -> Optional[str]:
        # Token callers must carry one of `allowed`; browser sessions bypass.
        if getattr(request.state, "api_token", False):
            scopes = set(getattr(request.state, "api_token_scopes", []) or [])
            if not scopes.intersection(allowed):
                raise HTTPException(403, f"API token missing required scope: {' or '.join(sorted(allowed))}")
            owner = getattr(request.state, "api_token_owner", None)
            if not owner:
                raise HTTPException(403, "API token has no owner")
            return owner
        return require_user(request) or None

    def _load(db, request: Request, area_id: str, allowed: set) -> Area:
        owner = _owner(request, allowed)
        a = db.query(Area).filter(Area.id == area_id).first()
        if not a or (owner is not None and a.owner != owner):
            raise HTTPException(status_code=404, detail="area not found")
        return a

    def _load_live(db, request: Request, area_id: str, allowed: set) -> Area:
        """Load an Area, raising 404 for both missing and soft-deleted rows."""
        owner = _owner(request, allowed)
        a = (db.query(Area)
             .filter(Area.id == area_id, Area.deleted_at.is_(None))
             .first())
        if not a or (owner is not None and a.owner != owner):
            raise HTTPException(status_code=404, detail="area not found")
        return a

    @router.get("")
    def list_areas(request: Request):
        owner = _owner(request, AREA_READ_SCOPES)
        db = SessionLocal()
        try:
            ensure_seeded_areas(db, owner)
            q = db.query(Area).filter(
                Area.archived == False,        # noqa: E712
                Area.deleted_at.is_(None),     # exclude tombstones
            )
            if owner is not None:
                q = q.filter(Area.owner == owner)
            rows = q.order_by(Area.sort_order, Area.name).all()
            return {"areas": [_area_to_dict(a) for a in rows]}
        finally:
            db.close()

    @router.post("")
    def create_area(request: Request, body: AreaCreate):
        owner = _owner(request, AREA_WRITE_SCOPES)
        db = SessionLocal()
        try:
            a = Area(id=str(uuid.uuid4()), owner=owner,
                     name=(body.name or "").strip(), color=body.color)
            a.seq = next_area_seq(db, owner)
            db.add(a)
            db.commit()
            return _area_to_dict(a)
        finally:
            db.close()

    # --- CHANGES (delta sync): rows with seq > since, INCLUDING tombstones ---
    @router.get("/changes")
    def list_changes(request: Request, since: Optional[int] = None):
        user = _owner(request, AREA_READ_SCOPES)
        db = SessionLocal()
        try:
            q = db.query(Area)
            if user is not None:
                q = q.filter(Area.owner == user)
            q = q.filter(Area.seq.isnot(None))    # NULL seq never appears in the feed
            if since is not None:
                q = q.filter(Area.seq > since)     # exclusive; seq is unique-per-owner monotonic
            # IMPORTANT: do NOT filter deleted_at here — the client must learn deletes.
            areas = q.order_by(Area.seq.asc()).all()
            dicts = [_area_to_dict(a) for a in areas]
            cursor = max((a.seq for a in areas), default=(since or 0))
            return {"areas": dicts, "cursor": cursor}
        finally:
            db.close()

    @router.get("/{area_id}")
    def get_area(request: Request, area_id: str):
        db = SessionLocal()
        try:
            return _area_to_dict(_load_live(db, request, area_id, AREA_READ_SCOPES))
        finally:
            db.close()

    @router.put("/{area_id}")
    def update_area(request: Request, area_id: str, body: AreaUpdate):
        db = SessionLocal()
        try:
            a = _load_live(db, request, area_id, AREA_WRITE_SCOPES)
            for field in ("name", "color", "sort_order", "archived"):
                val = getattr(body, field)
                if val is not None:
                    setattr(a, field, val)
            a.seq = next_area_seq(db, a.owner)
            db.commit()
            return _area_to_dict(a)
        finally:
            db.close()

    @router.delete("/{area_id}")
    def delete_area(request: Request, area_id: str):
        from datetime import datetime, timezone
        from src.links import remove_links_for, NODE_AREA
        db = SessionLocal()
        try:
            a = _load_live(db, request, area_id, AREA_WRITE_SCOPES)
            remove_links_for(db, a.owner, NODE_AREA, a.id)
            # soft-delete: stamp tombstone + advance seq (do NOT db.delete)
            a.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            a.seq = next_area_seq(db, a.owner)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.get("/{area_id}/page")
    def area_page(request: Request, area_id: str):
        from src.links import (links_to, NODE_AREA, NODE_PERSON, NODE_TASK,
                               NODE_NOTE, NODE_MEETING, REL_IN_AREA)
        from core.database import Note
        from core.hub_models import Person, PlanItem
        from src.hub_calendar import events_by_uids
        db = SessionLocal()
        try:
            area = _load_live(db, request, area_id, AREA_READ_SCOPES)
            owner = area.owner
            by_type: Dict[str, list] = {}
            for e in links_to(db, owner, NODE_AREA, area_id, rel=REL_IN_AREA):
                by_type.setdefault(e.from_type, []).append(e.from_id)

            pids = by_type.get(NODE_PERSON, [])
            # exclude tombstoned people from area page
            people = (db.query(Person)
                      .filter(Person.id.in_(pids), Person.deleted_at.is_(None))
                      .all()) if pids else []

            tids = by_type.get(NODE_TASK, [])
            tasks = (db.query(PlanItem)
                     .filter(PlanItem.id.in_(tids), PlanItem.status == "open",
                             PlanItem.deleted_at.is_(None)).all()) if tids else []
            tasks.sort(key=lambda t: (t.due_date is None, t.due_date or ""))

            nids = by_type.get(NODE_NOTE, [])
            notes = db.query(Note).filter(Note.id.in_(nids), Note.deleted_at.is_(None)).all() if nids else []

            mids = by_type.get(NODE_MEETING, [])
            meetings = events_by_uids(db, mids)

            return {
                "area": _area_to_dict(area),
                "people": [{"id": p.id, "name": p.name, "role": p.role} for p in people],
                "open_tasks": [{"id": t.id, "title": t.title, "due_date": t.due_date,
                                "priority": t.priority} for t in tasks],
                "notes": [{"id": n.id, "title": n.title} for n in notes],
                "meetings": [{"uid": m.uid, "summary": m.summary,
                              "dtstart": m.dtstart.isoformat() if m.dtstart else None} for m in meetings],
            }
        finally:
            db.close()

    return router
