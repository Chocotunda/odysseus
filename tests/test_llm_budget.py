import src.llm_budget as budget
from core.database import DailyLLMSpend, SessionLocal


def _reset(monkeypatch, cap, warn=80):
    monkeypatch.setattr(budget, "DAILY_LLM_BUDGET_USD", cap, raising=False)
    monkeypatch.setattr(budget, "DAILY_LLM_WARN_PCT", warn, raising=False)
    budget._WARNED_DAYS.clear()
    db = SessionLocal()
    db.query(DailyLLMSpend).filter(DailyLLMSpend.day == budget.day_key()).delete()
    db.commit()
    db.close()


def test_disabled_when_cap_zero(monkeypatch):
    _reset(monkeypatch, 0.0)
    budget.add_spend(999.0)
    assert budget.budget_status() == "disabled"


def test_ok_then_warn_then_exceeded(monkeypatch):
    _reset(monkeypatch, 10.0, warn=80)
    assert budget.budget_status() == "ok"
    budget.add_spend(5.0)
    assert budget.budget_status() == "ok"
    budget.add_spend(3.5)            # 8.5 / 10 = 85% >= 80%
    assert budget.budget_status() == "warn"
    budget.add_spend(2.0)            # 10.5 >= 10
    assert budget.budget_status() == "exceeded"


def test_should_warn_fires_once_per_day(monkeypatch):
    _reset(monkeypatch, 10.0, warn=80)
    budget.add_spend(9.0)
    assert budget.should_warn() is True
    assert budget.should_warn() is False  # already warned today


def test_add_spend_accumulates(monkeypatch):
    _reset(monkeypatch, 100.0)
    budget.add_spend(1.0)
    budget.add_spend(2.5)
    assert round(budget.today_spend(), 2) == 3.5


def test_should_warn_fires_on_direct_jump_to_exceeded(monkeypatch):
    _reset(monkeypatch, 10.0, warn=80)
    budget.add_spend(12.0)                 # ok -> exceeded in one step
    assert budget.budget_status() == "exceeded"
    assert budget.should_warn() is True    # advisory still fires once
    assert budget.should_warn() is False   # de-duped for the day
