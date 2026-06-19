"""AI extracts candidate action items; Python clamps owner + resolves dates and
degrades safely. The model is never trusted for correctness."""
from src import planner_ai


def test_coerce_clamps_owner_and_resolves_date():
    raw = [
        {"title": "Send Q3 deck", "owner": "Wiggert", "due_hint": "2026-06-23"},
        {"title": "Ping HR", "owner": "someone_else", "due_hint": None},
        {"title": "", "owner": "me", "due_hint": None},   # empty -> dropped
    ]
    out = planner_ai.coerce_action_items(raw, person_name="Wiggert", today="2026-06-19", items_already=[])
    assert out == [
        {"title": "Send Q3 deck", "owner": "Wiggert", "due_date": "2026-06-23"},
        {"title": "Ping HR", "owner": None, "due_date": None},
    ]


def test_coerce_drops_titles_already_present():
    raw = [{"title": "Send deck", "owner": "me", "due_hint": None}]
    out = planner_ai.coerce_action_items(raw, "Wiggert", "2026-06-19", items_already=["send deck"])
    assert out == []


def test_coerce_handles_non_list():
    assert planner_ai.coerce_action_items("garbage", "W", "2026-06-19", []) == []
