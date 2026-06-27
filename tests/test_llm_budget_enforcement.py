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


# ── Fix 2: stream_llm yields a clean error event instead of raising ──────────

_METERED_URL = "https://api.deepseek.com/v1/chat/completions"


async def test_stream_llm_over_budget_yields_error_event(monkeypatch):
    """When the daily cap is exceeded stream_llm must yield an error SSE
    event (status 402) and [DONE], then return — no exception must propagate.
    """
    _reset(monkeypatch, 1.0)
    budget.add_spend(2.0)   # now over cap

    chunks = []
    async for chunk in llm.stream_llm(
        url=_METERED_URL,
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hello"}],
    ):
        chunks.append(chunk)

    assert len(chunks) == 2, f"Expected 2 chunks, got {chunks}"
    error_chunk = chunks[0]
    done_chunk = chunks[1]

    assert error_chunk.startswith("event: error\n"), (
        f"First chunk should be an error event, got: {error_chunk!r}"
    )
    import json as _json
    data_line = error_chunk.split("data: ", 1)[1].strip()
    payload = _json.loads(data_line)
    assert payload.get("status") == 402, f"Expected status 402, got: {payload}"
    assert "budget" in payload.get("error", "").lower() or payload.get("error"), (
        f"Expected a budget message in error field, got: {payload}"
    )
    assert done_chunk.strip() == "data: [DONE]", (
        f"Expected [DONE] as second chunk, got: {done_chunk!r}"
    )
