"""Helpers over the Link join table — the management-hub graph spine.

Every link read/write goes through here so node-type/rel strings live in ONE
place and owner-scoping is never forgotten. The table is polymorphic: nodes are
referenced by (type, id) pairs, not FKs, so features stay decoupled.
"""
import uuid
from typing import List, Optional

from core.database import Link

NODE_NOTE = "note"
NODE_MEETING = "meeting"
NODE_PERSON = "person"
NODE_TASK = "task"

REL_NOTE_OF = "note_of"          # Note      -> Meeting
REL_ABOUT = "about"             # Note/Task -> Person
REL_FROM_NOTE = "from_note"      # Task      -> Note
REL_ATTENDED_BY = "attended_by"  # Meeting   -> Person


def add_link(db, owner: Optional[str], from_type: str, from_id: str,
             rel: str, to_type: str, to_id: str) -> Link:
    """Create the edge if it doesn't exist; return the existing one if it does."""
    existing = (
        db.query(Link)
        .filter(Link.owner == owner, Link.from_type == from_type, Link.from_id == from_id,
                Link.rel == rel, Link.to_type == to_type, Link.to_id == to_id)
        .first()
    )
    if existing:
        return existing
    edge = Link(id=str(uuid.uuid4()), owner=owner, from_type=from_type, from_id=from_id,
                rel=rel, to_type=to_type, to_id=to_id)
    db.add(edge)
    db.commit()
    return edge


def links_from(db, owner: Optional[str], from_type: str, from_id: str,
               rel: Optional[str] = None) -> List[Link]:
    q = db.query(Link).filter(Link.owner == owner, Link.from_type == from_type, Link.from_id == from_id)
    if rel is not None:
        q = q.filter(Link.rel == rel)
    return q.all()


def links_to(db, owner: Optional[str], to_type: str, to_id: str,
             rel: Optional[str] = None) -> List[Link]:
    q = db.query(Link).filter(Link.owner == owner, Link.to_type == to_type, Link.to_id == to_id)
    if rel is not None:
        q = q.filter(Link.rel == rel)
    return q.all()


def remove_links_for(db, owner: Optional[str], node_type: str, node_id: str) -> int:
    """Delete every edge touching a node at either end (used when a node is deleted)."""
    n = (
        db.query(Link)
        .filter(Link.owner == owner)
        .filter(((Link.from_type == node_type) & (Link.from_id == node_id))
                | ((Link.to_type == node_type) & (Link.to_id == node_id)))
        .delete(synchronize_session=False)
    )
    db.commit()
    return n
