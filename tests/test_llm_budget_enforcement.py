"""Task 4: verify that stream_llm enforces the per-day spend cap and accrues cost.

These tests do NOT hit a live API — they stub usage dicts and work against the
real DailyLLMSpend table (via SessionLocal), which is safe in test environments.
"""
import pytest
import src.llm_core as llm
import src.llm_budget as budget
from core.database import DailyLLMSpend, SessionLocal


def _reset(monkeypatch, cap):
    monkeypatch.setattr(budget, "DAILY_LLM_BUDGET_USD", cap, raising=False)
    budget._WARNED_DAYS.clear()
    db = SessionLocal()
    db.query(DailyLLMSpend).filter(DailyLLMSpend.day == budget.day_key()).delete()
    db.commit(); db.close()


def test_metered_over_budget_raises(monkeypatch):
    _reset(monkeypatch, 1.0)
    budget.add_spend(2.0)
    with pytest.raises(llm.LLMBudgetExceeded):
        llm._enforce_budget("https://api.deepseek.com/v1/chat/completions")


def test_local_endpoint_never_raises(monkeypatch):
    _reset(monkeypatch, 1.0)
    budget.add_spend(99.0)
    llm._enforce_budget("http://localhost:11434/v1/chat/completions")  # no raise


def test_disabled_never_raises(monkeypatch):
    _reset(monkeypatch, 0.0)
    budget.add_spend(99.0)
    llm._enforce_budget("https://api.deepseek.com/v1/chat/completions")  # no raise


def test_account_cost_records_for_metered(monkeypatch):
    _reset(monkeypatch, 100.0)
    llm._account_llm_cost("https://api.deepseek.com/v1/chat/completions",
                          "deepseek-v4-flash",
                          {"input_tokens": 1_000_000, "output_tokens": 0})
    assert round(budget.today_spend(), 4) == 0.14


def test_account_cost_skips_local(monkeypatch):
    _reset(monkeypatch, 100.0)
    llm._account_llm_cost("http://localhost:11434/v1/chat/completions",
                          "deepseek-v4-flash", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert budget.today_spend() == 0.0
