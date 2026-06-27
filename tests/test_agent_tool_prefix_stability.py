from src.agent_tools import FUNCTION_TOOL_SCHEMAS


def _names(schemas):
    return [s.get("function", {}).get("name") for s in schemas]


def test_function_tool_schemas_is_ordered_list():
    # Prefix caching (DeepSeek auto-cache / llama.cpp KV) requires the tools
    # array to serialize identically across rounds. The source must be an
    # order-stable list, and filtering it by a membership set must preserve
    # the source order (NOT the set's iteration order).
    assert isinstance(FUNCTION_TOOL_SCHEMAS, list)
    selected = {"manage_memory", "web_search"}
    filtered = [s for s in FUNCTION_TOOL_SCHEMAS if s.get("function", {}).get("name") in selected]
    # web_search is defined before manage_memory in the source list; the
    # set-filter must preserve that order, not reorder by the set.
    assert _names(filtered) == ["web_search", "manage_memory"]


def test_no_duplicate_tool_names():
    names = _names(FUNCTION_TOOL_SCHEMAS)
    assert len(names) == len(set(names))  # dupes would also perturb the prefix
