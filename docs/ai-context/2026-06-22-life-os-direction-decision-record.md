# Life-OS Direction — Decision Record, Findings & Plan

**Date:** 2026-06-22
**Status:** Direction settled end-to-end; first build identified (not yet started).
**Scope:** Consolidates a long multi-session deliberation about whether/how to involve **Hermes Agent**, whether to **hard-fork** Odysseus, where **notes** live, what the **frontend/client** should be, **iOS**, **hosting**, and **Tide's** role. Supersedes nothing but pulls together: the memories (`hermes-agent-eval`, `fork-strategy-decision`, `client-native-tide-decision`, `management-hub-build`, `planner-workspace-vision`, `tana-lynk-integration-hub`), the prior spec (`docs/superpowers/specs/2026-06-20-hermes-odysseus-hybrid-direction.md`), and 8 research workflows (provenance in §9).

> **How to read this:** §1 is the answer. §2 is the why. §3 is the full deliberation (every angle, including rejected options and reversals). §4 is what the research found (with honest confidence). §5–6 are the conclusion and plan. §7 is what's still open. §8 is what already shipped. §9 is sources.

---

## 1. Executive summary

We are building a **personal "life OS" / chief-of-staff**: frictionless capture → an LLM routes and auto-links it into a connected People↔Tasks↔Notes↔Meetings graph → it surfaces tasks *with their context* at the right time → reminds/helps → learns. The graph is essential but **effortless** — the LLM builds and traverses the links so the user never hand-maintains them.

**Settled architecture ("one brain, many surfaces"):**
- **Odysseus (Python/FastAPI)** = the **brain**: the canonical connected graph + the `.md` note vault + an **MCP/API surface** + the **single writer**. Also the **web "back-office"** (email, calendar, deep research, agent/model config, full graph admin) — its existing vanilla frontend stays **as-is**.
- **Tide (SwiftUI)** = the **polished daily client**, macOS + iOS, on the Odysseus API. Capture, tasks, notes/markdown editing, the board, the Do-Next surfacing. **This is where the user lives daily.**
- **Hermes (or a swappable agent)** = optional **always-on orchestrator + multi-channel reach**, calling Odysseus via MCP. Never the source of truth.
- **Hosting** = a cheap always-on box (VPS ~$5-15/mo, or a ~$200 home mini-PC) for the brain; **Tailscale** to the phone. (Mac mini for always-on *local-uncensored* deferred.)
- **Sync** = the user's `tana-lynk` machinery (id-map / content-hash / watermark / tombstone).

**Headline conclusions:**
- **Don't hard-fork** upstream Odysseus → stay a tracking fork via a thin **agnostic core (B2)**; keep disconnect as a *triggered* future option. *(Shipped this session — see §8.)*
- **Don't adopt Hermes as the center**; it's optional, swappable, and must never hold canonical data. Using it as an orchestrator-over-external-state is the *mainstream* pattern, not fighting the grain.
- **Don't rebuild the Odysseus web frontend, and don't build a React island.** Make **native Tide** the polished client instead — prepare for the perf/polish ceiling now rather than rebuild a third time.
- **Notes = portable `.md` vault**, `.md`-canonical for the body, graph-canonical in the DB; **single-writer** keeps sync trivial.
- **⚠️ Hermes's specifics failed verification three times** (fake-citation docs, non-resolving domains). The architecture is deliberately agent-agnostic so this doesn't matter — but **verify Hermes hands-on before investing in it specifically.**

---

## 2. The end goal (product vision)

The user is a manager with a demanding job, a busy personal life, and intensive **caretaking** responsibilities. The need is to **reduce cognitive load** — not a to-do list, not a chatbot. Stated most precisely by the user:

> "I mostly want frictionless capture. But also when it's time to focus and do the tasks or meetings, I want to easily see tasks and their context smartly… an LLM might make it much easier to know and find the right graph connection. And things like reminding me and helping me do stuff would be nice."

