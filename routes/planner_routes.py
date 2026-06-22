# routes/planner_routes.py
"""Planner API — user-facing tasks / daily planning.

Deliberately separate from task_routes.py (the scheduler's ScheduledTask/TaskRun
automation). HTTP-thin: owner-scoping and request shaping live here; business
logic (AI capture, surfacing, scheduling) lives in src/planner_*.py.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func

from core.database import SessionLocal, utcnow_naive
from core.hub_models import PlanItem, PlanProject
from src.auth_helpers import require_user
from src import planner_ai

logger = logging.getLogger(__name__)


def _next_seq_global(db, owner: Optional[str]) -> int:
    current = db.query(func.max(PlanItem.seq)).filter(PlanItem.owner == owner).scalar()
    return (current or 0) + 1


# Float-gap step for ordinal allocation (drag-reorder inserts the midpoint;
# adjacent gaps shrinking below 2 trigger a rebalance — added with reorder).
ORDINAL_GAP = 1024.0

# API-token scope gates (browser/cookie sessions bypass these — they auth by
# cookie). Reads accept either todos scope; writes require todos:write.
TODO_READ_SCOPES = {"todos:read", "todos:write"}
TODO_WRITE_SCOPES = {"todos:write"}


class PlanItemCreate(BaseModel):
    id: Optional[str] = None
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


class ReorderBody(BaseModel):
    before_id: Optional[str] = None   # item directly above the drop point (None = top)
    after_id: Optional[str] = None    # item directly below the drop point (None = bottom)


_VALID_PRIORITY = {"none", "normal", "important", "urgent"}
_VALID_STATUS = {"open", "in_progress", "done", "cancelled"}


class PlanItemPatch(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    planned_day: Optional[str] = None
    due_date: Optional[str] = None
    planned_start: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    estimate_minutes: Optional[int] = None
    project_id: Optional[str] = None
    ordinal: Optional[float] = None
    source_note_id: Optional[str] = None
    source_event_id: Optional[str] = None
    person_id: Optional[str] = None


def _item_to_dict(item: PlanItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "notes": item.notes,
        "planned_day": item.planned_day,
        "planned_start": item.planned_start,
        "due_date": item.due_date,
        "priority": item.priority,
        "status": item.status,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "deleted": item.deleted_at is not None,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
        "estimate_minutes": item.estimate_minutes,
        "ordinal": item.ordinal,
        "seq": item.seq,
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


def _ordinal_between(a: Optional[float], b: Optional[float]) -> float:
    # TODO: rebalance a bucket if a midpoint gap collapses (~50 same-gap inserts
    # before Double precision exhausts) — matches TideCore's Ordinal TODO.
    if a is None and b is None:
        return 0.0
    if a is None:
        return b - ORDINAL_GAP
    if b is None:
        return a + ORDINAL_GAP
    return (a + b) / 2


def _parse_since(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Invalid 'since' timestamp (expected ISO-8601)")
    # stored values are naive UTC (utcnow_naive); normalize tz-aware input to naive UTC.
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


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
        item.seq = _next_seq_global(db, owner)
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
        if item.deleted_at is not None:
            raise HTTPException(404, "Task not found")
        return item

    def _next_ordinal(db, user: Optional[str]) -> int:
        current = db.query(func.max(PlanItem.ordinal)).filter(
            PlanItem.owner == user, PlanItem.deleted_at.is_(None)
        ).scalar()
        return (current or 0) + ORDINAL_GAP

    def _next_seq(db, user: Optional[str]) -> int:
        # Per-owner monotonic. NOT filtered by deleted_at — tombstones occupy seq
        # space and a delete itself bumps seq so the change feed carries it.
        return _next_seq_global(db, user)

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
            q = q.filter(PlanItem.deleted_at.is_(None))
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

    # --- CHANGES (delta sync): rows updated since the watermark, incl. tombstones ---
    @router.get("/items/changes")
    def list_changes(request: Request, since: Optional[str] = None):
        user = _owner(request, TODO_READ_SCOPES)
        since_dt = _parse_since(since)
        db = SessionLocal()
        try:
            q = db.query(PlanItem)
            if user is not None:
                q = q.filter(PlanItem.owner == user)
            if since_dt is not None:
                q = q.filter(PlanItem.updated_at >= since_dt)   # inclusive; client upserts by id
            items = q.order_by(PlanItem.updated_at.asc()).all()
            dicts = [_item_to_dict(it) for it in items]
            cursor = max((it.updated_at for it in items), default=None)
            cursor_iso = cursor.isoformat() if cursor else utcnow_naive().isoformat()
            return {"items": dicts, "cursor": cursor_iso}
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
            # Idempotent create on a client-supplied id (optimistic-create support).
            if body.id:
                existing = db.query(PlanItem).filter(PlanItem.id == body.id).first()
                if existing is not None:
                    if user is not None and existing.owner != user:
                        raise HTTPException(404, "Task not found")  # never collide across owners
                    return _item_to_dict(existing)                  # idempotent: return existing, no dup
            item = PlanItem(
                id=body.id or str(uuid.uuid4()),
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
            item.seq = _next_seq(db, user)
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
            item.seq = _next_seq(db, user)
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
            item.seq = _next_seq(db, user)
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
            item.seq = _next_seq(db, user)
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()

    # --- REORDER (drag): place item between two neighbours (either may be None) ---
    @router.post("/items/{item_id}/reorder")
    def reorder_item(request: Request, item_id: str, body: ReorderBody):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = _get_owned(db, item_id, user)
            before = _get_owned(db, body.before_id, user) if body.before_id else None
            after = _get_owned(db, body.after_id, user) if body.after_id else None
            item.ordinal = _ordinal_between(
                before.ordinal if before else None,
                after.ordinal if after else None,
            )
            item.seq = _next_seq(db, user)
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()

    # --- PATCH (partial update; absent key != explicit null) ---
    @router.patch("/items/{item_id}")
    def patch_item(request: Request, item_id: str, body: PlanItemPatch):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = _get_owned(db, item_id, user)
            fields = body.model_dump(exclude_unset=True)   # only keys the client sent
            if "priority" in fields and fields["priority"] not in _VALID_PRIORITY:
                fields["priority"] = "normal"
            if "status" in fields and fields["status"] not in _VALID_STATUS:
                fields.pop("status")                        # ignore an invalid status rather than corrupt it
            for k, v in fields.items():
                setattr(item, k, v)
            item.seq = _next_seq(db, user)
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()

    # --- DELETE (soft): set the tombstone so the change syncs to clients ---
    @router.delete("/items/{item_id}")
    def delete_item(request: Request, item_id: str):
        user = _owner(request, TODO_WRITE_SCOPES)
        db = SessionLocal()
        try:
            item = _get_owned(db, item_id, user)   # 404s if already tombstoned
            item.deleted_at = utcnow_naive()
            item.seq = _next_seq(db, user)
            db.commit()
            db.refresh(item)
            return _item_to_dict(item)
        finally:
            db.close()

    return router
