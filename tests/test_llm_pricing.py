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
