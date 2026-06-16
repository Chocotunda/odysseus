# Odysseus App — native quick-capture companion (Phase 1 / MVP A)

**Date:** 2026-06-16
**Status:** Design approved, pre-implementation
**Related:** `planner-feature-design.md` (the in-app Planner), HANDOFF.md "Next Steps #1 + #3"

## Goal

A native macOS menubar companion (Tauri) that provides a **global-hotkey quick-capture**
into the Odysseus Planner. Press a hotkey anywhere → a tiny capture bar appears → type a
task in natural language → it lands in `/planner` (created instantly, AI-enriched in the
background, exactly as the web capture bar does today).

This is the **solid foundation** the rest of the companion (full planner window, autostart,
packaged app) will be built on later.

## Scope

**In scope (MVP A):**
- Tauri v2 app: menubar tray icon, global hotkey, frameless capture overlay, settings window.
- Backend prerequisite: make the planner capture/CRUD routes API-token-scope-aware so a
  `todos:write` token can drive `POST /api/planner/capture`.

**Out of scope (later phases):**
- Full `/planner` web UI window (MVP B), autostart-on-login, packaged/signed `.app`,
  OS-keychain token storage, in-app configurable-hotkey UI, cross-platform polish.

## Approach

**Tauri v2 + vanilla HTML/JS frontend, HTTP performed in Rust.** Chosen over a JS-framework
frontend (adds a build toolchain for a single text box) and over non-Tauri options
(Hammerspoon/Swift — Tauri keeps cross-platform + future web-UI reuse open). Rationale:
no build step (matches Odysseus's no-build vanilla ethos), token stays in Rust (never
exposed to the webview, no CORS), snappiest hotkey path, smallest dependency surface.

## Part 1 — Backend prerequisite (odysseus repo, branch `feat/planner-phase1`)

The planner routes currently use `_owner(request) = require_user(request) or None`, and
`require_user` **rejects bearer tokens** (`src/auth_helpers.py:81`,
`403 "API tokens must use a scope-aware API route"`). So today no API token can call the
planner. Fix by mirroring the proven `codex_routes._scope_owner` pattern
(`routes/codex_routes.py:84`):

- Add `_scope_owner(request, allowed: set[str])` to `planner_routes.py`:
  - If `request.state.api_token` is set: require `api_token_scopes ∩ allowed` (else `403`),
    require a non-empty `api_token_owner` (else `403`), return that owner.
  - Else: return `require_user(request) or None` (today's behaviour — browser sessions and
    single-user `""` → `None` unchanged).
- Scope wiring:
  - **Write routes** (`/capture`, `POST /items`, `/items/{id}/complete`, `/items/{id}/plan`)
    → require `{"todos:write"}`.
  - **Read routes** (`GET /items`, `GET /items/{id}`) → require `{"todos:read", "todos:write"}`.
- The existing strict ownership gate (`_get_owned`, exact-owner list filter) is **unchanged**
  and keeps working — a token resolves to a real owner string, which the gate already handles.
- **No new scopes** (`todos:read`/`todos:write` already in `ALLOWED_SCOPES`) and **no DB change**.

**Tests** — extend `tests/test_planner_owner_scope.py`:
- A `todos:write` token can `capture` / create / complete, and the item is owned by the
  token's owner.
- A token missing `todos:write` → `403` on a write route.
- A token cannot read or mutate another owner's item (404/403 as appropriate).
- Browser-session behaviour is regression-tested as unchanged.

## Part 2 — The Tauri app (new repo `/Users/kganpat/Projects/odysseus-app`)

Separate sibling repo (parallel to `odysseus` and `tide`) so it never conflicts with upstream
odysseus merges.

### Structure
```
odysseus-app/
  src-tauri/
    Cargo.toml
    tauri.conf.json
    src/main.rs
    icons/
  ui/                 # frontendDist — static, no build step
    overlay.html
    settings.html
    style.css
    overlay.js
    settings.js
  README.md
```

### Rust side (`src/main.rs`) — minimal
- **Tray icon** (menubar) with menu: *Capture…* (show overlay) · *Settings…* · *Quit*.
- **Global shortcut** via `tauri-plugin-global-shortcut`, default **`Cmd+Shift+Space`**,
  toggles the overlay window. (Hardcoded for MVP; configurable UI is a later phase.)
- **Commands:**
  - `capture(text) -> Result<Item, CaptureError>`: read config, `POST {serverUrl}/api/planner/capture`
    with `Authorization: Bearer <token>` and JSON `{ "text": text }` via `reqwest`; return the
    created item, or a structured error (`not_configured` / `unauthorized` / `unreachable` /
    `http_<status>`).
  - `get_config() -> Config` and `save_config(server_url, token)`.
- **Config:** JSON at the Tauri app-config dir, fields `serverUrl` + `token`. Token stored in
  plaintext for MVP; OS-keychain storage is a flagged future hardening item.

### Frontend (vanilla; Odysseus visual rules)
Dark theme, `Fira Code`, reuse CSS-variable names (`--bg`, `--fg`, `--card`, `--border`,
`--red`), monochrome inline SVG only, **no emoji**.
- **Overlay window:** frameless, always-on-top, centered, single capture input.
  - `Enter` → `invoke('capture', { text })`; on success show a brief confirmation echoing the
    captured title, then auto-hide (~1s).
  - `Esc` or window blur → hide. Errors shown inline (e.g. "Not configured — open Settings",
    "Unauthorized (check token)", "Server unreachable").
- **Settings window:** Server URL + API token fields + Save → `invoke('save_config', …)`.

### Capture flow
hotkey → overlay shown + focused → type NL task → `Enter` → Rust `capture()` POSTs →
Odysseus creates the item instantly (background AI enrichment as today) → overlay confirms →
hides. Item appears in `/planner`.

## Verification
- **Backend:** `./venv/bin/python -m pytest tests/test_planner_owner_scope.py -q` green,
  including the new token tests.
- **App:** `cargo tauri dev`; mint a `todos:write` token in Odysseus (Settings → API tokens),
  paste it + `http://127.0.0.1:7860` into the app's Settings, press `Cmd+Shift+Space`, capture
  "buy milk tomorrow", confirm it lands in `/planner`. Capture a screenshot.

## Prerequisites
- **Rust toolchain + Tauri CLI** installed (M4 Mac + Xcode CLT). Verify before scaffolding.
- A running Odysseus (app on `:7860`) with the Part-1 backend change deployed, and Ollama up
  for enrichment (capture itself does not block on the model).

## Open items / future phases
- Default hotkey is `Cmd+Shift+Space` (changeable in code now; settings UI later).
- Later: full `/planner` window (MVP B), autostart-on-login, packaged signed `.app`,
  keychain token storage, configurable-hotkey UI, Windows/Linux builds.
