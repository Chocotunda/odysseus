# Track A — Host, LLM Strategy & Cost — Decision Record + Stand-up Plan

**Date:** 2026-06-26
**Status:** Settled. Outcome of three adversarially-verified research passes this session (host/topology; Apple-stack + move-off-Odysseus; LLM cost drivers + subscription-vs-API). This is the cost/hosting/LLM half of "Track A" (always-on host + Hermes) from `HANDOFF.md`.
**Parent doc:** `docs/ai-context/2026-06-22-life-os-direction-decision-record.md` (the overall life-OS direction; this refines its §3.3 models/privacy and §7 hosting with concrete 2026 numbers).

> **How to read this:** §1 is the answer. §2–§5 are the why (cost, routing, what was rejected). §6 is the Apple-stack finding. §7 is the concrete stand-up plan with cost guardrails. §8 is what's still open. §9 is provenance.

---

## 1. Decisions (settled)

1. **Slim Odysseus — do NOT replace it, do NOT go Apple-canonical, do NOT adopt a PKM backend.** Moving off Odysseus saves ≈ **$0/mo** while costing months of rebuild, because the expensive capabilities the goals require (email IMAP, two-way CalDAV, deep research, the 6-provider uncensored agent loop, RAG, web back-office) are working sunk-cost code Odysseus already provides. Only a *product-scope cut* (dropping email/calendar/research/uncensored-chat) would reopen this.
2. **Brain host = a cheap CPU box, no GPU anywhere. DECIDED 2026-06-26: Hetzner CAX21 VPS for now (~€8.45/mo).** Reach it from Mac + iPhone over **free Tailscale**. The 24GB-RAM constraint disappears once LLM reasoning is remote.
   - **Why VPS over the home N100 in NL:** the N100 is **~€390 one-time here** (not the ~$150 US figure) and NL electricity (~€0.35/kWh) makes a ~12W box ~€3/mo — so the home box's running-cost edge is only ~€5.45/mo, giving a **~6-year break-even** on the hardware (before counting maintenance time + a backup drive). The home box is therefore **not a cost win** in NL; its only real advantage is keeping the private vault local (no privacy split).
   - **"Just start" + reversible:** the data is portable (SQLite + `.md` vault + Chroma), so migrating to a home N100 later is trivial if the privacy-split friction ever justifies the hardware.
   - **Accepted consequence (privacy split):** photos/gallery/personal_docs + sensitive notes stay **Mac-local** (don't point those dirs at the VPS); life-admin + the graph live on the CAX21; uncensored/sensitive inference routes to the Mac's Ollama when it's on.
3. **Capable online LLM = DeepSeek V4-Flash via API (metered + prompt caching).** A self-hosted local LLM on a GPU VPS is the *most expensive* option (adversarially confirmed ~3–20× costlier than the API for a single bursty user) and gives a *weaker* model. "Online + capable + cheap" = API, full stop.
4. **Uncensored = local Mac Ollama (abliterated Qwen3-14B), opportunistic when the Mac is on.** Both hosted DeepSeek and GLM APIs refuse PRC-sensitive content; the abliterated local model stays the only no-refusal path. Keep PII/sensitive prompts local too.
5. **Codex / ChatGPT Pro $100 subscription = interactive coding seat ONLY.** It *cannot* legitimately/reliably back Hermes or the server-side `agent_loop` (ToS bars powering third-party services; the only programmatic path is an undocumented no-SLA endpoint being locked down in 2026; it's a capped 5h-rolling + weekly quota, not flat-metered; and GPT is censored). Drive Codex CLI by hand for coding; that's the endorsed personal use.
6. **GLM Coding Plan = optional second *interactive* coding seat only.** Its ToS *also* explicitly forbids agents/automations/SDK access, so it is not a sanctioned Hermes backend either.
7. **Hermes = its own isolated cheap VM, DeepSeek-backed, defer-then-trial.** The isolated VM is non-negotiable (unpatched public-exploit RCE CVE-2026-9366, vendor unresponsive). Stand it up as a *measured trial* with a hard per-day credit cap before committing to a cadence.
8. **Edge cost-savers (adopt): ntfy → APNs** (free, headless from Linux, native phone delivery) and **route cheap micro-calls to free local Ollama / Apple `fm serve`.**
9. **First build step = prompt caching in `agent_loop.py`** (cache the static system-prompt + tool-schema prefix). Cuts agent-loop input cost ~10–50× and is the precondition that makes a metered Hermes affordable. Pin DeepSeek V4 model IDs (`deepseek-chat`/`deepseek-reasoner` hard-deprecate **2026-07-24**).
10. **deep_research stays, composed with Hermes (not replaced).** Hermes should *call* deep_research as a tool rather than freelance its own crawl loop — keeps untrusted-content crawling+sanitization in the hardened brain (not the RCE-prone agent), keeps cost bounded, and writes a cited artifact into the graph.

---

## 2. Cost picture

All token figures are **modeled, not measured** — the point of the Hermes trial (§7) is to replace them with real numbers.

### Without Hermes (the baseline life-OS)
≈ **$1–25/mo** typical: brain host $1–9 + DeepSeek metered $0–15 + push/backup ~$0 + embeddings local (free). Mac-Ollama-primary keeps most days near the bottom of that range.

### With Hermes
| Line item | LOW | TYPICAL | HEAVY |
|---|---|---|---|
| Brain host (Mac/N100 electricity, or ~$9 VPS) | ~$0–5 | ~$0–9 | ~$0–9 |
| Base LLM (chat/research bursts, DeepSeek metered) | ~$2–10 | ~$15–40 | ~$60–150 |
| **Hermes isolated VM** (mandatory) | ~$4 | ~$5–10 | ~$24 |
| **Hermes token burn** (DeepSeek V4-Flash, cached) | ~$3–10 | ~$15–40 | ~$80–250+ |
| Coding delegation to a premium model | ~$0 | ~$0–20 | ~$100–500+ |
| Push (ntfy/APNs) + backup | ~$0–2 | ~$2–5 | ~$5–10 |
| **TOTAL** | **~$10–35** | **~$50–120** | **~$350–1,000+** |

**What pushes it up, by swing:** (1) the *model* Hermes/agent_loop runs on (DeepSeek→GLM ~10×, →Sonnet/Opus 30–100×); (2) coding-agent delegation to a frontier model; (3) **cache misses** — a sparse Hermes loop lets DeepSeek's best-effort cache (no SLA, hours-to-days TTL, auto-evicted when idle, *not* Hermes-managed for DeepSeek) go cold, flipping the 64K prefix from $0.0028/M back to $0.14/M.

> Adversarial nuance: in a *well-mitigated* regime the mandatory isolated VM ($4–24) can exceed token burn — so "tokens are the biggest line item" only holds in the metered + frequent-wakes case.

---

## 3. LLM strategy & routing

| Workload | Route | Why |
|---|---|---|
| Uncensored / PII / sensitive chat | **Local Ollama (abliterated Qwen3-14B)** | Only no-refusal + zero-egress option; free |
| General agent / `agent_loop` / chat (non-sensitive) | **DeepSeek V4-Flash, metered + caching** | Cheapest sanctioned programmatic path; cache collapses re-sent context |
| Deep research | **DeepSeek V4-Flash** (V4-Pro for hard synthesis) | Input-heavy; cheap input price decisive |
| Hermes 24/7 loop | **DeepSeek V4-Flash, metered, on isolated VM** | Sanctioned for agents; NEVER subscription creds |
| Coding orchestration (you driving) | **$100 ChatGPT Pro/Codex** (optional GLM Lite $18) | Flat-rate interactive coding, endorsed personal use |
| Automated/unattended coding tasks | **DeepSeek V4-Pro** or **GLM-4.7-Flash (free)** | Subscription plans forbid automation |
| Cheap micro-calls (title/summary/classify) | **Local Ollama / Apple `fm serve`** | Negligible value, keep free |

**DeepSeek vs GLM (verified 2026 prices, USD per 1M):** DeepSeek V4-Flash $0.14/$0.28 (cache hit $0.0028), 1M ctx — **~4–16× cheaper** than GLM-5.2 ($1.40/$4.40). DeepSeek V4-Pro leads coding (SWE-bench Verified 80.6%); GLM-5.2 has the stronger general/agentic headline + 1M ctx; gap small enough that price/ToS decide → **DeepSeek for the metered/agentic path** (it's also the only one whose terms permit a custom autonomous backend). Both are PRC-hosted; GLM is the lesser privacy evil (Singapore processing) but on the US Entity List — keep PII local regardless.

---

## 4. Where the LLM costs come from (verified against the code)

Input tokens dominate because every heavy consumer re-sends its context. Ranked:
1. **`src/agent_loop.py`** — `MAX_AGENT_ROUNDS=50` (`src/agent_tools/__init__.py:60`); every round re-sends the full growing message list **+ the full tool schemas** (`src/agent_loop.py:2585`). One task can pay for a large prefix up to 50×.
2. **`src/deep_research.py`** — `max_rounds=8`, each round = planning call + up to 3 page fetches @ 15K chars + extraction + synthesis over last 10 findings.
3. **RAG context-stuffing in chat** — bounded (top-k=5, threshold 0.35, `src/chat_processor.py:52-54`), so smaller.
4. **Hermes** — once added, the new 24/7 baseline (itself an agent_loop-style consumer).
5. **Coding orchestration** — priciest per-task when it runs.

Embeddings (local fastembed all-MiniLM-L6-v2, 384-dim) ≈ $0 — keep local.

**Worked examples:** one deep-research run (~500K in / 20K out) = ~$0.08 on DeepSeek V4-Flash vs ~$3.00 on Opus. One long agent task (15 rounds, ~375K in / 15K out) = ~$0.057 raw → **~$0.016 cached** on DeepSeek vs ~$2.25 on Opus. **Architecture (round-count × context-resend) beats provider choice.**

**Top 3 levers, in order:** (1) **prompt caching the static prefix** (keep it byte-stable so it caches; DeepSeek hit ≈ 1/50 of miss); (2) **local/free routing** of routine calls; (3) **round/depth caps + tool-schema trimming + transcript compaction.**

---

## 5. What was rejected (don't re-litigate)

- **Self-hosted local LLM on a GPU VPS** — $100–400/mo for a reliable 24GB-class GPU you keep ~95% idle; sub-$100 quotes are spot (reclaimed mid-request); a 24GB GPU only fits a quantized 14B that fights Hermes's 64K context for VRAM. ~3–20× costlier than the API for a weaker model. Only justified by a "no prompt ever touches a third party" rule the user doesn't have.
- **Move off Odysseus** (lean custom backend / PKM backend) — ≈ $0/mo savings for a months-long rebuild; Anytype isn't `.md`-canonical + foreign sync (would force a Tide sync rewrite); Khoj is a RAG-assistant, not a graph/vault/email/calendar backend. No off-the-shelf product supplies the full required union.
- **Apple-canonical (CloudKit)** — a non-Apple brain can't reach the CloudKit private DB headlessly (only an interactive, rotating, ≤2-week ckWebAuthToken; server-to-server keys are public-DB-only). Incompatible with an always-on non-Apple brain + web back-office + agent.
- **Apple on-device/PCC model as *the* LLM** — guardrails can't be disabled (no uncensored), embeddings run only on Apple hardware (can't back the server RAG), 4K on-device / 32K PCC context too small for the agent loop, and PCC isn't a server you can call. (Two of the three *old* rejection reasons — text-only, no embeddings — are now stale; the decisive guardrails reason holds.)
- **Codex / GLM subscriptions as a Hermes/agent_loop backend** — both ToS forbid automation/agents/SDK use; Codex's programmatic path is undocumented/no-SLA/locked-down + capped quota. Interactive coding seats only.

---

## 6. Apple stack — what it CAN do for us (2026)

Adopt at the edges, not the core:
- **ntfy → APNs** — token `.p8` JWT (ES256) signs headlessly from the Linux brain, HTTP/2 to `api.push.apple.com`; free within the $99/yr dev program already paid for Tide; native lock-screen/Focus delivery; no self-hosted service. Pattern: push = wake-up signal, `/changes` REST feed = truth. Keep a thin ntfy/web-push path for the browser back-office only.
- **`fm serve`** (new 2026 local OpenAI-compatible server) + Apple Foundation Models — wire in as a provider through the existing OpenAI seam (zero architecture change); route trivial non-sensitive tasks (summaries, titles, tags, classification, intent routing) here for free, on Mac + capable iOS hardware, offline.
- **iCloud** — non-factor (graph is MBs; photos stay local); optional vault mirror only, never canonical (dataless-file trap).

---

## 7. Stand-up plan (phased, with cost guardrails baked in)

Goal: **put both Odysseus and Hermes online cheaply and measure real cost** before committing to anything. Reversible at each step.

### Phase 0 — Cost guardrails first (do before anything always-on)
- Implement **prompt caching** on the static prefix in `src/agent_loop.py` (system prompt + tool schemas kept byte-stable). Pin DeepSeek V4 model IDs (deprecation 2026-07-24). *(This is the one real code task; worth its own spec/plan when built.)*
- Add a **hard per-day credit cap** + spend logging on the DeepSeek endpoint so the trial can't run away.

### Phase 1 — Capable LLM online (no host change yet)
- Add **DeepSeek V4-Flash** as a `ModelEndpoint` (OpenAI-compatible base URL + key) via the existing provider layer. OpenRouter is already a first-class provider in `src/llm_core.py` (host detection + auth headers + classification), so this is **config, not code** — a `ModelEndpoint` row (`base_url`, encrypted `api_key`, pin the model id). No env-var fallback for the key — it must live on the row.
- **TWO ACCESS LANES for the SAME model (decided 2026-06-27):**
  - **TEST lane = OpenRouter free** (`deepseek/deepseek-v4-flash:free`, base `https://openrouter.ai/api/v1`). **$0**, 1M ctx. Use it for Phase-1 integration verification (tool/function-calling round-trips, the spend-cap plumbing, cache observability) at zero spend. Caps: **50 req/day** (→ **1,000/day** after a one-time **$10** top-up); free routing **auto-failovers across providers** so you can't pin one backend and **prompt-cache continuity breaks** (cache is per-provider). Privacy: OpenRouter doesn't train, no prompt logging by default — but keep PII local regardless (varying providers).
  - **PROD lane = paid metered** for the always-on VPS brain + Hermes. Free can't sustain production: one `agent_loop` task fires up to **50 req** (`MAX_AGENT_ROUNDS=50`), so 50/day ≈ one task, 1,000/day ≈ ~20 tasks; a 24/7 Hermes loop blows through it. Prefer **DeepSeek-direct** (`api.deepseek.com`) for prod — real prompt caching + the only ToS that permits an autonomous backend; OpenRouter-paid is the fallback (adds a middleman + routing variability).
- Verify tool/function-calling round-trips against `FUNCTION_TOOL_SCHEMAS`; run a small live soak. **Measure** real chat/research token cost for a week (on the PROD lane — free won't reflect cached cost). Keep Mac Ollama as the uncensored/sensitive route.
- **Model id note:** `deepseek/deepseek-v4-flash` is already V4-pinned (sidesteps the `deepseek-chat`/`deepseek-reasoner` **2026-07-24** deprecation).

### Phase 2 — Brain online (always-on, CPU-only)
- Stand up the **slimmed** Odysseus on the **Hetzner CAX21 VPS** (decided). Pin all Docker image tags; **tailnet-only bind** (no public 0.0.0.0). Drop the ChromaDB container (in-process fastembed); run SearXNG on demand.
- Join Mac + iPhone to the tailnet; **repoint Tide** and prove the always-on sync path with the Mac asleep.
- Keep the private subset (photos/gallery/personal_docs + sensitive notes) **off** a cloud host (or use the N100 so there's no split).

### Phase 3 — Edge savers
- Swap **ntfy → APNs** (provision `.p8`, add APNs sender at the notification path, register Tide device tokens). Highest-ROI, $0, independent of host.
- Wire **`fm serve`** + local Ollama as the free tier for cheap micro-calls.

### Phase 4 — Hermes trial (measured)
- Stand Hermes up on **its own isolated cheap VM** (Hetzner CX23 ~$4): default-deny egress, explicit allow-list of Odysseus endpoints it may call, non-root, no docker.sock, hardening flags (`terminal.backend=docker`, `home_mode=profile`, write_approval, user allowlist).
- Back it on **DeepSeek V4-Flash** (never subscription creds). Have it **call deep_research as a tool**, not freelance crawling.
- Run with the per-day cap; **measure** real per-wake token burn and cadence cost before deciding to keep it / tune the cadence.

### Phase 5 (later, gated) — decommission the frozen Tauri wrapper
Once backend lifecycle is re-homed on the host AND Tide capture parity holds, salvage `backend.rs` into a tiny launcher and retire the wrapper. (See parent decision record §7/§10.)

---

## 8. Open decisions for the user

1. ~~**Brain host:** home N100 vs Hetzner CAX21.~~ **RESOLVED 2026-06-26 → Hetzner CAX21 VPS for now** (NL economics killed the home-box cost case: €390 hardware + ~€3/mo power ≈ 6-yr break-even). Revisit a home N100 only if the privacy-split friction becomes worth the hardware.
2. **Slim depth:** OK to delete cookbook/gallery/documents/signatures/TTS-STT/model-admin UI (~6K+ LOC) and demote the web UI to admin-only, keeping Tide as the daily client?
3. **Hermes cadence:** event-driven vs polling, and how aggressive — to be set *after* the measured trial.
4. **DeepSeek jurisdiction:** DeepSeek-direct (cheapest, PRC) vs via Venice/OpenRouter zero-retention proxy for life-admin data.

---

## 9. Provenance

Three background research workflows this session (each: parallel web-grounded/codebase agents → adversarial verification → synthesis):
- **Host/topology + LLM + Hermes placement** — run `wf_fbbdd34a-d7f`.
- **Apple on-device/PCC + move-off-Odysseus** — run `wf_7f27435c-62e`.
- **LLM cost drivers + subscription-vs-API** — run `wf_1c171b4b-d4c`.

Key sources (verified): DeepSeek pricing + KV cache (`api-docs.deepseek.com`), Z.ai/GLM pricing + devpack ToS (`docs.z.ai`, `z.ai/subscribe`), OpenAI Codex pricing/auth/usage-policies + ChatGPT Pro $100 tier (VentureBeat, `developers.openai.com/codex`, `help.openai.com`), CVE-2026-9366 (SentinelOne), Hetzner pricing, Tailscale pricing, Apple Foundation Models / PCC / APNs / CloudKit Web Services (`developer.apple.com`, `machinelearning.apple.com`), Venice AI docs (`docs.venice.ai`). Full URL lists live in the workflow transcripts under `subagents/workflows/`.

> Caveats to carry forward: all token/cost figures are **modeled, not measured**; Hermes's "always-on cron" cadence is **inferred** (its docs describe a server-resident interactive tool, not a documented 24/7 loop); verify the exact Codex $100 quota + DeepSeek off-peak discount at use time.
