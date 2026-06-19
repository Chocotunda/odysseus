# routes/area_routes.py
"""Areas API — first-class life-area nodes for the management hub. HTTP-thin."""
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, Area
from src.auth_helpers import require_user
from src.areas import ensure_seeded_areas

logger = logging.getLogger(__name__)


class AreaCreate(BaseModel):
    name: str = ""
    color: Optional[str] = None


class AreaUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None
    archived: Optional[bool] = None


def _area_to_dict(a: Area) -> Dict[str, Any]:
    return {"id": a.id, "name": a.name, "color": a.color,
            "sort_order": a.sort_order, "archived": bool(a.archived)}


def setup_area_routes():
    router = APIRouter(prefix="/api/areas", tags=["areas"])

    def _owner(request: Request) -> Optional[str]:
        return require_user(request) or None

    def _load(db, request: Request, area_id: str) -> Area:
        owner = _owner(request)
        a = db.query(Area).filter(Area.id == area_id).first()
        if not a or (owner is not None and a.owner != owner):
            raise HTTPException(status_code=404, detail="area not found")
        return a

    @router.get("")
    def list_areas(request: Request):
        owner = _owner(request)
        db = SessionLocal()
        try:
            ensure_seeded_areas(db, owner)
            q = db.query(Area).filter(Area.archived == False)  # noqa: E712
            if owner is not None:
                q = q.filter(Area.owner == owner)
            rows = q.order_by(Area.sort_order, Area.name).all()
            return {"areas": [_area_to_dict(a) for a in rows]}
        finally:
            db.close()

    @router.post("")
    def create_area(request: Request, body: AreaCreate):
        owner = _owner(request)
        db = SessionLocal()
        try:
            a = Area(id=str(uuid.uuid4()), owner=owner,
                     name=(body.name or "").strip(), color=body.color)
            db.add(a)
            db.commit()
            return _area_to_dict(a)
        finally:
            db.close()

    @router.get("/{area_id}")
    def get_area(request: Request, area_id: str):
        db = SessionLocal()
        try:
            return _area_to_dict(_load(db, request, area_id))
        finally:
            db.close()

    @router.put("/{area_id}")
    def update_area(request: Request, area_id: str, body: AreaUpdate):
        db = SessionLocal()
        try:
            a = _load(db, request, area_id)
            for field in ("name", "color", "sort_order", "archived"):
                val = getattr(body, field)
                if val is not None:
                    setattr(a, field, val)
            db.commit()
            return _area_to_dict(a)
        finally:
            db.close()

    @router.delete("/{area_id}")
    def delete_area(request: Request, area_id: str):
        from src.links import remove_links_for, NODE_AREA
        db = SessionLocal()
        try:
            a = _load(db, request, area_id)
            remove_links_for(db, a.owner, NODE_AREA, a.id)
            db.delete(a)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    return router
