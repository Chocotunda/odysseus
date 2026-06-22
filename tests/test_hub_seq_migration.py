import sqlite3
import core.hub_models as hub_models


def test_seq_migration_adds_and_backfills(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE plan_items (id TEXT PRIMARY KEY, owner TEXT, updated_at TEXT)")
    conn.executemany(
        "INSERT INTO plan_items (id, owner, updated_at) VALUES (?, ?, ?)",
        [("a", "alice", "2026-06-20"), ("b", "alice", "2026-06-21"), ("c", "bob", "2026-06-20")],
    )
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_plan_item_seq_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(plan_items)").fetchall()]
    seqs = dict(conn.execute("SELECT id, seq FROM plan_items").fetchall())
    conn.close()
    assert "seq" in cols
    assert seqs == {"a": 1, "b": 2, "c": 1}   # per-owner 1..N by updated_at order
