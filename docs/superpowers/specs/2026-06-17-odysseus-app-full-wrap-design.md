# Odysseus App — full-workspace native wrap (v1: thin wrap)

**Date:** 2026-06-17
**Status:** Design approved, pre-implementation
**Repo for implementation:** `/Users/kganpat/Projects/odysseus-app` (separate sibling repo, pushed to `Chocotunda/odysseus-app`)
**Related:** `odysseus/docs/ai-context/odysseus-app-design.md` (the shipped quick-capture companion, "MVP A"), HANDOFF.md "Odysseus App" section.

## Goal

Turn the existing menubar quick-capture companion into a **full native macOS app for all of Odysseus**: a real window that hosts the entire web workspace (chat, email, calendar, research, notes, planner, cookbook, …) so the interconnected surfaces live together in one native app instead of a browser tab — while keeping the global-hotkey capture overlay exactly as it works today.

This is the **end-state the planner-only window (the old "MVP B") was a stepping stone toward** — we skip the planner-only scope and wrap the whole app, because the value (the connected Tasks↔Calendar↔Email↔Notes graph in one surface) requires the whole workspace, and a WebView pointed at `/` *is* the whole workspace at near-zero per-feature cost.

## Scope

This is **v1 — the thin wrap.** Backend lifecycle management is **v2** (designed-for, not built — see "v2 seam" below).

**In scope (v1):**
- A new `main` Tauri window that loads the live Odysseus web UI at `http://127.0.0.1:7860/` (server URL from existing config).
- Switch the app to a **Regular** activation policy (dock icon + Cmd-Tab always).
- Login handled by Odysseus's own `/login` page; the session cookie persists in WKWebView across launches.
- A local "server unreachable" screen with a Retry button (this is the v2 seam).
- Tray gains an "Open Odysseus" item; window lifecycle (open on launch, close-hides, explicit quit).
- Security: the remote `main` window gets **no** Tauri IPC/globals; only the local `overlay`/`settings` windows do.

**Out of scope (deferred):**
- **v2:** the app launches/manages the backend (uvicorn + chroma) — never Ollama. The unreachable screen becomes a "starting Odysseus…" state.
- Autostart-on-login, configurable-hotkey UI, Apple Developer-signed/notarized build, Windows/Linux builds.

## Approach

Add a third window to the existing Tauri v2 app that loads an **external URL** (the live Odysseus server) — no bundling, no proxy, no per-feature native work. The window *is* the workspace, exactly like a dedicated browser instance. Chosen over: (a) a planner-only window (too narrow given the interconnected vision), (b) rebuilding features as native panels (multi-month rewrite for ~zero gain), (c) bundling the web assets (loses live backend, pointless for a localhost app).

## Architecture

### Three windows, two trust zones

| Window | Content | Tauri IPC? | Notes |
|---|---|---|---|
| `overlay` | local `ui/overlay.html` | **yes** | unchanged — NSPanel capture bar, Cmd+Shift+Space |
| `settings` | local `ui/settings.html` | **yes** | unchanged — server URL + token config |
| `main` | **remote** `http://127.0.0.1:7860/` | **NO** | new — the full workspace, sandboxed like a browser tab |

**IPC isolation is a security boundary.** The mechanism is the **capability allowlist** (`capabilities/default.json`), which already scopes IPC to `["overlay","settings"]`. Tauri v2 has no per-window `withGlobalTauri` toggle, and the no-build-step overlay/settings JS depends on the global, so it stays on — but a window **not listed in any capability gets zero permitted commands**. The remote `main` window is therefore never added to capabilities; any `invoke` from it is rejected. To keep `main`'s label out of capabilities entirely, the server-down screen uses a **separate local `gate` window** (which *is* capability-bearing) rather than loading into `main`. A remote page (the Odysseus UI, which renders untrusted LLM/crawled content) thus has no usable Tauri surface.

### Activation policy

Switch from `ActivationPolicy::Accessory` (menubar-only) to **`ActivationPolicy::Regular`** — dock icon and Cmd-Tab always present, like a normal Mac app.

> ⚠️ **Risk:** the overlay's keyboard-focus fix (global hotkey → NSPanel becomes key → WKWebView first responder) was tuned under `Accessory`. NSPanels can still become key under `Regular`, but app-activation semantics differ. This is the highest-risk verification item — must be re-tested, may need adjustment. It is NOT expected to block the design.

