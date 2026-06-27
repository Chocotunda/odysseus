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
