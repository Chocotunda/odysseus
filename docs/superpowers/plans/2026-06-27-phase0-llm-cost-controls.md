# Phase 0 — LLM Cost Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cost guardrails to Odysseus's metered LLM path — a per-day USD spend cap (hard-fail + 80% warning), cache-hit observability, and a verified-stable cache prefix — all at the `stream_llm` chokepoint, disabled by default.

**Architecture:** A pure pricing module (`compute_cost`) + a persistent daily-spend accumulator (DB model + a small budget module) are wired into `src/llm_core.py:stream_llm`: a pre-call gate (raise when over budget, warn once at the threshold) and a post-call cost accrual. Cache-hit token counts are extracted from provider usage and surfaced. A regression test pins the DeepSeek cache-prefix stability. Local/free endpoints are fully exempt.

**Tech Stack:** Python 3.11, SQLAlchemy (SQLite), pytest (`asyncio_mode=auto`).

## Global Constraints

- **Config via `src/constants.py` + env**, never literals; follow the `os.getenv(...) or default` pattern (an empty env value must fall back).
- **Disabled by default:** with no budget configured, behaviour is a strict no-op (no exceptions, no cost DB writes).
- **Local/free endpoints are exempt** from cost accounting and the cap — gate every cap branch on `not is_local_endpoint(url)`.
- **Unknown model ⇒ cost 0.0** (never false-cap).
- **No Unicode emoji** in code or strings.
- **Conventional Commits** (`feat:`/`test:`/`refactor:`).
- **Tests require no live API** — stub usage dicts and the DB only.
- Pricing is **per 1,000,000 tokens** everywhere; divide by `1_000_000` when computing cost.

---

### Task 1: Pricing module + `compute_cost`

**Files:**
- Create: `src/llm_pricing.py`
- Test: `tests/test_llm_pricing.py`

**Interfaces:**
- Produces: `PRICING: dict[str, tuple[float, float, float]]` (model_id → `(input_per_1m, output_per_1m, cache_hit_input_per_1m)`); `price_for(model: str) -> tuple[float, float, float] | None`; `compute_cost(model: str, usage: dict) -> float` where `usage` may carry `input_tokens`, `output_tokens`, `cache_hit_tokens` (all int, optional, default 0).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_pricing.py
from src.llm_pricing import compute_cost, price_for

