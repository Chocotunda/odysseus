from core.database import DailyLLMSpend, SessionLocal

def test_insert_and_read_row():
    db = SessionLocal()
    try:
        db.query(DailyLLMSpend).filter(DailyLLMSpend.day == "2099-01-01").delete()
        db.add(DailyLLMSpend(day="2099-01-01", cost_usd=1.25))
        db.commit()
        row = db.query(DailyLLMSpend).filter(DailyLLMSpend.day == "2099-01-01").first()
        assert row is not None and round(row.cost_usd, 2) == 1.25
    finally:
        db.query(DailyLLMSpend).filter(DailyLLMSpend.day == "2099-01-01").delete()
        db.commit()
        db.close()
