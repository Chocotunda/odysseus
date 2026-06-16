# routes/planner_routes.py
"""Planner API — user-facing tasks / daily planning.

Deliberately separate from task_routes.py (the scheduler's ScheduledTask/TaskRun
automation). HTTP-thin: owner-scoping and request shaping live here; business
logic (AI capture, surfacing, scheduling) lives in src/planner_*.py.
"""
import hashlib
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func

from core.database import SessionLocal, PlanItem, PlanProject, utcnow_naive
from src.auth_helpers import require_user
from src import planner_ai

logger = logging.getLogger(__name__)

# Integer-gap step for ordinal allocation (drag-reorder inserts the midpoint;
# adjacent gaps shrinking below 2 trigger a rebalance — added with reorder).
ORDINAL_GAP = 1024

# API-token scope gates (browser/cookie sessions bypass these — they auth by
# cookie). Reads accept either todos scope; writes require todos:write.
TODO_READ_SCOPES = {"todos:read", "todos:write"}
TODO_WRITE_SCOPES = {"todos:write"}


class PlanItemCreate(BaseModel):
    title: str = ""
    notes: Optional[str] = None
    planned_day: Optional[str] = None
    due_date: Optional[str] = None
    priority: Optional[str] = "normal"
    estimate_minutes: Optional[int] = None
    project_id: Optional[str] = None
    source: Optional[str] = "user"
    session_id: Optional[str] = None
    source_note_id: Optional[str] = None
    source_event_id: Optional[str] = None
    person_id: Optional[str] = None


class PlanItemPlan(BaseModel):
    planned_day: Optional[str] = None


class CaptureBody(BaseModel):
    text: str = ""


