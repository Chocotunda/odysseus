# Odysseus App — Full-Workspace Native Wrap (v1) Implementation Plan

> **⚠️ SUPERSEDED 2026-06-23 — completed historical build plan.** Native **Tide** is now the daily client; the full-workspace wrapper this plan built is **FROZEN** (interim backend-booter + web-back-office shell only). Not current direction. See `docs/ai-context/2026-06-22-life-os-direction-decision-record.md` §10.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a full Odysseus workspace window to the existing menubar capture app — a native macOS window hosting the live web UI at `http://127.0.0.1:7860/`, with the capture overlay untouched.

**Architecture:** A third Tauri window (`main`) loads the **external** server URL — the whole workspace, no per-feature work. The app switches to `Regular` activation (dock icon always). Login uses Odysseus's own `/login` page; WKWebView persists the session cookie. IPC isolation is enforced by the **capability allowlist**: `main` (untrusted remote content) is never listed, so it gets zero commands; a separate local `gate` window shows the server-down/Retry screen and *is* capability-bearing.

**Tech Stack:** Tauri v2 (Rust), `reqwest` (already a dep, used for the reachability ping), vanilla HTML/JS UI (no build step), `tauri-nspanel` (overlay, unchanged).

**Repo:** all work is in `/Users/kganpat/Projects/odysseus-app`. (This plan and the spec live in the `odysseus` repo for continuity.)

---

## Deviations from the spec (intentional, stronger)

- Spec said "flip `withGlobalTauri` off globally." Tauri v2 has **no per-window `withGlobalTauri`**, and the no-build-step overlay/settings JS needs the global. The actual security boundary is the **capability allowlist**, which already excludes any window not listed. We keep `withGlobalTauri: true` and enforce isolation by **never adding `main` to capabilities**.
- Spec said the server-down screen loads into the same window. Instead it gets its **own `gate` window** so the trusted local page and the untrusted remote workspace never share a window label (and thus never share capabilities). This is the same UX, stronger isolation.

---

## File Structure

In `/Users/kganpat/Projects/odysseus-app`:

- **Create** `src-tauri/src/health.rs` — reachability ping + two pure helpers (`health_url`, `is_up_status`). Own responsibility: "is the server reachable?". Reused verbatim by v2.
- **Modify** `src-tauri/src/lib.rs` — `Regular` activation; `check_server`/`open_workspace`/`open_settings` commands; `show_or_create_main` (external) and `show_or_create_gate` (local) helpers; launch gate-or-workspace decision; tray "Open Odysseus" item; `main` close-hides handler.
- **Modify** `src-tauri/src/main.rs` — no change (entry point).
- **Create** `ui/unreachable.html` + `ui/unreachable.js` — the local `gate` page (server-down message + Retry + Open Settings).
- **Modify** `src-tauri/capabilities/default.json` — add `gate` to the window allowlist (NOT `main`).
- **Modify** `ui/style.css` — add the `.gate-*` styles (reuse existing CSS vars).
- **Modify** `README.md` — document the wrap and the two auth mechanisms.

---

## Task 1: Reachability helpers (`health.rs`)

**Files:**
- Create: `src-tauri/src/health.rs`
- Modify: `src-tauri/src/lib.rs` (add `mod health;` near the other `mod` lines at top)

- [ ] **Step 1: Write the failing tests**