def test_known_model_standard_cost():
    # DeepSeek V4-Flash: 0.14 / 0.28 per 1M. 1M input + 1M output = 0.14 + 0.28
    cost = compute_cost("deepseek-v4-flash", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert round(cost, 6) == 0.42

def test_cache_hit_discounts_input():
    # 1M input of which 1M is a cache hit (0.0028/1M), 0 output
    cost = compute_cost("deepseek-v4-flash",
                        {"input_tokens": 1_000_000, "output_tokens": 0, "cache_hit_tokens": 1_000_000})
    assert round(cost, 6) == 0.0028

def test_partial_cache_hit():
    # 1M input, 0.5M cached: 0.5M*0.14 + 0.5M*0.0028 = 0.07 + 0.0014
    cost = compute_cost("deepseek-v4-flash",
                        {"input_tokens": 1_000_000, "output_tokens": 0, "cache_hit_tokens": 500_000})
    assert round(cost, 6) == 0.0714

def test_unknown_model_is_free():
    assert compute_cost("some-random-model", {"input_tokens": 9_999_999, "output_tokens": 9_999_999}) == 0.0
    assert price_for("some-random-model") is None

def test_model_id_contains_fallback():
    # provider-prefixed id still resolves
    assert price_for("us.anthropic.claude-opus-4-8") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm_pricing.py -v`
Expected: FAIL (`ModuleNotFoundError: src.llm_pricing`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/llm_pricing.py
"""Per-model LLM pricing + cost computation for the Phase 0 spend cap.

Prices are USD per 1,000,000 tokens: (input, output, cache_hit_input).
Override/extend via the ODYSSEUS_LLM_PRICING_JSON env var (a JSON object of
model_id -> [input, output, cache_hit]). Unknown models cost 0.0 so the cap
never fires on un-priced traffic.
"""
import json
import logging
import os
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Seed table — verified 2026-06 (see docs/ai-context/2026-06-26-track-a-...).
PRICING: Dict[str, Tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28, 0.0028),
    "deepseek-v4-pro": (0.435, 0.87, 0.003625),
    "deepseek-chat": (0.14, 0.28, 0.0028),
    "deepseek-reasoner": (0.55, 2.19, 0.055),
    "claude-opus-4-8": (5.0, 25.0, 0.5),
    "claude-sonnet-4-6": (3.0, 15.0, 0.3),
    "claude-haiku-4-5": (1.0, 5.0, 0.1),
}


def _load_overrides() -> None:
    raw = os.getenv("ODYSSEUS_LLM_PRICING_JSON") or ""
    if not raw.strip():
        return
    try:
        for model, triple in (json.loads(raw) or {}).items():
            PRICING[str(model)] = (float(triple[0]), float(triple[1]), float(triple[2]))
    except Exception as e:  # never let bad config break LLM calls
        logger.warning("Ignoring invalid ODYSSEUS_LLM_PRICING_JSON: %s", e)


_load_overrides()


def price_for(model: str) -> Optional[Tuple[float, float, float]]:
    if not model:
        return None
    if model in PRICING:
        return PRICING[model]
    # provider-prefixed / suffixed ids (e.g. "us.anthropic.claude-opus-4-8[1m]")
    for key, triple in PRICING.items():
        if key in model:
            return triple
    return None


def compute_cost(model: str, usage: dict) -> float:
    triple = price_for(model)
    if not triple:
        return 0.0
    in_price, out_price, cache_price = triple
    usage = usage or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    hit_tok = min(int(usage.get("cache_hit_tokens") or 0), in_tok)
    miss_tok = in_tok - hit_tok
    return (miss_tok * in_price + hit_tok * cache_price + out_tok * out_price) / 1_000_000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_llm_pricing.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/llm_pricing.py tests/test_llm_pricing.py
git commit -m "feat(llm): per-model pricing table + compute_cost for the spend cap"
```

---

### Task 2: `DailyLLMSpend` persistence model

**Files:**
- Modify: `core/database.py` (add a model class near the other models; reuse the existing `Base`/`TimestampMixin`/`SessionLocal`)
- Test: `tests/test_daily_llm_spend_model.py`

**Interfaces:**
- Produces: `DailyLLMSpend` ORM model with columns `day` (String, primary key), `cost_usd` (Float, default 0.0), plus `created_at`/`updated_at` from `TimestampMixin`. Table name `daily_llm_spend`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_daily_llm_spend_model.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_daily_llm_spend_model.py -v`
Expected: FAIL (`ImportError: cannot import name 'DailyLLMSpend'`).

- [ ] **Step 3: Write minimal implementation**

Add to `core/database.py` (after an existing model class, before the table-create call at the bottom; match the surrounding column style):

```python
class DailyLLMSpend(TimestampMixin, Base):
    """One row per local day: cumulative USD spent on metered LLM calls.
    Persistent so a per-day budget survives a process restart (Phase 0 cap)."""
    __tablename__ = "daily_llm_spend"

    day      = Column(String, primary_key=True, index=True)   # "YYYY-MM-DD" local
    cost_usd = Column(Float, nullable=False, default=0.0)
```

If `Float` is not already imported at the top of `core/database.py`, add it to the existing `from sqlalchemy import (...)` line.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_daily_llm_spend_model.py -v`
Expected: PASS. (The schema is auto-created by the existing `Base.metadata.create_all` / setup path; if the test DB predates this model, the conftest fixture recreates tables.)

- [ ] **Step 5: Commit**

```bash
git add core/database.py tests/test_daily_llm_spend_model.py
git commit -m "feat(db): DailyLLMSpend model for the per-day LLM budget"
```

---

### Task 3: Budget module (accumulator + status + config)

**Files:**
- Create: `src/llm_budget.py`
- Modify: `src/constants.py` (add config constants)
- Test: `tests/test_llm_budget.py`

**Interfaces:**
- Consumes: `DailyLLMSpend`, `SessionLocal` (Task 2); `src.user_time.now_user_local`; constants below.
- Produces:
  - constants in `src/constants.py`: `DAILY_LLM_BUDGET_USD: float` (0.0 = disabled), `DAILY_LLM_WARN_PCT: int` (default 80).
  - `day_key() -> str` (local "YYYY-MM-DD"); `today_spend() -> float`; `add_spend(cost: float) -> None` (upsert add); `budget_status() -> str` returning `"disabled" | "ok" | "warn" | "exceeded"`; `should_warn() -> bool` (true once per day when first crossing the warn threshold).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_budget.py
import src.llm_budget as budget
from core.database import DailyLLMSpend, SessionLocal

def _reset(monkeypatch, cap, warn=80):
    monkeypatch.setattr(budget, "DAILY_LLM_BUDGET_USD", cap, raising=False)
    monkeypatch.setattr(budget, "DAILY_LLM_WARN_PCT", warn, raising=False)
    budget._WARNED_DAYS.clear()
    db = SessionLocal()
    db.query(DailyLLMSpend).filter(DailyLLMSpend.day == budget.day_key()).delete()
    db.commit(); db.close()

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
    budget.add_spend(1.0); budget.add_spend(2.5)
    assert round(budget.today_spend(), 2) == 3.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm_budget.py -v`
Expected: FAIL (`ModuleNotFoundError: src.llm_budget`).

- [ ] **Step 3a: Add config to `src/constants.py`**

```python
# --- Phase 0 LLM spend cap (0.0 disables the cap entirely) ---
DAILY_LLM_BUDGET_USD = float(os.getenv("ODYSSEUS_DAILY_LLM_BUDGET_USD") or 0.0)
DAILY_LLM_WARN_PCT = int(os.getenv("ODYSSEUS_DAILY_LLM_WARN_PCT") or 80)
```

- [ ] **Step 3b: Write `src/llm_budget.py`**

```python
# src/llm_budget.py
"""Per-day metered-LLM spend accumulator + budget status (Phase 0 cap).

Disabled when DAILY_LLM_BUDGET_USD <= 0. Spend is persisted per local day so
the cap survives a restart. Callers in llm_core gate every use on the endpoint
being metered (not local)."""
import logging
from typing import Set

from src.constants import DAILY_LLM_BUDGET_USD, DAILY_LLM_WARN_PCT
from src.user_time import now_user_local
from core.database import DailyLLMSpend, SessionLocal

logger = logging.getLogger(__name__)

_WARNED_DAYS: Set[str] = set()


def enabled() -> bool:
    return DAILY_LLM_BUDGET_USD and DAILY_LLM_BUDGET_USD > 0


def day_key() -> str:
    return now_user_local().strftime("%Y-%m-%d")


def today_spend() -> float:
    db = SessionLocal()
    try:
        row = db.query(DailyLLMSpend).filter(DailyLLMSpend.day == day_key()).first()
        return float(row.cost_usd) if row else 0.0
    finally:
        db.close()


def add_spend(cost: float) -> None:
    if not cost or cost <= 0:
        return
    db = SessionLocal()
    try:
        key = day_key()
        row = db.query(DailyLLMSpend).filter(DailyLLMSpend.day == key).first()
        if row is None:
            row = DailyLLMSpend(day=key, cost_usd=0.0)
            db.add(row)
        row.cost_usd = float(row.cost_usd or 0.0) + float(cost)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("add_spend failed (cost not recorded): %s", e)
    finally:
        db.close()


def budget_status() -> str:
    if not enabled():
        return "disabled"
    spent = today_spend()
    if spent >= DAILY_LLM_BUDGET_USD:
        return "exceeded"
    if spent >= DAILY_LLM_BUDGET_USD * (DAILY_LLM_WARN_PCT / 100.0):
        return "warn"
    return "ok"


def should_warn() -> bool:
    """True at most once per local day, when at/over the warn threshold."""
    if budget_status() != "warn":
        return False
    key = day_key()
    if key in _WARNED_DAYS:
        return False
    _WARNED_DAYS.add(key)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_llm_budget.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/llm_budget.py src/constants.py tests/test_llm_budget.py
git commit -m "feat(llm): per-day spend accumulator + budget status (disabled by default)"
```

---

### Task 4: Enforce + accrue in `stream_llm`

**Files:**
- Modify: `src/llm_core.py` (add two small helpers + call them inside `stream_llm`, `:1696`)
- Test: `tests/test_llm_budget_enforcement.py`

**Interfaces:**
- Consumes: `src.llm_budget` (Task 3); `src.llm_pricing.compute_cost` (Task 1); `is_local_endpoint` (already used at `src/llm_core.py:643`).
- Produces: `class LLMBudgetExceeded(RuntimeError)`; `_enforce_budget(url: str) -> None` (raises `LLMBudgetExceeded` when over budget on a metered endpoint; emits a one-shot warning at the threshold); `_account_llm_cost(url: str, model: str, usage: dict) -> None` (adds cost for metered endpoints).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_budget_enforcement.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm_budget_enforcement.py -v`
Expected: FAIL (`AttributeError: module 'src.llm_core' has no attribute 'LLMBudgetExceeded'`).

- [ ] **Step 3: Write minimal implementation**

Add near the top of `src/llm_core.py` (after imports):

```python
from src.llm_budget import enabled as _budget_enabled, budget_status as _budget_status, should_warn as _budget_should_warn, add_spend as _budget_add_spend, DAILY_LLM_BUDGET_USD as _DAILY_BUDGET
from src.llm_pricing import compute_cost as _compute_cost


class LLMBudgetExceeded(RuntimeError):
    """Raised when a metered LLM call is blocked by the per-day spend cap."""


def _enforce_budget(url: str) -> None:
    if not _budget_enabled():
        return
    from src.model_context import is_local_endpoint
    if is_local_endpoint(url):
        return
    status = _budget_status()
    if status == "exceeded":
        raise LLMBudgetExceeded(
            f"Daily LLM budget (${_DAILY_BUDGET:.2f}) reached - resets at local midnight"
        )
    if status == "warn" and _budget_should_warn():
        logger.warning("LLM spend at/over %s%% of the $%.2f daily budget",
                       _budget_status_pct(), _DAILY_BUDGET)


def _budget_status_pct() -> int:
    from src.llm_budget import today_spend
    if not _DAILY_BUDGET:
        return 0
    return int(today_spend() / _DAILY_BUDGET * 100)


def _account_llm_cost(url: str, model: str, usage: dict) -> None:
    if not _budget_enabled():
        return
    from src.model_context import is_local_endpoint
    if is_local_endpoint(url):
        return
    try:
        _budget_add_spend(_compute_cost(model, usage or {}))
    except Exception as e:
        logger.warning("LLM cost accrual failed: %s", e)
```

Then call `_enforce_budget(url)` once near the start of `stream_llm` (right after `provider = _detect_provider(url)`, `:1708`):

```python
    provider = _detect_provider(url)
    _enforce_budget(url)
```

And call `_account_llm_cost(...)` wherever a `type: usage` event is finalized in the OpenAI-compat path (`:2127`), using the captured `_usage_data`:

```python
                                    _account_llm_cost(url, _actual_model or model, _usage_data)
                                    yield f'data: {json.dumps({"type": "usage", "data": _usage_data})}\n\n'
```

(Anthropic/Ollama usage sites get the same `_account_llm_cost(...)` call in Task 5, once cache fields are added there; Ollama is local-exempt anyway.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_llm_budget_enforcement.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Verify nothing else broke**

Run: `python -m compileall -q src/llm_core.py && python -m pytest tests/test_llm_pricing.py tests/test_llm_budget.py tests/test_llm_budget_enforcement.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/llm_core.py tests/test_llm_budget_enforcement.py
git commit -m "feat(llm): enforce per-day spend cap + accrue cost in stream_llm (metered only)"
```

---

### Task 5: Cache-hit observability

**Files:**
- Modify: `src/llm_core.py` (add a usage-extraction helper; enrich the usage events at the OpenAI-compat site `:2109-2127` and the Anthropic site `:~1989`)
- Test: `tests/test_cache_usage_extraction.py`

**Interfaces:**
- Produces: `_extract_cache_tokens(usage: dict) -> dict` returning `{"cache_hit_tokens": int, "cache_miss_tokens": int}` (zeros when absent), recognizing DeepSeek (`prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`) and Anthropic (`cache_read_input_tokens`/`cache_creation_input_tokens`) field names.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache_usage_extraction.py
from src.llm_core import _extract_cache_tokens

def test_deepseek_fields():
    out = _extract_cache_tokens({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200})
    assert out == {"cache_hit_tokens": 800, "cache_miss_tokens": 200}

def test_anthropic_fields():
    out = _extract_cache_tokens({"cache_read_input_tokens": 500, "cache_creation_input_tokens": 50})
    assert out == {"cache_hit_tokens": 500, "cache_miss_tokens": 50}

def test_absent_fields_zero():
    assert _extract_cache_tokens({"prompt_tokens": 10}) == {"cache_hit_tokens": 0, "cache_miss_tokens": 0}
    assert _extract_cache_tokens({}) == {"cache_hit_tokens": 0, "cache_miss_tokens": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cache_usage_extraction.py -v`
Expected: FAIL (`ImportError`/`AttributeError` for `_extract_cache_tokens`).

- [ ] **Step 3: Write minimal implementation**

Add to `src/llm_core.py`:

```python
def _extract_cache_tokens(usage: dict) -> dict:
    """Normalize provider cache-usage fields to a common shape.
    DeepSeek: prompt_cache_hit_tokens / prompt_cache_miss_tokens.
    Anthropic: cache_read_input_tokens / cache_creation_input_tokens."""
    u = usage or {}
    hit = int(u.get("prompt_cache_hit_tokens") or u.get("cache_read_input_tokens") or 0)
    miss = int(u.get("prompt_cache_miss_tokens") or u.get("cache_creation_input_tokens") or 0)
    return {"cache_hit_tokens": hit, "cache_miss_tokens": miss}
```

In the OpenAI-compat usage block (`:2110`, where `_usage_data` is built), merge the cache fields in before accrual/emit:

```python
                                    _usage_data = {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}
                                    _cache = _extract_cache_tokens(u)
                                    if _cache["cache_hit_tokens"] or _cache["cache_miss_tokens"]:
                                        _usage_data.update(_cache)
                                        logger.info("[llm-cache] model=%s hit=%s miss=%s", _actual_model or model, _cache["cache_hit_tokens"], _cache["cache_miss_tokens"])
```

At the Anthropic usage emit site (`:~1989`), build/extend its usage dict the same way (add `_extract_cache_tokens(usage)` to the emitted `data` and an `_account_llm_cost(url, model, data)` call) so Claude cache reads are visible and costed.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cache_usage_extraction.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify integration compiles + cost uses cache price**

Run: `python -m compileall -q src/llm_core.py && python -m pytest tests/test_cache_usage_extraction.py tests/test_llm_budget_enforcement.py -q`
Expected: PASS. (`compute_cost` now receives `cache_hit_tokens`, so cached input is billed at the discount.)

- [ ] **Step 6: Commit**

```bash
git add src/llm_core.py tests/test_cache_usage_extraction.py
git commit -m "feat(llm): surface + cost provider cache-hit tokens (DeepSeek/Anthropic)"
```

---

### Task 6: DeepSeek cache-prefix stability (verify + regression test)

**Files:**
- Test: `tests/test_agent_tool_prefix_stability.py`
- Modify (only if the test fails): `src/agent_loop.py:2532-2546` (add a deterministic sort)

**Interfaces:**
- Consumes: `FUNCTION_TOOL_SCHEMAS` from `src.agent_tools`.

- [ ] **Step 1: Write the test (pins the cache-prefix invariant)**

```python
# tests/test_agent_tool_prefix_stability.py
from src.agent_tools import FUNCTION_TOOL_SCHEMAS

def _names(schemas):
    return [s.get("function", {}).get("name") for s in schemas]

def test_function_tool_schemas_is_ordered_list():
    # Prefix caching (DeepSeek auto-cache / llama.cpp KV) requires the tools
    # array to serialize identically across rounds. The source must be an
    # order-stable list, and filtering it by a membership set must preserve order.
    assert isinstance(FUNCTION_TOOL_SCHEMAS, list)
    selected = {"manage_memory", "web_search"}
    once = [s for s in FUNCTION_TOOL_SCHEMAS if s.get("function", {}).get("name") in selected]
    twice = [s for s in FUNCTION_TOOL_SCHEMAS if s.get("function", {}).get("name") in selected]
    assert _names(once) == _names(twice)  # deterministic across rebuilds

def test_no_duplicate_tool_names():
    names = _names(FUNCTION_TOOL_SCHEMAS)
    assert len(names) == len(set(names))  # dupes would also perturb the prefix
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_agent_tool_prefix_stability.py -v`
Expected: PASS (filtering an ordered list by a set preserves order — confirms the prefix is already stable). If `selected` names don't exist, swap them for two names present in `FUNCTION_TOOL_SCHEMAS` (check with `python -c "from src.agent_tools import FUNCTION_TOOL_SCHEMAS; print([s['function']['name'] for s in FUNCTION_TOOL_SCHEMAS][:10])"`).

- [ ] **Step 3: Only if Step 2 FAILED — add a deterministic sort**

In `src/agent_loop.py`, immediately after `all_tool_schemas` is assembled (around `:2546`, before the `disabled_tools` filter), pin the order:

```python
            all_tool_schemas = sorted(
                all_tool_schemas,
                key=lambda s: s.get("function", {}).get("name") or "",
            )
```

Add a comment: `# Stable order keeps the DeepSeek/llama.cpp cached prefix byte-identical across rounds.` Re-run Step 2 to confirm PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_agent_tool_prefix_stability.py src/agent_loop.py
git commit -m "test(llm): pin tool-schema prefix stability for cache hits"
```

---

## Final verification

- [ ] Run the full Phase 0 suite:

Run: `python -m pytest tests/test_llm_pricing.py tests/test_daily_llm_spend_model.py tests/test_llm_budget.py tests/test_llm_budget_enforcement.py tests/test_cache_usage_extraction.py tests/test_agent_tool_prefix_stability.py -v`
Expected: all PASS.

- [ ] Confirm disabled-by-default no-op:

Run: `python -c "import src.llm_budget as b; print('enabled=', b.enabled(), 'status=', b.budget_status())"`
Expected: `enabled= 0 ... status= disabled` (no env set).

- [ ] Syntax check touched modules:

Run: `python -m compileall -q src/llm_core.py src/llm_pricing.py src/llm_budget.py core/database.py src/constants.py src/agent_loop.py`
Expected: no output (success).

## Spec coverage check

- §4 prefix-stability → Task 6. §5 cache observability → Task 5. §6.1 pricing → Task 1. §6.2 accumulator → Tasks 2+3. §6.3 config → Task 3. §6.4 enforce/accrue → Task 4. §6.5 scope (global/metered-only/local-exempt) → Tasks 3+4. §6.6 tests → all tasks. §2 non-goals (no fallback router, no per-owner, Hermes separate) → respected (hard-fail only; global; no Hermes coupling).

## Open items deferred to stand-up Phase 1 (not this plan)

- Confirm exact DeepSeek V4 model IDs the endpoint reports and reconcile with `PRICING` keys (spec §9.3).
- 80% warning → ntfy/APNs delivery (currently log-only; spec §9.2). Add when the notification path is wired.