def _item_to_dict(item: PlanItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "notes": item.notes,
        "planned_day": item.planned_day,
        "due_date": item.due_date,
        "priority": item.priority,
        "status": item.status,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "estimate_minutes": item.estimate_minutes,
        "ordinal": item.ordinal,
        "project_id": item.project_id,
        "source": item.source,
        "source_note_id": item.source_note_id,
        "source_event_id": item.source_event_id,
        "person_id": item.person_id,
        # True once the background AI pass has run (success OR handled failure),
        # so the client knows when to stop polling a freshly-captured item.
        "ai_enriched": item.ai_content_hash is not None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


async def enrich_item(item_id: str, text: str, owner: Optional[str]) -> None:
    """Background enrichment of a captured item with AI-parsed fields.

    Runs AFTER the capture response is sent, so the UI never blocks on the
    model (or a model swap). Best-effort: a failure leaves the raw item intact.
    Always stamps ai_content_hash so the client's poll terminates either way.
    """
    db = SessionLocal()
    try:
        item = db.query(PlanItem).filter(PlanItem.id == item_id).first()
        if not item or (owner is not None and item.owner != owner):
            return
        try:
            projects = db.query(PlanProject).filter(PlanProject.owner == owner).all()
            valid_ids = {p.id for p in projects}
            raw = await planner_ai.parse_capture(
                text, [{"id": p.id, "name": p.name} for p in projects], owner=owner,
            )
            fields = planner_ai.coerce_capture(raw, valid_ids)
            if fields["title"]:
                item.title = fields["title"]
            item.priority = fields["priority"]
            if fields["estimate_minutes"] is not None:
                item.estimate_minutes = fields["estimate_minutes"]
            if fields["project_id"]:
                item.project_id = fields["project_id"]
            if fields["due_date"]:
                item.due_date = fields["due_date"]
        except Exception:
            logger.exception("planner enrichment failed; keeping raw item")
        item.ai_content_hash = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
        db.commit()
    finally:
        db.close()


def setup_planner_routes(task_scheduler=None):
    router = APIRouter(prefix="/api/planner", tags=["planner"])

    def _owner(request: Request, allowed: set) -> Optional[str]:
        # Resolve the data owner, honoring API-token scopes (mirrors
        # routes/codex_routes.py:_scope_owner). Bearer-token callers must carry
        # one of `allowed` and resolve to their token's owner; everyone else
        # falls back to require_user (which still fails closed for stray tokens),
        # coercing "" -> None in single-user mode so the ownership gate below
        # behaves.
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

    def _get_owned(db, item_id: str, user: Optional[str]) -> PlanItem:
        # SECURITY: strict ownership gate written against the coerced `user`
        # (require_user returns "" not None; _owner coerces it). A missing,
        # null-owner, or cross-owner row 404s (not 403 — don't leak existence).
        item = db.query(PlanItem).filter(PlanItem.id == item_id).first()
        if not item:
            raise HTTPException(404, "Task not found")
        if user is not None and item.owner != user:
            raise HTTPException(404, "Task not found")
        return item

    def _next_ordinal(db, user: Optional[str]) -> int:
        current = db.query(func.max(PlanItem.ordinal)).filter(PlanItem.owner == user).scalar()
        return (current or 0) + ORDINAL_GAP

    # --- LIST ---
    @router.get("/items")
    def list_items(
        request: Request,
        day: Optional[str] = None,
        status: Optional[str] = None,
        project: Optional[str] = None,
    ):
        user = _owner(request, TODO_READ_SCOPES)
        db = SessionLocal()
        try:
            q = db.query(PlanItem)
            # SECURITY: exact-owner only (no include_shared). Background actions
            # can mint null-owner rows; in multi-user mode include_shared would
            # leak them to every user.
            if user is not None:
                q = q.filter(PlanItem.owner == user)
            if day is not None:
                q = q.filter(PlanItem.planned_day == day)
            if status is not None:
                q = q.filter(PlanItem.status == status)
            if project is not None:
                q = q.filter(PlanItem.project_id == project)
            items = q.order_by(PlanItem.ordinal.asc(), PlanItem.created_at.desc()).all()
            return {"items": [_item_to_dict(it) for it in items]}
        finally:
            db.close()

    # --- GET ONE ---
    @router.get("/items/{item_id}")
    def get_item(request: Request, item_id: str):
        user = _owner(request, TODO_READ_SCOPES)
        db = SessionLocal()
        try:
            return _item_to_dict(_get_owned(db, item_id, user))
        finally:
            db.close()

    # --- CREATE ---
    @router.post("/items")
    def create_item(request: Request, body: PlanItemCreate):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = PlanItem(
                id=str(uuid.uuid4()),
                owner=user,                         # always a concrete owner (or None in single-user)
                title=body.title or "",
                notes=body.notes,
                planned_day=body.planned_day,
                due_date=body.due_date,
                priority=body.priority or "normal",
                status="open",
                estimate_minutes=body.estimate_minutes,
                ordinal=_next_ordinal(db, user),
                project_id=body.project_id,
                source=body.source or "user",
                session_id=body.session_id,
                source_note_id=body.source_note_id,
                source_event_id=body.source_event_id,
                person_id=body.person_id,
            )
            db.add(item)
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()

    # --- COMPLETE ---
    @router.post("/items/{item_id}/complete")
    def complete_item(request: Request, item_id: str):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = _get_owned(db, item_id, user)
            item.status = "done"
            item.completed_at = utcnow_naive()
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()

    # --- CAPTURE (NL → structured; returns instantly, enriches in background) ---
    @router.post("/capture")
    async def capture(request: Request, body: CaptureBody, background_tasks: BackgroundTasks):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            # Create + return the item IMMEDIATELY from the raw text. The model
            # (and any swap it triggers) runs AFTER the response, in the
            # background — capture never blocks on it.
            item = PlanItem(
                id=str(uuid.uuid4()),
                owner=user,
                title=(body.text or "").strip(),
                status="open",
                source="capture",
                ordinal=_next_ordinal(db, user),
            )
            db.add(item)
            db.commit()
            db.refresh(item)
            out = _item_to_dict(item)
        finally:
            db.close()
        background_tasks.add_task(enrich_item, out["id"], body.text, user)
        out["enriching"] = True
        return out

    # --- PLAN (set/clear the day; NULL day = backlog) ---
    @router.post("/items/{item_id}/plan")
    def plan_item(request: Request, item_id: str, body: PlanItemPlan):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = _get_owned(db, item_id, user)
            item.planned_day = body.planned_day
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()

    return router
