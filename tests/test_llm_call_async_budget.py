"""llm_call_async (the NON-streaming LLM path) must enforce + accrue the per-day
spend cap, exactly like stream_llm. Before this it was uncapped, so non-streaming
chat and any llm_call_async caller could spend past the budget unmetered.

These tests stub the HTTP transport (no live API) and work against the real
DailyLLMSpend table via SessionLocal (safe in test envs).
"""
import pytest
from fastapi import HTTPException
import src.llm_core as llm
import src.llm_budget as budget
from core.database import DailyLLMSpend, SessionLocal

_METERED = "https://openrouter.ai/api/v1/chat/completions"
_LOCAL = "http://localhost:8002/v1/chat/completions"  # local => exempt, openai-compat shape


def _reset(monkeypatch, cap):
    monkeypatch.setattr(budget, "DAILY_LLM_BUDGET_USD", cap, raising=False)
    budget._WARNED_DAYS.clear()
    db = SessionLocal()
    db.query(DailyLLMSpend).filter(DailyLLMSpend.day == budget.day_key()).delete()
    db.commit(); db.close()


class _FakeResp:
    def __init__(self, payload):
        self._p = payload
        self.is_success = True
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._p


def _mock_post(payload):
    async def _post(client, url, headers, **kwargs):
        return _FakeResp(payload)
    return _post


async def test_over_budget_blocks_call(monkeypatch):
    """Over budget on a metered endpoint => HTTPException(402), and NO HTTP call."""
    _reset(monkeypatch, 1.0)
    budget.add_spend(2.0)  # over the $1 cap

    async def _boom(*a, **k):
        raise AssertionError("HTTP must not be called when over budget")
    monkeypatch.setattr(llm, "httpx_post_kimi_aware_async", _boom)

    with pytest.raises(HTTPException) as ei:
        await llm.llm_call_async(_METERED, "deepseek/deepseek-v4-flash",
                                 [{"role": "user", "content": "blocked?"}])
    assert ei.value.status_code == 402


async def test_successful_call_accrues(monkeypatch):
    """A successful metered non-streaming call accrues its cost to the day's spend."""
    _reset(monkeypatch, 100.0)
    payload = {"choices": [{"message": {"content": "ok"}}],
               "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
    monkeypatch.setattr(llm, "httpx_post_kimi_aware_async", _mock_post(payload))

    out = await llm.llm_call_async(_METERED, "deepseek-v4-flash",
                                   [{"role": "user", "content": "accrue please"}])
    assert out == "ok"
    assert round(budget.today_spend(), 4) == 0.14  # 1M input @ $0.14/1M


async def test_local_endpoint_exempt(monkeypatch):
    """Local endpoint is exempt: over budget still proceeds, and no accrual."""
    _reset(monkeypatch, 1.0)
    budget.add_spend(2.0)  # over cap, but local must be exempt
    payload = {"choices": [{"message": {"content": "ok"}}],
               "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
    monkeypatch.setattr(llm, "httpx_post_kimi_aware_async", _mock_post(payload))

    out = await llm.llm_call_async(_LOCAL, "qwen",
                                   [{"role": "user", "content": "local exempt"}])
    assert out == "ok"
    assert round(budget.today_spend(), 4) == 2.0  # unchanged — local accrues nothing