### Window lifecycle

- `main` **opens on launch** (it's a real app now).
- Tray menu gains an **"Open Odysseus"** item (above "Capture…") to reopen it after close.
- **Closing** `main` (red button) **hides it; the app keeps running** for the tray + capture overlay. Quit is explicit (Cmd-Q or tray → Quit).
- **No separate hotkey** for `main` in v1 (open via dock/tray). Cmd+Shift+Space remains the capture hotkey.

### What stays untouched

The capture overlay (NSPanel, park-don't-orderOut dismiss, Bearer-token capture path), the settings window, the tray, and the bundle id `com.krishen.odysseus-app`. The overlay and the full window are independent surfaces.

## Auth / session

The `main` window loads `/`; with no session cookie, Odysseus redirects to `/login` (cookie name `odysseus_session`, httponly, samesite=lax). The user logs in once via the real login page. **WKWebView persists the cookie in its per-app data store across quit/relaunch**, so `/login` only reappears after explicit logout or cookie expiry. The app stores **no credentials** — the existing `/login` flow (including any 2FA) is reused untouched.

> Verification item: confirm Tauri v2 uses a **persistent** (not ephemeral/incognito) WebView data store by default. Default is persistent; we prove it with a quit/relaunch test.

The capture overlay's Bearer-token path (Keychain, `todos:write`) is **independent** and unchanged. The app thus has two intentional auth mechanisms: a session cookie for the full window, a scoped token for instant capture.

## Server-down handling (the v2 seam)

Before loading `main`, a Rust reachability check pings the server (`GET {server_url}/`).
- **Up** → load the remote URL.
- **Down** → load a tiny **local** `ui/unreachable.html`: Odysseus dark theme, `Fira Code`, monochrome inline SVG, **no emoji**; message *"Odysseus isn't running at `<url>`"* + a **Retry** button that re-checks and, on success, navigates `main` to the remote URL.

In **v1** the user starts the backend themselves, then clicks Retry. In **v2** this same check becomes "…so I'll start it for you": the unreachable screen turns into a "starting Odysseus…" state while the app spawns uvicorn + chroma. The seam exists from day one — no rework.

## Components (files)

In `odysseus-app/`:
- `src-tauri/tauri.conf.json` — flip `withGlobalTauri` off globally; declare the `main` window (external URL, Regular-app chrome, visible on launch); keep `overlay`/`settings`; capability/permission config so only local windows get IPC.
- `src-tauri/src/lib.rs` — `ActivationPolicy::Regular`; create/show `main`; tray "Open Odysseus" item; window close→hide handler; reachability check + load `main` (remote) or `unreachable.html`.
- `src-tauri/src/` (new small module, e.g. `health.rs`) — the `GET {server_url}/` reachability check (reused by v2).
- `ui/unreachable.html` + `ui/unreachable.js` — the local server-down screen with Retry.
- `README.md` — document the full-wrap behavior and the two auth mechanisms.

## Verification & risk checklist

All must pass before "done":
1. ⚠️ **Overlay focus under Regular policy** — re-run the full hotkey → capture-bar-takes-focus test (highest-risk item).
2. **Chat streaming** (SSE/streamed responses) renders live in the wrapped webview.
3. **File upload/download** (documents, gallery) works in WKWebView.
4. **Cookie persists** across a quit/relaunch (log in once, relaunch, still logged in).
5. **Remote window has no `window.__TAURI__`** — confirm IPC isolation holds (eval in the `main` webview).
6. Odysseus's CSP / security headers don't break anything in-webview (should match browser behavior).
7. Window lifecycle: opens on launch, close hides (app survives, overlay still works), Quit exits.
8. Unreachable screen shows when the server is down; Retry recovers once it's up.

## Prerequisites

- Existing Tauri toolchain (already set up — the companion builds today).
- A running Odysseus on `:7860` (the nohup stack) for live verification; Ollama up only for capture enrichment, not for the wrap itself.

## v2 (documented next phase, not in this spec)

App-managed backend lifecycle: on launch, if the server is unreachable, spawn **uvicorn + chroma** (from the known venv) with a "starting…" state and child-process cleanup on quit; **health-check (not manage) Ollama**, leaving it as `brew services`. Turns "open Odysseus.app" into "everything just works," solving the nohup/reboot pain.
