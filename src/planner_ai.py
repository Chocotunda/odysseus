"""Local-AI helpers for the Planner.

Golden rule (see docs/ai-context/planner-feature-design.md §7): the LLM does
*language*, Python does *math/dates/placement*. Every AI call is one-shot,
single-purpose, and its output is passed through ``coerce_capture`` — never
trusted for enum or project-id correctness. AI failure must degrade to the raw
user input, never block capture.
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

ALLOWED_PRIORITIES = {"none", "normal", "important", "urgent"}


def coerce_capture(raw: Any, valid_project_ids: Iterable[str]) -> Dict[str, Any]:
    """Validate-and-coerce raw model output into safe planner fields.

    `format: json` only guarantees valid JSON, not correct fields — so clamp the
    priority enum, drop a project id the user doesn't own, and reject a
    non-integer estimate. Anything unparseable falls back to a safe default.
    """
    valid = set(valid_project_ids or ())
    if not isinstance(raw, dict):
        raw = {}

    title = str(raw.get("title") or "").strip()

    pr = raw.get("priority")
    priority = pr if pr in ALLOWED_PRIORITIES else "normal"

    est = raw.get("estimate_minutes")
    try:
        est = int(est)
        if est <= 0:
            est = None
    except (TypeError, ValueError):
        est = None

    proj = raw.get("project_id") or raw.get("project")
    project_id = proj if proj in valid else None

    due = raw.get("due_date")
    due_date = due.strip() if isinstance(due, str) and due.strip() else None

    return {
        "title": title,
        "priority": priority,
        "estimate_minutes": est,
        "project_id": project_id,
        "due_date": due_date,
    }


def _extract_json(text: str) -> Dict[str, Any]:
    """Pull the first JSON object out of a model response (tolerates code fences
    and surrounding prose)."""
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else None
    if not candidate:
        return {}
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


async def parse_capture(
    text: str,
    projects: Optional[List[Dict[str, str]]] = None,
    owner: Optional[str] = None,
) -> Dict[str, Any]:
    """One-shot NL → structured fields on the resident/default model.

    Returns the raw parsed dict (the caller coerces it). Raises on transport/
    config failure so the caller can degrade to the raw item.

    NOTE (Phase 1): the model's date *phrase* is returned as-is and only kept if
    it is already YYYY-MM-DD; resolving fuzzy phrases ("fri", "next week") in
    Python via dateutil/user-tz is a Phase 2 refinement.
    """
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async

    # Capture/planning deliberately use the small "utility" lane (a fast 4B),
    # NOT the chat model — capture should stay cheap and simple, and this keeps
    # it off the larger chat model the user may have selected. resolve_endpoint
    # falls back to the default model if no utility model is configured.
    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        raise RuntimeError("no model endpoint configured for planner capture")

    today = datetime.now().strftime("%Y-%m-%d")
    proj_lines = "\n".join(
        f'- id="{p.get("id")}" name="{p.get("name")}"' for p in (projects or [])[:15]
    ) or "(none)"

    system = (
        "You convert a short natural-language task into JSON. Today is "
        f"{today}. Respond with ONLY a JSON object, no prose, with keys:\n"
        '  "title": concise task title (string),\n'
        '  "priority": one of none|normal|important|urgent,\n'
        '  "estimate_minutes": integer minutes if stated, else null,\n'
        '  "due_date": "YYYY-MM-DD" only if an explicit calendar date is given, else null,\n'
        '  "project_id": the id of the best-matching project below, else null.\n'
        f"Projects:\n{proj_lines}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": text},
    ]
    resp = await llm_call_async(
        url, model, messages,
        temperature=0.0, max_tokens=300, headers=headers,
        prompt_type="planner_capture",
    )
    return _extract_json(resp)
