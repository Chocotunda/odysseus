"""Helpers over the Link join table — the management-hub graph spine.

Every link read/write goes through here so node-type/rel strings live in ONE
place and owner-scoping is never forgotten. The table is polymorphic: nodes are
referenced by (type, id) pairs, not FKs, so features stay decoupled.

Links are SOFT-DELETED (tombstoned, `deleted_at` set, `seq` advanced) so the
edge graph syncs to the Tide client as a seq-cursor delta feed exactly like
PlanItem. The `uq_links_edge` unique constraint covers a tombstoned row too, so
only ONE physical row per edge tuple ever exists: reads treat a tombstone as
absent, writes REVIVE it (never re-INSERT). Every mutation stamps a fresh
per-owner `seq` via `next_link_seq` so the change appears in `/links/changes`.
"""
import uuid
from typing import List, Optional

from core.database import utcnow_naive
from core.hub_models import Link, next_link_seq

NODE_NOTE = "note"
NODE_MEETING = "meeting"
NODE_PERSON = "person"
NODE_TASK = "task"
NODE_AREA = "area"

REL_NOTE_OF = "note_of"          # Note      -> Meeting
REL_ABOUT = "about"             # Note/Task -> Person
REL_FROM_NOTE = "from_note"      # Task      -> Note
REL_ATTENDED_BY = "attended_by"  # Meeting   -> Person
REL_IN_AREA = "in_area"          # <node>    -> Area  (single primary area)


def add_link(db, owner: Optional[str], from_type: str, from_id: str,
             rel: str, to_type: str, to_id: str) -> Link:
    """Create the edge if it doesn't exist; revive it if tombstoned; return the
    live row if it already exists.

    The find-existing query MUST ignore `deleted_at` (matching the unique tuple),
    because a tombstoned row keeps its physical row — inserting a second one would
    violate `uq_links_edge`. Three cases:
      - row exists & live    -> return it unchanged (idempotent).
      - row exists & tombstoned -> REVIVE (clear deleted_at, advance seq, commit).
      - no row               -> INSERT with a fresh seq.
    """
    existing = (
        db.query(Link)
        .filter(Link.owner == owner, Link.from_type == from_type, Link.from_id == from_id,
                Link.rel == rel, Link.to_type == to_type, Link.to_id == to_id)
        .first()   # NOTE: deliberately NOT filtered by deleted_at — revive vs re-insert
    )
    if existing is not None:
        if existing.deleted_at is None:
            return existing                       # live -> idempotent, no change
        # tombstoned -> revive (never INSERT; would hit uq_links_edge)
        existing.deleted_at = None
        existing.seq = next_link_seq(db, owner)
        db.commit()
        return existing
    edge = Link(id=str(uuid.uuid4()), owner=owner, from_type=from_type, from_id=from_id,
                rel=rel, to_type=to_type, to_id=to_id,
                deleted_at=None, seq=next_link_seq(db, owner))
    db.add(edge)
    db.commit()
    return edge


def links_from(db, owner: Optional[str], from_type: str, from_id: str,
               rel: Optional[str] = None) -> List[Link]:
    q = (db.query(Link)
         .filter(Link.owner == owner, Link.from_type == from_type, Link.from_id == from_id)
         .filter(Link.deleted_at.is_(None)))   # tombstones are absent for reads
    if rel is not None:
        q = q.filter(Link.rel == rel)
    return q.all()


def links_to(db, owner: Optional[str], to_type: str, to_id: str,
             rel: Optional[str] = None) -> List[Link]:
    q = (db.query(Link)
         .filter(Link.owner == owner, Link.to_type == to_type, Link.to_id == to_id)
         .filter(Link.deleted_at.is_(None)))   # tombstones are absent for reads
    if rel is not None:
        q = q.filter(Link.rel == rel)
    return q.all()


def remove_links_for(db, owner: Optional[str], node_type: str, node_id: str) -> int:
    """Soft-delete every LIVE edge touching a node at either end (used when a node
    is deleted). Row-enumerated so each tombstone gets its OWN fresh seq, so the
    client learns every deleted edge. Returns the count tombstoned."""
    rows = (
        db.query(Link)
        .filter(Link.owner == owner, Link.deleted_at.is_(None))
        .filter(((Link.from_type == node_type) & (Link.from_id == node_id))
                | ((Link.to_type == node_type) & (Link.to_id == node_id)))
        .all()
    )
    now = utcnow_naive()
    for row in rows:
        row.deleted_at = now
        row.seq = next_link_seq(db, owner)
    db.commit()
    return len(rows)


def set_area(db, owner: Optional[str], node_type: str, node_id: str,
             area_id: Optional[str]):
    """Set a node's single primary Area. Soft-deletes any existing live in_area
    edge from the node first (enforces 'exactly one'), then adds node -> area
    (which revives the prior row if reassigning back, or creates a new one). A
    falsy area_id just clears and returns None."""
    rows = (
        db.query(Link)
        .filter(Link.owner == owner, Link.from_type == node_type,
                Link.from_id == node_id, Link.rel == REL_IN_AREA,
                Link.deleted_at.is_(None))
        .all()
    )
    now = utcnow_naive()
    for row in rows:
        row.deleted_at = now
        row.seq = next_link_seq(db, owner)
    db.commit()
    if not area_id:
        return None
    return add_link(db, owner, node_type, node_id, REL_IN_AREA, NODE_AREA, area_id)


def clear_area(db, owner: Optional[str], node_type: str, node_id: str) -> int:
    """Soft-delete a node's live in_area edge(s). Returns the number tombstoned."""
    rows = (
        db.query(Link)
        .filter(Link.owner == owner, Link.from_type == node_type,
                Link.from_id == node_id, Link.rel == REL_IN_AREA,
                Link.deleted_at.is_(None))
        .all()
    )
    now = utcnow_naive()
    for row in rows:
        row.deleted_at = now
        row.seq = next_link_seq(db, owner)
    db.commit()
    return len(rows)