Create `src-tauri/src/health.rs` with ONLY the tests first:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn health_url_appends_root_and_trims_trailing_slash() {
        assert_eq!(health_url("http://127.0.0.1:7860"), Some("http://127.0.0.1:7860/".into()));
        assert_eq!(health_url("http://127.0.0.1:7860/"), Some("http://127.0.0.1:7860/".into()));
        assert_eq!(health_url("  http://x:7860  "), Some("http://x:7860/".into()));
    }

    #[test]
    fn health_url_is_none_when_unconfigured() {
        assert_eq!(health_url(""), None);
        assert_eq!(health_url("   "), None);
    }

    #[test]
    fn is_up_status_treats_any_answer_as_up() {
        // "/" redirects to /login (3xx) when unauthenticated, 2xx when authed —
        // both mean the server answered, so both are "up".
        assert!(is_up_status(200));
        assert!(is_up_status(302));
        assert!(is_up_status(401));
        assert!(is_up_status(403));
        assert!(!is_up_status(502));
        assert!(!is_up_status(0));
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src-tauri && cargo test --lib health`
Expected: FAIL — `cannot find function health_url` / `is_up_status`.

- [ ] **Step 3: Write the minimal implementation**

Prepend to `src-tauri/src/health.rs` (above the `#[cfg(test)]` module):

```rust
use std::time::Duration;

/// Build the health-check URL for a server base. `None` when unconfigured.
pub fn health_url(server_url: &str) -> Option<String> {
    let base = server_url.trim().trim_end_matches('/');
    if base.is_empty() {
        None
    } else {
        Some(format!("{base}/"))
    }
}

/// Whether a status from the ping means the server is reachable. Any HTTP
/// answer counts as up (a 302 to /login is the unauthenticated case).
pub fn is_up_status(status: u16) -> bool {
    (200..=399).contains(&status) || status == 401 || status == 403
}

/// Ping `{server_url}/` with a short timeout, following no redirects.
/// Returns true if the server answered with an "up" status.
pub async fn ping(server_url: &str) -> bool {
    let Some(url) = health_url(server_url) else {
        return false;
    };
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(2))
        .redirect(reqwest::redirect::Policy::none())
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    match client.get(&url).send().await {
        Ok(resp) => is_up_status(resp.status().as_u16()),
        Err(_) => false,
    }
}
```

Then add the module declaration to `src-tauri/src/lib.rs` — directly under the existing `mod capture;` line (line 2):

```rust
mod health;
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src-tauri && cargo test --lib health`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add src-tauri/src/health.rs src-tauri/src/lib.rs
git commit -m "feat: add server reachability ping (health module)"
```

---

## Task 2: Capability allowlist for the `gate` window

**Files:**
- Modify: `src-tauri/capabilities/default.json`

- [ ] **Step 1: Add `gate` to the window allowlist**

Replace the contents of `src-tauri/capabilities/default.json` with:

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Capability for local Tauri windows (overlay, settings, gate). The remote workspace window 'main' is deliberately excluded so it gets no IPC.",
  "windows": ["overlay", "settings", "gate"],
  "permissions": [
    "core:default"
  ]
}
```

Note: `main` is intentionally absent — that is the IPC isolation boundary.

- [ ] **Step 2: Verify it still builds**

Run: `cd src-tauri && cargo build`
Expected: builds without capability-schema errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add src-tauri/capabilities/default.json
git commit -m "feat: allow IPC for the gate window (main stays excluded)"
```

---

## Task 3: The `gate` page (server-down / Retry screen)

**Files:**
- Create: `ui/unreachable.html`
- Create: `ui/unreachable.js`
- Modify: `ui/style.css` (append `.gate-*` block)

- [ ] **Step 1: Create `ui/unreachable.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Odysseus</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body class="gate-body">
  <main class="gate-card">
    <svg class="gate-icon" width="40" height="40" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5" />
      <path d="M12 16h.01" />
    </svg>
    <h1 class="gate-title">Odysseus isn't running</h1>
    <p class="gate-msg" id="msg">Couldn't reach the server at <span id="url">the configured URL</span>.</p>
    <div class="gate-actions">
      <button id="retry" class="gate-btn gate-btn-primary">Retry</button>
      <button id="settings" class="gate-btn">Open Settings</button>
    </div>
  </main>
  <script src="unreachable.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `ui/unreachable.js`**

```javascript
const { invoke } = window.__TAURI__.core;

const msgEl = document.getElementById("msg");
const urlEl = document.getElementById("url");
const retryBtn = document.getElementById("retry");
const settingsBtn = document.getElementById("settings");

async function showConfiguredUrl() {
  try {
    const cfg = await invoke("get_config");
    urlEl.textContent = cfg.server_url || "(not configured — open Settings)";
  } catch (_) {
    urlEl.textContent = "the configured URL";
  }
}

retryBtn.addEventListener("click", async () => {
  retryBtn.disabled = true;
  retryBtn.textContent = "Checking…";
  const up = await invoke("check_server");
  if (up) {
    await invoke("open_workspace");
  } else {
    msgEl.textContent = "Still can't reach the server. Start Odysseus, then Retry.";
    retryBtn.disabled = false;
    retryBtn.textContent = "Retry";
  }
});

settingsBtn.addEventListener("click", () => invoke("open_settings"));

showConfiguredUrl();
```

- [ ] **Step 3: Append `.gate-*` styles to `ui/style.css`**

```css
/* --- Server-down gate window --- */
.gate-body {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  margin: 0;
}
.gate-card {
  text-align: center;
  max-width: 380px;
  padding: 24px;
}
.gate-icon { color: var(--red); margin-bottom: 12px; }
.gate-title { font-size: 16px; margin: 0 0 8px; color: var(--fg); }
.gate-msg { font-size: 13px; color: var(--fg); opacity: 0.8; margin: 0 0 20px; line-height: 1.5; }
.gate-actions { display: flex; gap: 10px; justify-content: center; }
.gate-btn {
  font-family: inherit;
  font-size: 13px;
  padding: 8px 16px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--card);
  color: var(--fg);
  cursor: pointer;
}
.gate-btn:disabled { opacity: 0.5; cursor: default; }
.gate-btn-primary { border-color: var(--red); }
```

- [ ] **Step 4: Verify CSS vars exist**

Run: `grep -nE '\-\-red|\-\-fg|\-\-card|\-\-border' ui/style.css`
Expected: each variable is defined (in the existing `:root`/theme block). If `--red` is absent, reuse the accent var that the overlay already uses for its primary action and adjust the class above to match.

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add ui/unreachable.html ui/unreachable.js ui/style.css
git commit -m "feat: add gate (server-unreachable) page with retry"
```

---

## Task 4: Window helpers, commands, and `Regular` activation (`lib.rs`)

**Files:**
- Modify: `src-tauri/src/lib.rs`

- [ ] **Step 1: Add imports and window helpers**

At the top of `src-tauri/src/lib.rs`, extend the `use tauri::...` imports to include the webview builder and window event types. Replace the existing line:

```rust
use tauri::{AppHandle, Manager};
```

with:

```rust
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
```

Then add these helpers below `config_dir` (after line ~20):

```rust
/// Show the existing workspace window, or create it pointing at the live
/// server URL. The `main` window loads REMOTE content and is intentionally
/// NOT in capabilities — it gets no Tauri IPC.
fn show_or_create_main(app: &AppHandle, server_url: &str) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.set_focus();
        return Ok(());
    }
    let base = server_url.trim().trim_end_matches('/');
    if base.is_empty() {
        return Err("no server configured".into());
    }
    let url = reqwest::Url::parse(base).map_err(|e| e.to_string())?;
    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
        .title("Odysseus")
        .inner_size(1200.0, 820.0)
        .min_inner_size(720.0, 480.0)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Show or create the local server-down gate window.
