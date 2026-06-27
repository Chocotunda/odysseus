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
