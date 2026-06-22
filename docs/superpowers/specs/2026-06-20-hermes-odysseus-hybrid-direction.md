# Hermes × Odysseus — hybrid direction & Phase 0 setup

**Date:** 2026-06-20
**Status:** Direction approved (brainstorm). Phase 0 = "Stand up Hermes + talk to it."
**Inputs:** 3 research workflows (`wf_097fac70-ae4` dossier, `wf_226cef28-1af` coding/deploy/access, `wf_60868741-fff` hosting/models/privacy). Memory: `hermes-agent-eval`.

> **⚠️ EXPANDED & PARTLY SUPERSEDED (2026-06-22):** this doc captured the early Hermes-hybrid framing. The authoritative, complete record — including the later decisions (native **Tide** as the client, not a web frontend; the fork/B2 conclusion; notes/Obsidian vault; hosting) and all 8 research workflows with sources — is **`docs/ai-context/2026-06-22-life-os-direction-decision-record.md`**. Read that first; this remains valid for the Hermes-specific detail (memory toggles, MCP config, hardening).

## 1. The decision

Adopt **Hermes Agent** (Nous Research) as the **always-on autonomous + phone-reach + coding-orchestration layer**, while **Odysseus stays the canonical graph / second-brain AND the private vault**. This is a *hybrid with a hard boundary*, not a pivot.

Why this (and not "build native", which was the initial steer): the user's privacy bar is **narrow** — only photos/images and a few personal things must stay local; life-admin (weather/email/calendar/tasks) and code are fine in the cloud. And the user specifically wants autonomous *project management/monitoring/bug-fixing*, which Hermes delivers by **orchestrating** Claude Code / Codex on cron — exactly its design. With privacy relaxed for the cloud-bound data, the "renting a server kills privacy" objection dissolves.

## 2. Architecture

```
PHONE ──talk/capture──► HERMES (always-on VPS, cloud models)
                          │  • front desk: weather, email, calendar, tasks, briefings
                          │  • capture buffer (holds while laptop off)
                          │  • autonomous ops: cron → manage/monitor projects,
                          │    fix bugs by delegating to Claude Code / Codex
                          │
        ─────────── HARD BOUNDARY (MCP/API + tana-lynk-style sync) ───────────
                          │
                          ▼
ODYSSEUS (Mac) = graph / second brain + ⛔ PRIVATE VAULT
   • People ↔ Tasks ↔ Notes ↔ Meetings linked graph (the real asset)
   • photos/images + sensitive notes on LOCAL models — never exposed to Hermes
   • holds the iCloud CalDAV/Gmail creds already wired in
```

**The boundary is the design.** Hermes gets life-admin + code; it never gets the photo/image vault, the iCloud photo creds, or the Mac document libraries.

### Key decisions
- **Host:** cheap always-on **VPS now** (Hetzner CX22 ~$5/mo) + a **cloud model (Claude API)**. Mac mini = possible later upgrade.
- **Graph location:** Odysseus graph **stays on the Mac** (with the vault). **Hermes buffers captures while the laptop is off and reconciles into Odysseus when it wakes**, using the `tana-lynk` sync blueprint (id-map / watermark / content-hash echo-gate / tombstones).
- **Coding:** cloud is fine. Delegate to **Claude Code** (privacy-cleaner than Codex-on-ChatGPT-Plus) with `--max-budget-usd` + `--max-turns` caps. Email **read + draft only, never auto-send**.

## 3. Hardening (non-negotiable)

Hermes has a real RCE-class issue (CVE-2026-9366) and a memory-poisoning class. Even with a narrow privacy bar, contain the blast radius:
- `terminal.backend: docker` (the container is the boundary), non-root, **no `/var/run/docker.sock` mount**.
- `terminal.home_mode: profile` (no inherited SSH/cloud/git creds).
- `skills.write_approval: true` + `memory.write_approval: true` (kills silent skill/memory poisoning).
- Explicit messaging **user allowlist** — never `GATEWAY_ALLOW_ALL_USERS`.
- Narrow, mostly `:ro` volumes — a single project dir, never `~/Documents`/`~/Pictures`/`~/.ssh`.
- Budget caps on every delegated coding call.
- Pin **v0.15.1+** (v0.15.0 has a loopback dashboard reload-loop bug).

Resulting blast radius = "life-admin data + code repos (already on GitHub)" — acceptable for the stated priorities.

## 4. Known gaps to solve later
- **No bundled Apple/iCloud CalDAV skill** in Hermes (Google Cal works). Since Odysseus already holds the iCloud CalDAV creds, the calendar bridge likely flows **through Odysseus**, not Hermes directly.
- **Photo management** in Hermes is clipboard-only (no batch local-folder vision) — fine, photos stay on the Mac by design.
- **Spreadsheet-from-calendar** has no first-class skill (falls back to raw openpyxl).
- **Hermes 4 models are poor at tool-calling** → don't use them as the agent model; use Claude/OpenAI.
- **Nous Portal** privacy is bad (training-license ToS + unenforced `data_collection:deny`) → use direct API keys / OpenRouter-with-no-train, skip Portal.

## 5. Roadmap (phased)
- **Phase 0 — Stand up Hermes + talk to it** *(current).* VPS + Docker Hermes + phone pairing (Telegram first) + email/weather/tasks. Feel the 24/7 experience. No Odysseus integration yet.
- **Phase 1 — Capture buffer → Odysseus sync.** Captures fired at Hermes reconcile into the Odysseus graph (tana-lynk machinery). The "capture wall" fix.
- **Phase 2 — Smart routing in Odysseus.** classify note-vs-task, resolve who/what, link into the graph (local models). Hermes is the front door; Odysseus is the brain.
- **Phase 3 — Surfacing in Odysseus** (time/calendar/person-aware; Tide is the prototype/inspiration, free to diverge later) + briefings delivered via Hermes.
- **Phase 4 — Autonomous coding ops.** Hermes cron → monitor repos → delegate fixes to Claude Code, human-confirm merges.
- **Calendar bridge** (iCloud via Odysseus) slotted where it unblocks "talk about my calendar."

Each Odysseus-side phase gets its own spec → plan when reached.
