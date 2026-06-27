"""Tests for _extract_cache_tokens — Task 5: cache-hit observability."""
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