fn show_or_create_gate(app: &AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("gate") {
        let _ = w.show();
        let _ = w.set_focus();
        return Ok(());
    }
    WebviewWindowBuilder::new(app, "gate", WebviewUrl::App("unreachable.html".into()))
        .title("Odysseus")
        .inner_size(520.0, 360.0)
        .resizable(false)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}
```

- [ ] **Step 2: Add the three new commands**

Below the existing `capture` command (after line ~40), add:

```rust
#[tauri::command]
async fn check_server(app: AppHandle) -> bool {
    let cfg = config::load_from(&config_dir(&app));
    health::ping(&cfg.server_url).await
}

#[tauri::command]
fn open_workspace(app: AppHandle) -> Result<(), String> {
    let cfg = config::load_from(&config_dir(&app));
    show_or_create_main(&app, &cfg.server_url)?;
    if let Some(g) = app.get_webview_window("gate") {
        let _ = g.hide();
    }
    Ok(())
}

#[tauri::command]
fn open_settings(app: AppHandle) {
    if let Some(w) = app.get_webview_window("settings") {
        let _ = w.show();
        let _ = w.set_focus();
    }
}
```

- [ ] **Step 3: Switch to `Regular` activation and gate-or-launch on startup**

In `run()`'s `.setup(|app| { ... })`, replace the existing activation line:

```rust
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
```

with `Regular` + the launch decision:

```rust
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Regular);

            // Open the workspace window if the server is reachable, otherwise
            // the gate (server-down) window. A short blocking ping is fine at
            // launch (2s timeout in health::ping).
            {
                let handle = app.handle().clone();
                let cfg = config::load_from(&config_dir(&handle));
                let up = tauri::async_runtime::block_on(health::ping(&cfg.server_url));
                let result = if up {
                    show_or_create_main(&handle, &cfg.server_url)
                } else {
                    show_or_create_gate(&handle)
                };
                if let Err(e) = result {
                    eprintln!("startup window error: {e}");
                    let _ = show_or_create_gate(&handle);
                }
            }
