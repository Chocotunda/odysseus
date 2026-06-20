"""Area seeding for the management hub. The owner's first visit to /api/areas
gets three default life-areas; all are editable nodes thereafter."""
import uuid
from typing import Optional

from core.hub_models import Area

# (name, stored hex) — distinct colors from the UI palette.
SEED_AREAS = [
    ("Work", "#0066ff"),
    ("Personal", "#1dbf8c"),
    ("Krishna Movements", "#ff9100"),
]


def ensure_seeded_areas(db, owner: Optional[str]) -> None:
    """Create the three default areas for an owner that has none. Idempotent."""
    if db.query(Area).filter(Area.owner == owner).count() > 0:
        return
    for i, (name, color) in enumerate(SEED_AREAS):
        db.add(Area(id=str(uuid.uuid4()), owner=owner, name=name, color=color, sort_order=i))
    db.commit()
