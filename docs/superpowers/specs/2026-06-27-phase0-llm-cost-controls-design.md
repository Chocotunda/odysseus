# Phase 0 — LLM Cost Controls (prefix-stability + cache observability + per-day spend cap)

**Date:** 2026-06-27
**Status:** Design approved; ready for implementation plan.
**Context:** First build step of the Track A stand-up plan (`docs/ai-context/2026-06-26-track-a-host-llm-cost-decision-record.md` §7, Phase 0). These are the cost guardrails that must exist *before* moving the brain to the VPS / adding a metered DeepSeek endpoint / running the Hermes trial — so metered spend is provably bounded and the trial can be **measured, not modeled**.

---

## 1. Goal & success criteria

Make Odysseus's own metered LLM spend **cheap (provably cache-hitting), observable, and safely bounded** — disabled-by-default so nothing changes until a budget is configured.

Success:
- The DeepSeek/OpenAI-compatible agent path keeps a **byte-stable system+tools prefix across rounds** so server-side prefix caching keeps hitting (verified by a regression test).
- Every metered call's **cache-hit/miss token counts are surfaced** (SSE usage event + logs).
- A configured **per-day USD budget hard-stops** further metered calls with a clear error, warns at a threshold first, **survives a process restart**, and **never counts or blocks free/local endpoints**.
- Full unit-test coverage with **no live API calls required**.

## 2. Non-goals (explicit — later slices)

- Mac-liveness fallback router / auto route-to-local (separate slice; this cap only *hard-fails*, it does not fall back).
- Per-owner budgets (global-only for now; designed so per-owner can be added without rework).
- Hermes's own LLM spend cap — Hermes calls DeepSeek directly off its own VM and **bypasses `llm_core` entirely**; it is bounded separately via DeepSeek's account spending limit + Hermes config in Phase 4.
- Model-tiering/routing of micro-calls to local.

## 3. Architecture — one chokepoint

All three components attach at/around **`stream_llm`** (`src/llm_core.py:1696`) — the single async function every LLM call flows through (`stream_llm_with_fallback`, `:2301`, wraps it across candidate endpoints). The free/local exemption uses the canonical **`is_local_endpoint(url)`** (`src/model_context`, already imported at `src/llm_core.py:643-644`): if an endpoint is local, it is exempt from cost accounting **and** the cap.

## 4. Component A — DeepSeek prefix-stability (verify + guard)

**Why:** DeepSeek (detected as a generic `openai` provider) auto-caches the request prefix server-side at ~1/50 the input price. The lever is keeping the system+tools prefix **byte-identical** round-to-round for a task so the cache keeps hitting.