```

- [ ] **Step 4: Add the "Open Odysseus" tray item and its handler**

In the tray-menu setup, add a menu item above `capture_i`. Replace:

```rust
            let capture_i = MenuItem::with_id(app, "capture", "Capture\u{2026}", true, None::<&str>)?;
            let settings_i = MenuItem::with_id(app, "settings", "Settings\u{2026}", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&capture_i, &settings_i, &quit_i])?;
```

with:

```rust
            let open_i = MenuItem::with_id(app, "open", "Open Odysseus", true, None::<&str>)?;
            let capture_i = MenuItem::with_id(app, "capture", "Capture\u{2026}", true, None::<&str>)?;
            let settings_i = MenuItem::with_id(app, "settings", "Settings\u{2026}", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_i, &capture_i, &settings_i, &quit_i])?;
```

Then add an `"open"` arm to the `on_menu_event` match (next to the `"settings"` arm):

```rust
                    "open" => {
                        let cfg = config::load_from(&config_dir(app));
                        let up = tauri::async_runtime::block_on(health::ping(&cfg.server_url));
                        let r = if up {
                            show_or_create_main(app, &cfg.server_url)
                        } else {
                            show_or_create_gate(app)
                        };
                        if let Err(e) = r {
                            eprintln!("open workspace error: {e}");
                        }
                    }
