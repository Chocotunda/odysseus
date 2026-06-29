# VPS Options Research — Odysseus brain host (Track A, Phase 2)

**Date:** 2026-06-29
**Status:** Research only (no code; Phase-2 brainstorm paused while another agent works the branch).
**Context:** Pressure-tests the host choice from `2026-06-26-track-a-host-llm-cost-decision-record.md` (§1.2 picked Hetzner CAX21) against the providers the user has heard floated. **Outcome: CAX21 confirmed — nothing surfaced beats it for this workload.**

## The workload (what we're sizing for)
Single-user, **always-on** Odysseus brain as the existing `docker-compose` stack:
`odysseus` (FastAPI + SQLite + `.md` vault) + `chromadb` + `searxng` + `ntfy`. Lift-and-shift, model **A** (VPS = sole canonical brain; Mac = Tide client + opportunistic Ollama provider over Tailscale; photos/gallery/personal_docs stay Mac-local, unserved for now).

**Resource profile:** memory-hungry pieces are ChromaDB + the fastembed (ONNX) embeddings + searxng. Realistically wants **~8 GB RAM** (4 GB is tight running the full stack + OS), modest CPU, small-but-growing disk (SQLite + vault + Chroma), EU location (NL user → low latency + GDPR), reachable over **Tailscale** (free).

## Comparison

| Provider / plan | Arch | vCPU / RAM / disk | True steady €/mo | Verdict |
|---|---|---|---|---|
| **Hetzner CAX21** ⭐ | ARM Ampere Altra | 4 / 8 GB / 80 GB | **~€8.49** (€7.99 + €0.50 IPv4), flat, no lock-in | **PICK.** Best Docker/API/snapshot tooling, ISO 27001 + EU/GDPR, 20 TB transfer, energy-efficient ARM. |
| Hetzner CAX11 | ARM Ampere Altra | 2 / 4 GB / 40 GB | ~€4.49 | Floor/fallback. Too tight for the *full* stack; viable only if we slim first. Resize CAX11→CAX21 is live (RAM/CPU up; disk only grows). |
| Hetzner CX33 (x86) | Intel/AMD | 4 / 8 GB / 80 GB | ~€8+ | x86 fallback **only if** an arm64 image is missing (see pre-flight). |
| **Hostinger** KVM 2 | x86 KVM | 2 / 8 GB | **~€7 intro → ~€14 renewal** | Teaser pricing: low rate needs 1–2 yr prepay, **renews ~2×**; only 2 vCPU at 8 GB; weak infra tooling (shared-host company doing VPS). Loses steady-state. |
| **DigitalOcean** Basic | x86 | 4 / 8 GB | **~€44** ($48) | ❌ ~5× CAX21 for identical specs, less bundled bandwidth (4 TB). Paying for managed DB/k8s/docs a single-user brain doesn't need. |
| **Oracle Cloud** Always Free | ARM A1 | up to 4 / 24 GB | €0 | ❌ **Now a trap** — June 2026 cut 4 OCPU/24 GB → 2 OCPU/12 GB, chronic "out of capacity" provisioning, no escalation, over-limit instances shut down. Unfit as a reliability-critical brain (fine as a throwaway experiment box). |
| **Netcup** VPS/RS | ARM / x86 | more RAM/€ | ~€4–6 | Best raw value-per-euro (RS G12 "budget king"), but **ARM sold out**, more signup friction, weaker API/Docker ecosystem. Not now. |
| **Contabo** | x86 | cheap, lots of RAM | ~€4.50 | ❌ F-grade VPSBenchmarks web-performance scores. RAM-heavy but slow. |

## What changed since the 2026-06-26 decision record
1. **Hetzner raised prices 3× in 2026** (latest 15 June). The **CAX ARM line was affected but only mildly** — CAX21 = €7.99 + €0.50 IPv4 ≈ **€8.49/mo**, essentially the €8.45 the record already quoted. The severe hike hit the *dedicated CCX* line (≈tripled), which we don't use. **Budget holds.**
2. **Oracle's free tier was gutted** (4 OCPU/24 GB → 2 OCPU/12 GB) + capacity/reliability problems → disqualified as an always-on brain.

## Two concrete knobs
- **IPv6-only saves €0.50/mo** (IPv6 free; IPv4 is the €0.50 add-on). **Keep the IPv4** anyway — DeepSeek/OpenRouter, Docker registries, and apt mirrors aren't all reliably reachable IPv6-only without a NAT64 gateway; €0.50/mo is cheap outbound insurance. Tailscale works either way.
- **ARM pre-flight (the one real check before committing):** confirm all four compose images have **arm64** variants — `odysseus` (our Dockerfile, base `python:3.11` is multi-arch), `chromadb`, `searxng`, `ntfy` (all multi-arch), and fastembed's ONNX runtime (has ARM builds). Expected clean; if anything is x86-only, fall back to Hetzner **CX33** (x86, ~same price).

## Bottom line
**Hetzner CAX21 (ARM, 8 GB, ~€8.49/mo) stays the pick.** Flat month-to-month (no prepay, no renewal jump), more vCPU than Hostinger at the same RAM, a fraction of DigitalOcean, and far more reliable than Oracle-free. Start at CAX21 for the full-stack lift-and-shift; CAX11 is the cheap floor only if we slim first.

## Sources
- Hetzner CAX pricing (post-hike): https://www.bitdoze.com/hetzner-cloud-cost-optimized-plans/
- Hetzner IPv4 pricing docs: https://docs.hetzner.com/general/infrastructure-and-availability/ipv4-pricing/
- Hetzner 15 June 2026 price adjustment: https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
- Oracle free-tier 2026 cut: https://terminalbytes.com/oracle-cloud-free-tier-changes-2026/
- Oracle free-tier limits/capacity: https://space-node.net/blog/oracle-cloud-always-free-limits-2026
- Netcup vs Hetzner 2026: http://netcupvoucher.com/blog/netcup-vs-hetzner-budget-servers-2026
- Contabo vs Hetzner benchmarks: https://www.vpsbenchmarks.com/compare/contabo_vs_hetzner
- Hostinger VPS pricing 2026: https://smarthostfinder.com/hostinger-vps-pricing/
- Hostinger renewal costs: https://hostadvice.com/hosting-company/hostinger-reviews/vps-pricing/
- DigitalOcean vs Hetzner: https://betterstack.com/community/guides/web-servers/digitalocean-vs-hetzner/
