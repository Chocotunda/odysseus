"""Thin adapter over upstream's LLM plumbing — the single seam hub code uses to
reach the model layer.

`src/llm_core.py` and `src/endpoint_resolver.py` are among upstream's most-churned
files. By importing through this facade (instead of reaching into them directly),
hub modules depend on ONE place: if upstream renames/moves `llm_call_async` or
`resolve_endpoint`, only this file needs fixing — not every hub call site.
(B2 / agnostic-core boundary; see
docs/superpowers/specs/2026-06-20-hermes-odysseus-hybrid-direction.md)
"""
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import llm_call_async

__all__ = ["resolve_endpoint", "llm_call_async"]
