import importlib

from src.llm_pricing import compute_cost, price_for
import src.llm_pricing


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
    assert price_for("us.anthropic.claude-opus-4-8") == (5.0, 25.0, 0.5)


def test_invalid_json_ignored(monkeypatch):
    """Invalid JSON is ignored, seed table still intact"""
    monkeypatch.setenv("ODYSSEUS_LLM_PRICING_JSON", "{not valid json}")
    importlib.reload(src.llm_pricing)
    assert src.llm_pricing.price_for("deepseek-v4-flash") == (0.14, 0.28, 0.0028)


def test_empty_env_falls_back(monkeypatch):
    """Empty env value falls back to seed table"""
    monkeypatch.setenv("ODYSSEUS_LLM_PRICING_JSON", "")
    importlib.reload(src.llm_pricing)
    assert src.llm_pricing.price_for("deepseek-v4-flash") is not None


def test_valid_override_applies(monkeypatch):
    """Valid override applies to PRICING dict"""
    monkeypatch.setenv("ODYSSEUS_LLM_PRICING_JSON", '{"my-test-model": [1.0, 2.0, 0.1]}')
    importlib.reload(src.llm_pricing)
    assert src.llm_pricing.price_for("my-test-model") == (1.0, 2.0, 0.1)