**The core loop:** capture (anywhere, low-friction) → **LLM routes** (note vs task) → **LLM auto-links** into the graph (who/what it's about) → **surfaces with smart context** at focus-time → **reminds/coaches** → **learns**.

**The key insight that resolved the "is the graph worth it?" debate:** the graph *is* essential, but the user's fear was the **manual-linking tax** (the Tana/Obsidian setup-tax). The LLM removes that tax — it's the *linker* and the *context-finder*, so the graph is a byproduct of capture, not work. Graph = the structure that makes smart context possible; LLM = what makes the graph effortless.

**Caretaking/safety-critical items** (medication, welfare check-ins): **assistive only** — drafts, reminders, surfacing. The human stays the safety net. Never an autonomous AI as the sole mechanism, on any current tool.

---

## 3. The deliberation — angles considered

This section records *what was discussed and rejected*, so decisions aren't re-litigated.

### 3.1 Hermes Agent — adopt / companion / complement / reject
- **Considered:** (a) adopt Hermes as the center; (b) Hermes as a companion while building Odysseus; (c) Hermes as a complement (orchestrator over Odysseus via MCP); (d) build native, mine its ideas, don't adopt; (e) use its built-in notes/tasks/memory.
- **Findings:** Hermes is an **orchestrator that delegates coding** to Claude Code/Codex (not a coder); its own models (Hermes 4) are weak at tool-calling; its memory is a tiny flat scratchpad (~2.2K-char `MEMORY.md`), **not** a graph; it has real security debt (RCE-class CVE, memory-poisoning, audit findings); and **its specifics repeatedly failed verification** (§4).
- **Conclusion:** Hermes is **optional and swappable**. If used, it's the **always-on orchestrator + multi-channel reach** over Odysseus-via-MCP, never the data store. **Don't use its built-in notes/tasks**; **keep** a small *preference/working* memory (agent-managed is useful) but route durable data to Odysseus and gate it (poisoning risk); `~/.hermes/` stays disposable scratch. Verified (§4) that this orchestrator-over-external-state pattern is mainstream, not fighting the grain.
- **Rejected:** adopting Hermes as the brain/source-of-truth (sacrifices the graph for flat memory + inherits security debt + roadmap dependency); using its built-in task/note features (competing brain).

### 3.2 Fork strategy — hard-fork / B2 agnostic-core / fresh app
- **Considered:** (A) stay a tracking fork; (B1) hard-fork/disconnect and own the snapshot; (B2) extract a thin agnostic core, rent the commodity; (fresh) rebuild a focused app.
- **Findings (adversarially challenged, §4):** disconnecting = becoming sole maintainer of ~430K lines of credential-touching code + ~195 transitive deps, solo, no budget; empirical solo-fork security-patch lag is bad. The hub's coupling to upstream was *not* trivial (entangled in upstream's hottest files). A "fresh app" was disqualified by the user's own keep-list (they want most of Odysseus's substance: email, calendar, contacts, documents, STT).
- **Conclusion:** **B2 — stay a tracking fork via a thin agnostic core**, keep disconnect as a *triggered* future option (explicit triggers in §7). **Shipped this session (§8).**
- **Rejected:** hard-fork (the trap), fresh rebuild (rebuilds what the user wants to keep).

### 3.3 Models & privacy
- **Considered:** local uncensored fleet vs cloud models; Grok for explicit media; the provider-privacy landscape; Nous Portal.
- **Findings (§4):** privacy/capability/cost is a pick-two triangle. Privacy ranking: local > Anthropic API > OpenAI API/Vertex > OpenRouter(no-train) > Nous Portal (avoid) > ChatGPT-consumer/Codex-on-Plus > DeepSeek hosted (China). DeepSeek *open-weights self-hosted* = fine.
- **Conclusion:** the brain's **text smarts can use cloud (Claude)** per-task via a seam (user's privacy bar = photos/sensitive, not text). **Explicit/private media → local abliterated vision model** (uncensored *and* private), never cloud/Grok. **Coding = Claude Code** on a paid Anthropic plan (privacy-cleaner than Codex-on-Plus); orthogonal to Odysseus.
- **Rejected:** Grok for explicit media (unverified policy + violates the photos-private rule); Nous Portal (training-license ToS + unenforced deny).