**Already in place (do not regress):** base system prompt is memoized (`_cached_base_prompt`, `src/agent_loop.py:991-1031`); the per-minute datetime is deliberately kept **out** of the system prompt and injected as a separate late user message (`:1043-1051`, issue #2927); llama.cpp slot affinity (`_apply_local_cache_affinity`, `src/llm_core.py:647`).

**Work:**
1. **Verify** the outgoing tool-schema array is deterministic across rounds for a given task: the build at `src/agent_loop.py:2520-2560` runs *inside* the round loop; confirm (a) the selected tool set (`_relevant_tools`, chosen at prep ~`:2251`) does not change between rounds, and (b) `base_schemas + mcp_schemas` produce a **stable order**.
2. If non-deterministic, add a **stable ordering** (e.g. sort by tool name) at the assembly point and a pinning comment documenting the cache-prefix invariant.
3. Confirm the system message content sent on the OpenAI-compat path is byte-stable across rounds (no per-round mutation of the system role).

**Risk:** low — behaviour-preserving (reordering tool list / no change if already stable).

**Test:** a regression test that drives two consecutive rounds of the same task and asserts the serialized (system message + tools array) prefix is byte-identical.

## 5. Component B — cache observability

**Why:** turn the trial from modeled to measured.

**Work:** at the OpenAI-compat usage parse (`src/llm_core.py:~2109-2127`), additionally read DeepSeek's `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`; at the Anthropic usage parse (`:~1975-1989`), read `cache_read_input_tokens` / `cache_creation_input_tokens`. Include these in the emitted `type: usage` SSE event payload (new optional keys, e.g. `cache_hit_tokens`, `cache_miss_tokens`) and log them per call at INFO. Backward-compatible: keys absent when the provider doesn't report them.

**Test:** unit-test the usage-extraction helper against representative DeepSeek and Anthropic usage objects (with and without cache fields).

## 6. Component C — per-day spend cap

### 6.1 Pricing
New `src/llm_pricing.py`:
- `PRICING: dict[str, tuple[float, float, float]]` = `{model_id: (input_per_1M, output_per_1M, cache_hit_input_per_1M)}` seeded with DeepSeek V4-Flash (`0.14/0.28/0.0028`), V4-Pro (`0.435/0.87/0.003625`), and current Anthropic models (Opus/Sonnet/Haiku). Values **env-overridable** (e.g. a JSON env var) so prices can be corrected without a code change.
- `compute_cost(model, usage) -> float`: uses cache-hit price for the hit-token portion, miss/standard price for the rest, output price for completion tokens. **Unknown model → return 0.0** (never false-cap). Helper is pure and unit-tested.

### 6.2 Accumulator (persistent)
- New lightweight DB model in `core/database.py`: `DailyLLMSpend(day: str PK, cost_usd: float, updated_at)`. `day` = **local day** string via `src/user_time` (fallback UTC). Persisted so a runaway is still remembered across a restart.
- Access helpers (sync, mirroring existing patterns): `add_spend(cost)`, `today_spend() -> float`. Single-row-per-day upsert.

### 6.3 Config (`src/constants.py` + env)
- `DAILY_LLM_BUDGET_USD` — the cap; **unset/0 ⇒ feature fully disabled** (default).
- `DAILY_LLM_WARN_PCT` — default `80`.
- Pricing-override env (optional JSON).

### 6.4 Behaviour in `stream_llm`
- **Pre-call** (only when budget enabled AND `not is_local_endpoint(url)`): if `today_spend() >= budget` → raise a clear, catchable error surfaced to the caller as `"Daily LLM budget ($X) reached — resets at local midnight"` (**hard-fail**, decided option (a)); the agent/chat/research loop stops on it. If `today_spend() >= budget * warn_pct/100` and no warning emitted today → emit a **one-shot** warning (INFO log + a notification via the existing notify path), tracked so it fires once/day.
- **Post-call** (when usage is known, at the existing end-of-stream usage handling): `add_spend(compute_cost(model, usage))`.
- Local/free endpoints skip both branches entirely.

### 6.5 Scope
Global, metered-only, all call paths (chat + `agent_loop` + `deep_research`) — as decided. Local endpoints exempt, so uncensored/local chat is never blocked.

### 6.6 Tests
- `compute_cost`: standard, full-cache-hit, partial-hit, unknown-model→0, output-only.
- Accumulator: add/read, single-row-per-day upsert, **daily rollover** (day key changes), restart persistence (reopen DB → total retained).
- Threshold logic: under cap (passes), ≥80% (warns once, still passes), ≥100% (raises), local endpoint (never raises regardless of spend), budget unset (no-op).

## 7. Error/edge handling

- Budget disabled (default) ⇒ zero behavioural change; no DB writes for cost unless enabled. *(Decision point for the plan: still record spend when only observability is wanted? Default: only when budget enabled, to keep disabled-path a true no-op — see §9.)*
- Pricing lookup failures / missing usage ⇒ cost 0, log once; never crash a stream.
- The cap is checked best-effort around streaming; a single in-flight call may slightly overshoot the cap (acceptable for a guardrail) — we stop *subsequent* calls, which bounds the 50-round agent loop.

## 8. Files touched

- `src/llm_core.py` — cap pre/post hooks in `stream_llm`; cache-field extraction in usage parse.
- `src/llm_pricing.py` — **new**: pricing table + `compute_cost`.
- `core/database.py` — **new** `DailyLLMSpend` model + access helpers.
- `src/constants.py` — new config constants.
- `src/agent_loop.py` — prefix-stability guard (only if §4 verify shows instability).
- `tests/` — unit tests per §4/§5/§6.6.

## 9. Open questions for the plan

1. **Spend recording when the cap is disabled:** record cost for observability even with no budget set, or keep the disabled path a strict no-op? (Lean: no-op when disabled; observability/logging in Component B is separate and always on.)
2. **Notification channel** for the 80% warning: reuse ntfy now (APNs later per Track A), or log-only for Phase 0? (Lean: log + ntfy if configured, else log-only.)
3. **DeepSeek model IDs:** pin `deepseek-chat`/`deepseek-reasoner` (hard-deprecate 2026-07-24) — confirm the exact V4 IDs to seed in `PRICING` when the endpoint is added in stand-up Phase 1.
