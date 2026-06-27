"""Unit tests for Anthropic cost normalisation (Fix 1).

Verifies that compute_cost, when given a cost dict built the way
stream_llm now builds _anth_cost_usage, bills:
  - cache_read tokens at the cheaper cache_hit price
  - (fresh_input + cache_creation) tokens at the full input price
  - output tokens at the output price

No live API or network calls are made.
"""
from src.llm_pricing import compute_cost, PRICING


def test_anthropic_cache_tokens_billed_correctly():
    """fresh=500, cache_read=20000, cache_creation=1000, output=100.

    Normalized cost dict:
      input_tokens  = 500 + 20000 + 1000 = 21500
      output_tokens = 100
      cache_hit_tokens = 20000

    Expected billing:
      miss_tokens  = 21500 - 20000 = 1500  (fresh + creation) at in_price
      hit_tokens   = 20000                  at cache_price
      output_tokens= 100                    at out_price
    """
    model = "claude-sonnet-4-6"
    in_price, out_price, cache_price = PRICING[model]

    fresh = 500
    cache_read = 20000
    cache_creation = 1000
    output = 100

    cost_usage = {
        "input_tokens": fresh + cache_read + cache_creation,  # 21500
        "output_tokens": output,
        "cache_hit_tokens": cache_read,                        # 20000
    }
    cost = compute_cost(model, cost_usage)

    miss_tok = fresh + cache_creation  # 1500
    expected = (miss_tok * in_price + cache_read * cache_price + output * out_price) / 1_000_000
    assert abs(cost - expected) < 1e-12, f"Expected {expected}, got {cost}"


def test_all_cache_read_billed_at_cache_rate():
    """When entire input is a cache hit the cost uses cache_price only."""
    model = "claude-opus-4-8"
    in_price, out_price, cache_price = PRICING[model]

    total = 100_000
    cost_usage = {
        "input_tokens": total,
        "output_tokens": 0,
        "cache_hit_tokens": total,
    }
    cost = compute_cost(model, cost_usage)
    expected = total * cache_price / 1_000_000
    assert abs(cost - expected) < 1e-12, f"Expected {expected}, got {cost}"


def test_no_cache_tokens_billed_at_full_input_rate():
    """Without any cache tokens the cost is pure input + output."""
    model = "claude-haiku-4-5"
    in_price, out_price, cache_price = PRICING[model]

    inp = 1_000_000
    out = 500_000
    cost_usage = {"input_tokens": inp, "output_tokens": out}
    cost = compute_cost(model, cost_usage)
    expected = (inp * in_price + out * out_price) / 1_000_000
    assert abs(cost - expected) < 1e-12, f"Expected {expected}, got {cost}"
