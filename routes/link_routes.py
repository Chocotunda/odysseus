# routes/link_routes.py
"""Links delta-feed API — exposes the polymorphic Link graph spine as a
seq-cursor delta feed so edges sync to the Tide client (mirrors the PlanItem
`/items/changes` contract).

HTTP-thin: owner-scoping lives here, link mutation logic in src/links.py. Links
are server-side side effects of people/area/note writes, so clients get
`links:read` ONLY — there is no client write surface here."""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from core.database import SessionLocal
from core.hub_models import Link
from src.auth_helpers import require_user

logger = logging.getLogger(__name__)

LINKS_READ_SCOPES = {"links:read"}


def _link_to_dict(l: Link) -> Dict[str, Any]:
    return {
        "id": l.id,
        "from_type": l.from_type,
        "from_id": l.from_id,
        "rel": l.rel,
        "to_type": l.to_type,
        "to_id": l.to_id,
        "seq": l.seq,
        "deleted": l.deleted_at is not None,
        "deleted_at": l.deleted_at.isoformat() if l.deleted_at else None,
        "updated_at": l.updated_at.isoformat() if l.updated_at else None,
    }


def setup_link_routes():
    router = APIRouter(prefix="/api/links", tags=["links"])

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

    # --- CHANGES (delta sync): rows with seq > since, INCLUDING tombstones ---
    @router.get("/changes")
    def list_changes(request: Request, since: Optional[int] = None):
        user = _owner(request, LINKS_READ_SCOPES)
        db = SessionLocal()
        try:
            q = db.query(Link)
            if user is not None:
                q = q.filter(Link.owner == user)
            q = q.filter(Link.seq.isnot(None))      # NULL seq never appears in the feed
            if since is not None:
                q = q.filter(Link.seq > since)        # exclusive; seq is unique-per-owner monotonic
            # IMPORTANT: do NOT filter deleted_at here — the client must learn deletes.
            links = q.order_by(Link.seq.asc()).all()
            dicts = [_link_to_dict(l) for l in links]
            cursor = max((l.seq for l in links), default=(since or 0))
            return {"links": dicts, "cursor": cursor}
        finally:
            db.close()

    return router