### 3.4 Notes & the Obsidian vault
- **Considered:** notes in the DB vs as markdown files; Obsidian-the-app as editor; multi-writer sync.
- **Findings (§4):** an Obsidian vault is just a folder of `.md`; viable as shared storage. The hard part is multi-writer sync. Obsidian-as-editor is fine but the user chose to edit in their own app.
- **Conclusion:** notes are **portable `.md` files** in an Obsidian-compatible vault (frontmatter + wikilinks) = a *storage format*, not a mandate to use the Obsidian app. **`.md` canonical for body; DB canonical for graph/metadata** (mirrored to frontmatter). **Single writer = Odysseus** (the agent writes via MCP) → collapses the dangerous multi-writer sync problem.
- **Rejected:** DB-only proprietary storage (not portable); letting the agent + the user + Odysseus all write files directly (echo loops).

### 3.5 Frontend — web vs native *(this one reversed twice; recorded honestly)*
- **Considered, in order:** (1) keep the vanilla JS frontend; (2) build a modern **React island** for the life-OS UI; (3) **native Tide (SwiftUI)**; Electron was raised and ruled out.
- **The reversal:** we first leaned **React island** (to get a good editor + avoid rebuilding the whole vanilla frontend). Then the user surfaced **WebView perf + polish + macOS/iOS-consistency** concerns, which re-weighted toward **native**.
- **Findings:** the earlier WebView perf pain was largely **self-inflicted** (animated embers canvas + box-shadow) and fixable; a clean web UI performs fine for productivity. **But** for *polished consistency across macOS + iOS*, **native SwiftUI is the unambiguous ceiling** — and Tide already *is* this form-factor. **Electron is worse than Tauri** (heavier webview, no iOS) → ruled out.
- **Conclusion:** **native Tide is the client.** User's reasoning: "we'd hit this ceiling eventually anyway — prepare for it now instead of rebuilding again." This also lets us **stop touching the web frontend entirely** (it stays the back-office, as-is) — dodging the rebuild question rather than answering it.
- **Rejected:** Electron (categorically worse here); rebuilding the whole vanilla frontend (the trap); the React island (superseded by native — kept as a documented faster-but-lower-ceiling alternative).

### 3.6 iOS & cross-platform sharing
- **Considered:** share UI components web↔native (React Native, Capacitor/Tauri-wrap) vs share only the API.
- **Findings:** the iOS features the user wants most (transcription, widgets, Siri, background) are **native-only** — they don't come from shared web UI. UI-sharing across web↔native is leaky/low-ROI; **API/data sharing is the high-ROI win.**
- **Conclusion:** **share the API + `.md` vault, not UI components.** Native SwiftUI (one codebase) is the most consistent for macOS↔iOS anyway. (This is now moot in the chosen path, since the client is native Tide for both Apple platforms.)
- **Rejected:** contorting the stack to force web↔native UI sharing.

### 3.7 Hosting
- **Considered:** Mac mini (local-uncensored, ~$600+); cheap VPS (~$5-15/mo); home mini-PC (~$200); the requirement that **Odysseus be always-on** so the agent + phone work when the Mac is off.
- **Findings:** Odysseus + the agent are *light* (run on a cheap box) — only the **local uncensored fleet** needs an expensive host. On a cheap host, the fleet is sidelined to desk-only and the always-on brain thinks on cloud models (acceptable per the privacy bar).
- **Conclusion:** **cheap VPS or ~$200 home mini-PC** for the always-on brain; **Tailscale** for private phone access (no public exposure); Mac mini deferred. **Hosting is a deployment step — it does not block building.**
- **Open:** the medical/caretaking data tier — decide explicitly whether it lives on the host or stays Mac-local (§7).

### 3.8 Tide's role
- **Considered:** abandon / reference-only / repurpose as the client.
- **Findings (3-agent Tide investigation, §4):** Tide is a mature, agent-built (4 days, 184 commits, 141 tests) realization of *this exact form-factor* — "your day's command center, one brain, two surfaces" — with a clean separable `TideCore` brain (SurfacingEngine, CaptureDirectiveParser, fractional Ordinals, DayMath) that is a near-perfect **blueprint + tested logic reference**, and `TideTask` ↔ `PlanItem` map cleanly.
- **Conclusion:** Tide = **the polished daily client** (chosen, §3.5). `TideCore`'s SurfacingEngine + CaptureDirectiveParser **stay in Tide** (client-side, already built + tested — not ported to Python). Odysseus does the smart server-side graph-linking.

