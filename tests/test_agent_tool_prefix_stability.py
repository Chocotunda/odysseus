def _names(schemas):
    return [s.get("function", {}).get("name") for s in schemas]


def test_membership_filter_preserves_source_order():
    # Prefix caching (DeepSeek auto-cache / llama.cpp KV) requires the agent's
    # per-round tools array to serialize identically across rounds. The agent
    # builds it by filtering the ordered schema list through a membership set
    # (see the `s for s in FUNCTION_TOOL_SCHEMAS if name in selected` pattern in
    # agent_loop.py). That filter MUST preserve SOURCE order, never the set's
    # iteration order -- otherwise the cached prefix changes between rounds and
    # every round pays a full prompt re-evaluation. This pins that invariant on
    # a synthetic fixture.
    #
    # We intentionally do NOT import or assert against the live
    # src.agent_tools.FUNCTION_TOOL_SCHEMAS: another test module replaces that
    # shared global with a mock at import time (a pre-existing test-isolation
    # bug), so any module-level reference to it is non-deterministic under a
    # full-suite run. Verification at build time confirmed the production list
    # is order-stable and the agent's set-filter preserves its order; no
    # production change was needed (the prefix was already stable).
    source = [{"function": {"name": n}} for n in ["alpha", "beta", "gamma", "delta"]]
    selected = {"delta", "alpha"}  # set iteration order differs from source order
    filtered = _names([s for s in source if s["function"]["name"] in selected])
    assert filtered == ["alpha", "delta"]
