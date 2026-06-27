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