### 3.9 CloudKit-canonical vs self-hosted-sync — the sync architecture *(resolved 2026-06-22, 4-agent research)*
- **Considered:** make Apple **CloudKit the canonical store** (free, built-in offline/auth/E2E, zero server) vs **Odysseus canonical** with a hand-rolled client sync; and whether to run *both*.
- **Findings (§4):** CloudKit-canonical is **disqualified for a non-Apple brain** on three independent grounds — (1) a Linux/Python server **cannot read/write a user's CloudKit *private* DB** (server-to-server keys are public-DB-only; the private DB needs interactive 30-min Apple-ID web auth — a hard, never-relaxed policy wall), so the agent / web back-office / `.md`-vault could never reach the canonical data; (2) **SwiftData+CloudKit** is bug-prone through 2026 (incl. an iOS 26.4 update that broke `CKSubscription` for "countless apps"); (3) running CloudKit **and** a self-hosted sync over one store is a known **split-brain** anti-pattern (silent data loss). CloudKit-canonical is right **only** for Apple-only apps with no web/Linux surface (e.g. Bear) — which contradicts the life-OS vision.
- **Conclusion:** **Odysseus canonical, CloudKit dropped.** Tide is an **online-first** client with a **local cache + optimistic writes (+ small retry buffer)**; **durable offline-write is deferred** to a later phase — validated **SOLID-WITH-CAVEATS** (an *extension, not a rewrite*) *iff* the day-1 invariants hold: **client-UUIDs + server upsert-on-PK**, idempotency-key = the UUID, an **authoritative (not opportunistic) local store**, soft-delete tombstones, additive-only migrations, and an optimistic UI that never shows "saved" before ACK. **Cache layer = GRDB / Point-Free SQLiteData**, *not* SwiftData (SwiftData-local's unfixed `@ModelActor`→`@Query` refresh bug + iOS-26.1 store-corruption risk hit the sync-cache design directly). **Hand-rolled** sync — no Swift library fits a self-hosted REST/SQLite backend (PowerSync/ElectricSQL/Zero need Postgres; Realm/Atlas Device Sync is EOL Sep-2025) — but the contract stays **PowerSync-compatible** for a future Postgres move. Precedent: **Linear / Figma / Notion / Things / Todoist** all use local-store + server-ordered cursor + idempotent mutation queue and deliberately **avoid CRDTs**; LWW is fine single-user.

---

## 4. Key findings from research (with confidence + sources)

Confidence is flagged because **Hermes-specific facts were repeatedly unreliable** this session — a critical finding in itself.

| Finding | Confidence | Source (run id / named) |
|---|---|---|
| Hermes is a real MIT runtime, distinct from Hermes LLMs; orchestrator that delegates coding | Medium (repo resolved; some claims unverified) | dossier `wf_097fac70`; deploy `wf_226cef28` |
| Hermes hype inflated: "224B daily tokens", "140k stars/3mo", "NVIDIA partnership", "40% self-improvement" overstated/unverified | High (verified as unreliable) | `wf_097fac70` adversarial pass |
| Hermes security debt (RCE-class CVE, memory-poisoning, audit findings) | Medium (directionally solid, version-sensitive) | `wf_097fac70`, `wf_226cef28` |
| **Hermes specifics keep failing verification** (fake citations, non-resolving `hermes-agent.ai`/`.nousresearch.com` domains; "Nous ships models, not a documented agent product") | High | `wf_ac973fcc`, `wf_efeb728d` skeptic passes |
| Orchestrator + external system-of-record via MCP is the **mainstream** pattern (Anthropic Managed Agents, LangGraph, MCP, Airtable/Workday "agent system of record") | High, evidence-based | `wf_efeb728d` |
| Disconnecting from upstream = owning ~430K LOC + ~195 deps solo; bad solo-fork security-patch record | High (repo-measured + arxiv 2404.17964 Neovim/Vim study; Glazkov/Desaulniers fork-cost) | `wf_80a780e4` |
| Privacy ranking; cloud-model retention/jurisdiction (incl. NYT consumer-ChatGPT retention order; DeepSeek China) | Medium-High | `wf_60868741` |
| Hosting: agent is light (cheap VPS ok); local fleet needs GPU/Apple-Silicon; Tailscale for phone | High | `wf_226cef28`, `wf_60868741` |
| Native required for the iOS features wanted (PWA can't do transcription/widgets/Siri/background) | High | `wf_f1a06816` |
| On-device meeting transcription strong on macOS (system audio), capped on iOS (can't record other apps) | High | `wf_f1a06816` |
| Obsidian vault viable as shared store; hybrid (`.md` body + DB graph); single-writer collapses sync risk | Medium-High (no production precedent for the SQL+agent+mobile trio → prototype) | `wf_ac973fcc` |
| Tide: mature agent-built form-factor; `TideCore` portable blueprint; `TideTask`↔`PlanItem` map | High (read against the repo) | 3-agent Tide investigation |
| B2 extraction is runtime-correct, complete, no blockers | High (adversarially verified on a fresh DB) | `wf_0a01bd84` |
| **CloudKit private DB unreachable by a non-Apple server** (S2S key = public-DB-only; private DB needs interactive 30-min web auth) | High | sync research 2026-06-22 agent A — Apple archived CloudKit Web Services docs; Apple Dev Forums thread/84754 (2017-22, unresolved) |
| SwiftData+CloudKit immature thru 2026 (custom-migration breakage; iOS 26.4 broke `CKSubscription`); two-writer split-brain is a known anti-pattern | High | sync research agent B — Apple Dev Forums; mjtsai; Notion/Linear eng |
| SwiftData-**local** `@ModelActor`→`@Query` refresh bug **unfixed since Jul 2024**; iOS 26.1 array-attr store corruption → use **GRDB/SQLiteData** as the cache | High | sync research agent C — Apple Dev Forums 759364 / 806161 / 761522; Point-Free SQLiteData |
| **No Swift sync library fits a self-hosted REST/SQLite backend** (PowerSync/Electric/Zero=Postgres; Realm/Atlas Device Sync EOL Sep-2025) → hand-roll; stay PowerSync-compatible | High | sync research agent D — vendor docs/changelogs |
| Online-first + local cache + optimistic writes, **offline deferred = SOLID-WITH-CAVEATS** (extension not rewrite *iff* invariants held); LWW fine single-user; **`?since=` cursor should be a monotonic per-owner sequence**, not raw `updated_at` | High / Med-High (cursor) | sync research agent A1 + app survey — Kleppmann SE-Radio #716 (Apr 2026); Notion eng blog; Todoist Sync API; Linear sync engine; Figma blog |

---

## 5. The settled architecture (conclusion)

```
            PHONE ──talk/capture──►  HERMES (optional, swappable agent)
                                       │  always-on orchestrator + reach
                                       │  (cloud models OK; memory = scratch + prefs)
        ───────────── MCP (read-mostly, confirm-on-write) ──────────────
                                       ▼
   ODYSSEUS (Python) = THE BRAIN  ── canonical graph (DB) + .md vault + MCP/API + sole writer
        • web BACK-OFFICE: email, calendar, deep research, agent/model config, graph admin
        • smart server-side LLM linking (cloud or local via seam)
        • vanilla frontend kept AS-IS (NOT rebuilt; NO React island)
                                       ▲
                       (same API, host-agnostic)
                                       │
   TIDE (SwiftUI) = THE POLISHED DAILY CLIENT (macOS + iOS)
        • capture, tasks, notes/markdown editing, the board, Do-Next surfacing
        • SurfacingEngine + CaptureDirectiveParser stay in TideCore (client-side)
        • local GRDB/SQLiteData cache (NOT SwiftData); ONLINE-FIRST + optimistic
          writes (durable offline DEFERRED) → Odysseus canonical; CloudKit dropped

   HOST: cheap always-on box (VPS / ~$200 mini-PC) for the brain; Tailscale to phone.
   SYNC: tana-lynk machinery (id-map / content-hash / watermark / tombstone).
   PRIVATE: photos + (decide) caretaking/medical stay Mac-local, never on the host.
```

---

## 6. The plan — the "Tide-as-Odysseus-client" program

Decomposed into shippable sub-projects (XL overall, ~3-5 months per the native-client scoping). Each gets its own spec → plan → TDD build.

- **1a — Odysseus client API readiness** — ✅ **SHIPPED** (merged+pushed `dev @ 027e4a5`, 6 commits, 68 tests + live smoke): `people:`/`areas:` token scopes + `tide` profile; PATCH + server-computed reorder (TideCore `Ordinal.between`); `?since=` delta + soft-delete tombstones; LWW by `updated_at`. *Deferred from 1a:* MCP-tools exposure (its own spec); People/Areas incremental sync (slice 3). Spec/plan: `docs/superpowers/{specs,plans}/2026-06-22-odysseus-client-api-readiness-slice-1a*`; memory `client-api-readiness-slice-1a`.
- **1b — Tide as an online-first Odysseus client** *(decomposed; see §3.9)* — **1b-0 (Odysseus):** contract amendments — accept client-supplied `id` on create (upsert-on-PK / idempotent) + add a **monotonic per-owner `seq`** column, switch `?since=` to the `seq` cursor (`updated_at` stays the LWW tie-breaker). **1b-1 (Tide):** persistence **SwiftData → GRDB/SQLiteData**, behavior-preserving (141 tests green), CloudKit dropped. **1b-2 (Tide):** the sync engine — `OdysseusClient` (URLSession + DTOs) + `SyncCoordinator` (pull `?since=seq` upsert-by-UUID + tombstone-delete; optimistic writes + idempotency + retry buffer; `NWPathMonitor`) + `SyncConfig` (URL in UserDefaults, token in Keychain) + a Settings screen. Task layer first (TideTask↔PlanItem) = the "80% moment".
- **2 — Tide markdown notes editing → the `.md` vault** (native editor; `.md`-canonical).
- **3 — People / Notes / Meetings surfaces in Tide** (net-new SwiftUI; the graph surfaces; this is when People/Areas get their own `?since=`/soft-delete).
- **Parallel/when-ready — Hosting**: deploy the brain to a cheap always-on box + Tailscale (does not block building).
- **Later/optional — Agent layer**: Hermes (or a thin bot) as the always-on orchestrator over the API/MCP — *after* verifying Hermes hands-on.
- **Meeting transcription** (a Tide-native feature; macOS-strong) slots in when wanted.

**Recommended next action:** spec **slice 1b** (then build 1b-0 → 1b-1 → 1b-2).

---

## 7. Open questions / deferred / honest caveats

- **Verify Hermes hands-on** before investing in it specifically (its specifics keep failing verification). The architecture is agent-agnostic, so this is contained.
- **Medical/caretaking data tier:** decide explicitly host-stored vs Mac-local-only (more sensitive than the "photos" privacy bar).
- **Uncensored local fleet is sidelined** to desk-only on a cheap host (price of no Mac mini). Accepted.
- **Mobile vault sync** is the weak link if notes are ever edited outside Tide; single-writer (Tide-only editing) keeps it safe.
- **Obsidian/SQL/agent/mobile sync trio has no production precedent** → prototype small before committing.
- **Disconnect triggers** (when hard-forking flips to correct): a real >½-day merge conflict in `core/database.py`/`app.py`; upstream ships a native tasks/people/planner surface (collides with our tables); merge cadence slips >1 month twice; upstream abandoned; AGPL relicense.
- **The Tauri `odysseus-app` wrapper is slated for retirement** (Tide replaces it as the macOS app).
- **Make the upstream security-scan recurring** (cron line / launchd) — `scripts/hub_upstream_security_scan.py`.
- **CloudKit was evaluated and rejected** (§3.9) — do not relitigate "why not just use CloudKit?" without a *new* constraint (the blocker is structural: a non-Apple brain can't reach the CloudKit private DB).
- **`?since=` cursor amendment owed to 1a:** the merged 1a contract uses raw `updated_at` as the cursor; 1b-0 must switch it to a **monotonic per-owner `seq`** (`updated_at` stays the LWW tie-breaker) before more clients depend on it.
- **Cache = GRDB/SQLiteData, not SwiftData** — driven by SwiftData-local's unfixed `@ModelActor`→`@Query` refresh bug + OS-level store-corruption risk; revisit only if Apple fixes those *and* there's a real reason.
- **LWW silently clobbers concurrent unrelated field edits** (e.g. "done offline" overwriting a title edited elsewhere) — acceptable single-user; flag the fields that are unsafe to clobber if/when collaboration is ever added.

---

## 8. What shipped this session (B2 agnostic-core extraction)

Merged to `dev` and **pushed to origin** (`3e5f1bb`); 0 behind upstream (pulled #4602); adversarially verified (no blockers).
- `core/hub_models.py` — the 5 hub models + the `planned_start` migration, extracted from `core/database.py` (footprint **+160 → +11/−1**).
- `src/hub_llm.py` — facade over `resolve_endpoint`/`llm_call_async` (so `planner_ai` doesn't reach into the 52-commit `llm_core.py`).
- `src/hub_calendar.py` — read-seam owning all `CalendarCal`/`CalendarEvent` access (`events_by_uids`, `expand_event_in_window`); `today.py`/`area_routes.py`/`people_routes.py` route through it.
- `scripts/hub_upstream_security_scan.py` — filtered upstream security-commit feed (watermark in `.git/`).
- 22 import sites rewired; 65 hub tests green throughout; the verification follow-up (`3e5f1bb`) closed the seam bypasses.

---

## 9. Sources & research provenance

Eight workflows + a 3-agent codebase investigation. Full structured outputs are in the session's workflow transcripts (run ids below). **Honesty note:** web sources for Hermes-the-product were repeatedly unreliable (AI-generated docs with fake citations, non-resolving domains); the general-architecture sources (Anthropic, LangGraph, MCP, Apple docs, Obsidian docs, the arxiv fork study) were solid. Treat Hermes-specific claims as *unverified until checked hands-on*.

| Run id | Topic | Notable sources |
|---|---|---|
| `wf_097fac70-ae4` | Hermes dossier + claim verification | GitHub repo, OpenRouter rankings, NVIDIA RTX-AI-Garage blog (co-marketing, not partnership), security audit/CVE refs |
| `wf_226cef28-1af` | Hermes coding/deploy/access | Hermes docs (orchestrator-delegates-to-Claude-Code), Docker/sandboxing |
| `wf_60868741-fff` | Hosting / models / privacy | Hetzner pricing, Anthropic/OpenAI/DeepSeek policies, NYT consumer-retention order |
| `wf_f1a06816-1e2` | Native-client scoping | iOS PWA capability limits, Apple SpeechAnalyzer/ScreenCaptureKit, Tide gap analysis (file:line), sync design |
| `wf_80a780e4-d31` | Fork-disconnect challenge | repo-measured 430K LOC, arxiv 2404.17964 (Neovim/Vim patch-lag), Glazkov/Desaulniers fork-cost |
| `wf_0a01bd84-ed5` | B2 extraction verification | fresh-DB runtime check; codebase |
| `wf_efeb728d-17c` | "Fighting the grain?" | Anthropic Managed Agents, LangGraph checkpointers, MCP, Airtable/Workday "agent system of record" |
| `wf_ac973fcc-332` | Obsidian vault viability | Obsidian docs, Local REST API plugin, obsidian-mcp servers, org-roam/MarkdownDB prior art |
| 3-agent Explore | Tide history / form-factor / architecture | the Tide repo (`docs/superpowers/specs|plans|research`, `Sources/TideCore`, git log) |
| sync research 2026-06-22 (6 background agents + verifier, adversarial) | CloudKit-canonical feasibility/tradeoffs; Swift sync-library landscape; SwiftData-as-local-cache soundness; online-first-deferred-offline pattern + app survey | Apple archived CloudKit Web Services docs; Apple Dev Forums 84754/759364/806161/761522; PowerSync/ElectricSQL/Zero/Realm vendor docs; Point-Free SQLiteData; Kleppmann SE-Radio #716; Notion eng blog; Todoist Sync API; Linear sync engine; Figma blog; Ink&Switch local-first essay. **Honesty note:** per-app *internals* (Things/Bear/TickTick/Sunsama) are medium-confidence; the CloudKit private-DB wall + the SwiftData bugs are high-confidence (Apple's own docs/forums) |

**Related internal docs/memories:** `docs/superpowers/specs/2026-06-20-hermes-odysseus-hybrid-direction.md`; memories `client-native-tide-decision`, `fork-strategy-decision`, `hermes-agent-eval`, `management-hub-build`, `planner-workspace-vision`, `tana-lynk-integration-hub`, `odysseus-app-companion`; `docs/ai-context/HANDOFF.md`.
