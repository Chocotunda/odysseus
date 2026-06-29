"""DB layer: owner_self_addresses + resolve_or_create_person_by_email."""
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, EmailAccount
from core.hub_models import Person, next_person_seq
from src.contacts import owner_self_addresses, resolve_or_create_person_by_email


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _person(db, owner, name, email, source="manual"):
    p = Person(id=str(uuid.uuid4()), owner=owner, name=name, email=email, source=source)
    p.seq = next_person_seq(db, owner)
    db.add(p); db.commit()
    return p


def test_self_addresses_from_email_accounts(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_OWNER_EMAILS", raising=False)
    db = _db()
    db.add(EmailAccount(id="e1", owner="alice", name="alice-gmail", imap_user="Alice@Gmail.com"))
    db.commit()
    addrs = owner_self_addresses(db, "alice")
    assert "alice@gmail.com" in addrs


def test_self_addresses_env_override(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_OWNER_EMAILS", "me@work.com, alias@x.io")
    db = _db()
    addrs = owner_self_addresses(db, "alice")
    assert {"me@work.com", "alias@x.io"} <= addrs


def test_resolve_matches_existing_person_without_downgrade():
    db = _db()
    existing = _person(db, "alice", "Wiggert", "wiggert@firm.nl", source="manual")
    cache = {}
    pid = resolve_or_create_person_by_email(db, "alice", "WIGGERT@firm.nl", "Wiggert L", cache=cache)
    db.commit()
    assert pid == existing.id
    db.refresh(existing)
    assert existing.source == "manual"            # never downgraded
    assert cache["wiggert@firm.nl"] == existing.id


def test_resolve_creates_calendar_person_and_dedupes():
    db = _db()
    cache = {}
    a = resolve_or_create_person_by_email(db, "alice", "new@x.com", "New Person", cache=cache)
    db.commit()
    b = resolve_or_create_person_by_email(db, "alice", "NEW@x.com", "Dup", cache=cache)  # same email
    db.commit()
    assert a == b                                  # one person, deduped via cache
    p = db.query(Person).filter(Person.id == a).first()
    assert p.source == "calendar"
    assert p.email == "new@x.com" and p.name == "New Person"
    assert (p.seq or 0) > 0                         # seq stamped


def test_resolve_owner_scoped():
    db = _db()
    _person(db, "bob", "Bob's Wiggert", "wiggert@firm.nl")   # different owner
    cache = {}
    pid = resolve_or_create_person_by_email(db, "alice", "wiggert@firm.nl", "W", cache=cache)
    db.commit()
    p = db.query(Person).filter(Person.id == pid).first()
    assert p.owner == "alice"                       # created fresh for alice, not bob's
