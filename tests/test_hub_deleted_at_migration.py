import sqlite3
import core.hub_models as hub_models


def test_deleted_at_migration_adds_column(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE plan_items (id TEXT PRIMARY KEY, ordinal INTEGER)")
    conn.execute("INSERT INTO plan_items (id, ordinal) VALUES ('x', 1024)")
    conn.commit(); conn.close()

    monkeypatch.setattr(hub_models, "DATABASE_URL", f"sqlite:///{db_file}")
    hub_models._migrate_add_plan_item_deleted_at_column()

    conn = sqlite3.connect(db_file)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(plan_items)").fetchall()]
    # fractional ordinal round-trips in the integer-affinity column (no migration needed there)
    conn.execute("UPDATE plan_items SET ordinal = 1536.5 WHERE id = 'x'")
    val = conn.execute("SELECT ordinal FROM plan_items WHERE id='x'").fetchone()[0]
    conn.close()
    assert "deleted_at" in cols
    assert val == 1536.5
