"""Hub graph models — extracted from core/database.py to keep that upstream
file merge-clean (B2 / agnostic-core boundary; see
docs/superpowers/specs/2026-06-20-hermes-odysseus-hybrid-direction.md).

These register on the SAME declarative Base via init_db()'s local import, so
Base.metadata.create_all() and SessionLocal keep working unchanged.
"""
import os
import logging

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, Float, Integer, ForeignKey, Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from core.database import Base, TimestampMixin, DATABASE_URL


class PlanProject(TimestampMixin, Base):
    """A project / area / channel that PlanItems group under (Sunsama 'channel').

    Part of the Planner feature — the user-facing daily-planning surface, kept
    deliberately separate from the scheduler's ScheduledTask/TaskRun (which are
    backend automation, not to-dos).
    """
    __tablename__ = "plan_projects"

    id         = Column(String, primary_key=True, index=True)
    owner      = Column(String, nullable=True, index=True)
    name       = Column(String, nullable=False)
    color      = Column(String, nullable=True)      # reuse the UI CSS-var hexes
    archived   = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)

    __table_args__ = (Index('ix_plan_projects_owner_archived', 'owner', 'archived'),)


class PlanItem(TimestampMixin, Base):
    """A user-facing task / to-do with planning fields.

    Distinct from ScheduledTask (backend automation). `planned_day`/`due_date`
    are 'YYYY-MM-DD' strings in the user's local day (server tz for now), so the
    central "items for a day = exact string equality" invariant holds even if the
    server runs UTC. `ordinal` uses an integer-gap scheme for drag-reorder.

    The `source_*`/`person_id` columns are soft references (plain ids, not FKs)
    into other features, so a task can carry a back-link to the Note / meeting /
    person it came from without coupling delete-cascades across features. They
    are unused by Phase 1 CRUD but designed in now to avoid a later migration.
    """
    __tablename__ = "plan_items"

    id            = Column(String, primary_key=True, index=True)
    owner         = Column(String, nullable=True, index=True)  # always stamped on write
    title         = Column(String, nullable=False, default="")
    notes         = Column(Text, nullable=True)
    # planning
    planned_day   = Column(String, nullable=True, index=True)   # 'YYYY-MM-DD'; NULL = backlog
    due_date      = Column(String, nullable=True)               # 'YYYY-MM-DD'
    planned_start = Column(String, nullable=True)              # 'HH:MM' local; set => time-blocked
    priority      = Column(String, default="normal")  # none|normal|important|urgent
    status        = Column(String, default="open")    # open|in_progress|done|cancelled
    completed_at  = Column(DateTime, nullable=True)
    deleted_at    = Column(DateTime, nullable=True, index=True)   # soft-delete tombstone; NULL = live
    seq           = Column(Integer, index=True)   # per-owner monotonic write sequence; the ?since= cursor
    # effort
    estimate_minutes = Column(Integer, nullable=True)
    # ordering (float-gap scheme; new items at max+1024.0; midpoint for reorder)
    ordinal       = Column(Float, default=0, index=True)
    # grouping
    project_id    = Column(String, ForeignKey("plan_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    source        = Column(String, default="user")       # user|agent|email|calendar|capture
    session_id    = Column(String, nullable=True)        # chat session that spawned/solves it
    # soft cross-feature back-references (no FK on purpose — see docstring)
    source_note_id  = Column(String, nullable=True, index=True)
    source_event_id = Column(String, nullable=True, index=True)
    person_id       = Column(String, nullable=True, index=True)
    # AI (mirror Note's reserved fields; gate re-classification to avoid LLM spend)
    ai_classification = Column(Text, nullable=True)
    ai_content_hash   = Column(String, nullable=True)

    project = relationship("PlanProject")

    __table_args__ = (
        Index('ix_plan_items_owner_day', 'owner', 'planned_day'),
        Index('ix_plan_items_owner_status', 'owner', 'status'),
    )


class Link(TimestampMixin, Base):
    """A typed edge between two nodes — the whole graph spine.

    `(from_type, from_id, rel, to_type, to_id)` is a directed, typed edge.
    Backlinks are just the reverse query (`links_to`). Polymorphic by design so
    every node type (note/meeting/person/task/email/doc/...) links the same way
    with no new schema. Owner-scoped: a new isolation boundary.
    """
    __tablename__ = "links"

    id        = Column(String, primary_key=True, index=True)
    owner     = Column(String, nullable=True, index=True)
    from_type = Column(String, nullable=False)
    from_id   = Column(String, nullable=False)
    rel       = Column(String, nullable=False)
    to_type   = Column(String, nullable=False)
    to_id     = Column(String, nullable=False)

    __table_args__ = (
        Index('ix_links_from', 'owner', 'from_type', 'from_id'),
        Index('ix_links_to', 'owner', 'to_type', 'to_id'),
        UniqueConstraint('owner', 'from_type', 'from_id', 'rel', 'to_type', 'to_id',
                         name='uq_links_edge'),
    )


class Person(TimestampMixin, Base):
    """A person the user tracks (report, contact). First-class node in the hub
    graph — meetings/notes/tasks link to it via the Link table. Single-user:
    people do NOT log in; this is info the owner keeps ABOUT them."""
    __tablename__ = "people"

    id          = Column(String, primary_key=True, index=True)
    owner       = Column(String, nullable=True, index=True)
    name        = Column(String, nullable=False, default="")
    contact_uid = Column(String, nullable=True)   # optional iCloud CardDAV contact ref
    email       = Column(String, nullable=True)
    role        = Column(String, nullable=True)
    archived    = Column(Boolean, default=False)

    __table_args__ = (Index('ix_people_owner_archived', 'owner', 'archived'),)


class Area(TimestampMixin, Base):
    """A life-area / context (Work, Personal, Krishna Movements...) — a first-class
    node. A node belongs to at most ONE area via a single `in_area` Link edge; the
    Area dashboard aggregates its members via reverse-Link queries. Owner-scoped."""
    __tablename__ = "areas"

    id         = Column(String, primary_key=True, index=True)
    owner      = Column(String, nullable=True, index=True)
    name       = Column(String, nullable=False, default="")
    color      = Column(String, nullable=True)   # stored hex (reuses UI palette)
    sort_order = Column(Integer, default=0)
    archived   = Column(Boolean, default=False)

    __table_args__ = (Index('ix_areas_owner_archived', 'owner', 'archived'),)


def _migrate_add_plan_item_deleted_at_column():
    """Add `deleted_at` (soft-delete tombstone) to plan_items. Guarded + idempotent."""
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(plan_items)")
        columns = [row[1] for row in cursor.fetchall()]
        if "deleted_at" not in columns:
            conn.execute("ALTER TABLE plan_items ADD COLUMN deleted_at DATETIME")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_plan_items_deleted_at ON plan_items(deleted_at)")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'deleted_at' to plan_items")
    except Exception as e:
        logging.getLogger(__name__).warning(f"plan_items.deleted_at migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_plan_item_planned_start_column():
    """Add `planned_start` ('HH:MM' time-block) to plan_items. Guarded + idempotent.

    create_all() only creates missing tables; it does NOT alter an existing
    plan_items table, so this column must be added explicitly for DBs created
    before the /today day-planner slice.
    """
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(plan_items)")
        columns = [row[1] for row in cursor.fetchall()]
        if "planned_start" not in columns:
            conn.execute("ALTER TABLE plan_items ADD COLUMN planned_start VARCHAR")
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added 'planned_start' to plan_items")
    except Exception as e:
        logging.getLogger(__name__).warning(f"plan_items.planned_start migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _migrate_add_plan_item_seq_column():
    """Add per-owner monotonic `seq` to plan_items + backfill existing rows. Guarded + idempotent."""
    import sqlite3
    from collections import defaultdict
    db_path = DATABASE_URL.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(plan_items)")
        columns = [row[1] for row in cursor.fetchall()]
        if "seq" not in columns:
            conn.execute("ALTER TABLE plan_items ADD COLUMN seq INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_plan_items_seq ON plan_items(seq)")
            # backfill: per-owner 1..N in a stable (updated_at, id) order
            rows = conn.execute(
                "SELECT id, owner FROM plan_items ORDER BY owner, updated_at, id"
            ).fetchall()
            counters = defaultdict(int)
            for rid, owner in rows:
                counters[owner] += 1
                conn.execute("UPDATE plan_items SET seq = ? WHERE id = ?", (counters[owner], rid))
            conn.commit()
            logging.getLogger(__name__).info("Migrated: added + backfilled 'seq' on plan_items")
    except Exception as e:
        logging.getLogger(__name__).warning(f"plan_items.seq migration failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def next_plan_item_seq(db, owner) -> int:
    """Next per-owner monotonic write sequence for PlanItem (the /items/changes cursor).
    Any code that writes a PlanItem MUST set `seq = next_plan_item_seq(db, owner)` before
    commit, or the row/edit will never appear in the delta feed (seq > since skips NULL)."""
    from sqlalchemy import func
    current = db.query(func.max(PlanItem.seq)).filter(PlanItem.owner == owner).scalar()
    return (current or 0) + 1


def run_hub_migrations():
    """Run hub-owned column migrations (guarded + idempotent). Called from
    core.database.init_db() after create_all()."""
    _migrate_add_plan_item_planned_start_column()
    _migrate_add_plan_item_deleted_at_column()
    _migrate_add_plan_item_seq_column()
