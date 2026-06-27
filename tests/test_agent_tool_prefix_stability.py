from src.agent_tools import FUNCTION_TOOL_SCHEMAS


def _names(schemas):
    return [s.get("function", {}).get("name") for s in schemas]


def test_tool_schemas_is_an_ordered_list_without_duplicates():
    # Prefix caching (DeepSeek auto-cache / llama.cpp KV) requires the agent's
    # per-round tools array to serialize identically across rounds. The agent
    # filters this list by set membership each round, so the source MUST be an
    # order-stable list (not a set/dict whose iteration order is unspecified)
    # with unique names (a duplicate would also perturb the serialized prefix).
    # (conftest pre-imports the real src.agent_tools, so this live catalog is
    # reliable even under a full-suite run.)
    assert isinstance(FUNCTION_TOOL_SCHEMAS, list)
    names = _names(FUNCTION_TOOL_SCHEMAS)
    assert len(names) == len(set(names))


def test_membership_filter_preserves_source_order():
    # The agent builds each round's tool list by filtering the ordered schema
    # list through a membership set (agent_loop.py). That filter must preserve
    # SOURCE order, never the set's iteration order -- otherwise the cached
    # prefix changes between rounds and every round pays a full re-evaluation.
    source = [{"function": {"name": n}} for n in ["alpha", "beta", "gamma", "delta"]]
    selected = {"delta", "alpha"}  # set iteration order differs from source order
    filtered = _names([s for s in source if s["function"]["name"] in selected])
    assert filtered == ["alpha", "delta"]