```

- [ ] **Step 5: Make closing `main` hide it instead of quitting**

On the `tauri::Builder::default()` chain in `run()`, add an `.on_window_event` handler. Insert it immediately after the `let builder = builder.plugin(tauri_nspanel::init());` block / before `.setup(`:

```rust
    let builder = builder.on_window_event(|window, event| {
        if window.label() == "main" {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        }
    });
```

- [ ] **Step 6: Register the new commands in `invoke_handler`**

In both the macOS and non-macOS `generate_handler!` arms, add `check_server`, `open_workspace`, `open_settings`. The macOS arm becomes:

```rust
                tauri::generate_handler![
                    get_config,
                    save_config,
                    capture,
                    show_overlay,
                    hide_overlay,
                    check_server,
                    open_workspace,
                    open_settings
                ]
```

and the non-macOS arm:

```rust
                tauri::generate_handler![
                    get_config,
                    save_config,
                    capture,
                    check_server,
                    open_workspace,
                    open_settings
                ]
```

- [ ] **Step 7: Verify it compiles**

Run: `cd src-tauri && cargo build`
Expected: builds clean. Fix any unused-import warnings (e.g. if `WindowEvent` path differs, it is `tauri::WindowEvent`).

- [ ] **Step 8: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add src-tauri/src/lib.rs
git commit -m "feat: full workspace window, gate flow, Regular activation, Open Odysseus tray item"
```

---

## Task 5: Live verification (GUI — requires a real session)

This task has no automated tests — the wrap is webview + macOS window behavior, verified by running the app. Start Odysseus first (`:7860`), then run `cd src-tauri && cargo tauri dev` from a **real Terminal** (not detached — detached launch strips focus rights). Check each item; record pass/fail.

- [ ] **Step 1: Workspace loads & login persists**
  - App launches → `main` window opens showing Odysseus, redirected to `/login`.
  - Log in. Confirm the workspace renders. **Quit the app, relaunch.** Expected: still logged in (no `/login`) — proves cookie persistence. *(Verification item #4.)*

- [ ] **Step 2: Overlay focus still works under `Regular`** ⚠️ highest risk
  - Press **Cmd+Shift+Space**. Expected: the capture bar appears AND the input has keyboard focus (type immediately, no NSBeep). Enter captures; Escape dismisses without beep.
  - If focus is lost: this is the documented `Accessory→Regular` risk. Re-check `panel.rs` `unpark_overlay`'s `set_focus` on the WKWebView; do not revert activation to `Accessory` without re-discussing (it would undo the dock-icon requirement).

- [ ] **Step 3: Chat streaming renders live** — open a chat, send a prompt, confirm tokens stream in (SSE works in WKWebView). *(Verification item #2.)*

- [ ] **Step 4: File upload/download works** — upload a document or gallery image through the UI; confirm it uploads and can be opened/downloaded. *(Verification item #3.)*

- [ ] **Step 5: IPC isolation holds** — in the `main` (workspace) window, open the webview's devtools console and run:

```js
typeof window.__TAURI__ !== "undefined" && window.__TAURI__.core.invoke("get_config").then(()=>"LEAK").catch(e=>"blocked: "+e)
```

Expected: the invoke is **rejected** (no capability for `main`) — logs `blocked: ...`, never `LEAK`. *(Verification item #5.)*

- [ ] **Step 6: Window lifecycle** — close the `main` window (red button): the app keeps running (overlay + tray still work). Tray → **Open Odysseus** reopens it. Tray → **Quit** (or Cmd-Q) exits. *(Verification item #7.)*

- [ ] **Step 7: Server-down gate** — quit the app, **stop** the Odysseus server, relaunch the app. Expected: the `gate` window shows "Odysseus isn't running". Start the server, click **Retry** → workspace loads; **Open Settings** opens the settings window. *(Verification item #8.)*

- [ ] **Step 8: Record results** — note any failures inline in this plan. If all pass, proceed to Task 6.

---

## Task 6: README + standalone build + screenshot

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update `README.md`**

Add a section documenting:
- The app is now the **full Odysseus workspace** (window loads `http://127.0.0.1:7860/`) plus the global-hotkey capture overlay.
- **Two auth mechanisms:** session cookie (full window, via `/login`, persists in WKWebView) and the Keychain Bearer token (capture overlay, `todos:write`).
- v1 assumes the backend is already running; **v2** will auto-launch uvicorn + chroma (never Ollama).
- Build/install: `cargo tauri build && codesign --force --deep --sign - "src-tauri/target/release/bundle/macos/Odysseus.app" && rm -rf "/Applications/Odysseus.app" && cp -R "src-tauri/target/release/bundle/macos/Odysseus.app" /Applications/`.

- [ ] **Step 2: Build the standalone app**

Run:
```bash
cd /Users/kganpat/Projects/odysseus-app
cargo tauri build \
  && codesign --force --deep --sign - "src-tauri/target/release/bundle/macos/Odysseus.app" \
  && rm -rf "/Applications/Odysseus.app" \
  && cp -R "src-tauri/target/release/bundle/macos/Odysseus.app" /Applications/
```
Expected: builds, signs ad-hoc, installs to `/Applications`.

- [ ] **Step 3: Launch the installed app and screenshot the workspace**

Open `/Applications/Odysseus.app` from Finder/Spotlight (real GUI session). Confirm the workspace window appears. Take a screenshot for the handoff.

- [ ] **Step 4: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add README.md
git commit -m "docs: document the full-workspace wrap and build steps"
```

- [ ] **Step 5: Push**

```bash
cd /Users/kganpat/Projects/odysseus-app
git push
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Wrap mechanism (external-URL `main` window) → Task 4.
- Regular activation → Task 4 Step 3.
- IPC isolation → Task 2 (capabilities) + Task 5 Step 5 (verification).
- Login via `/login` + cookie persistence → Task 5 Step 1.
- Server-down screen + Retry (v2 seam) → Tasks 1, 3, 4; verified Task 5 Step 7.
- Window lifecycle (open on launch, close-hides, tray Open, explicit quit) → Task 4 Steps 3–5; verified Task 5 Step 6.
- Capture overlay untouched → no overlay code modified; re-verified Task 5 Step 2.
- Full verification checklist (8 items) → Task 5.

**Placeholder scan:** none — all code blocks complete; the one conditional ("if `--red` absent…") gives an explicit fallback.

**Type consistency:** `health_url`/`is_up_status`/`ping` (Task 1) match their callers (`check_server`, startup, tray `open`). `show_or_create_main`/`show_or_create_gate`/`check_server`/`open_workspace`/`open_settings` names are consistent across helpers, commands, `invoke_handler`, and the JS (`unreachable.js` calls `get_config`/`check_server`/`open_workspace`/`open_settings` — all registered).
