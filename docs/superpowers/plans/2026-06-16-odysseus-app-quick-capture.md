# Odysseus App — Quick-Capture Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a native macOS menubar app (Tauri v2) whose global hotkey pops a tiny capture bar that creates a Planner item in Odysseus via an API token.

**Architecture:** Two parts. **Part 1** (in the `odysseus` repo, branch `feat/planner-phase1`) makes the planner routes API-token-scope-aware so a `todos:write` token can call `POST /api/planner/capture`. **Part 2** (a new sibling repo `/Users/kganpat/Projects/odysseus-app`) is a Tauri v2 app: tray icon + global shortcut + frameless overlay + settings window; all HTTP is done in Rust so the token never touches the webview.

**Tech Stack:** Python/FastAPI/SQLAlchemy/pytest (Part 1). Rust + Tauri v2 + `tauri-plugin-global-shortcut` + `reqwest` (rustls), vanilla HTML/CSS/JS with `withGlobalTauri` (no build step) (Part 2).

**Spec:** `docs/ai-context/odysseus-app-design.md`

---

## Context the implementer needs

- **Run the backend tests** from the odysseus repo root with its venv:
  `./venv/bin/python -m pytest tests/test_planner_owner_scope.py -q`
- **Planner routes** live in `routes/planner_routes.py`. `setup_planner_routes()` returns an
  `APIRouter` with the prefix `/api/planner`. Endpoints are plain functions invoked directly in
  tests (no TestClient).
- **The proven scope pattern to mirror** is `routes/codex_routes.py:84` (`_scope_owner`). The auth
  middleware sets `request.state.api_token` (bool), `request.state.api_token_scopes` (list[str]),
  and `request.state.api_token_owner` (str|None) for bearer-token requests.
- **Existing test helpers** (`tests/test_planner_owner_scope.py`): `_session_factory()`,
  `_request(user)` (browser session), `_seed(...)`, `_endpoint(router, path, method)`. Reuse them.
- Odysseus runs locally on **`http://127.0.0.1:7860`**; Ollama on `:11434` (capture does not block
  on the model — enrichment is a background task).

---

# PART 1 — Backend: make planner routes token-scope-aware (odysseus repo)

### Task 1: Scope-aware owner resolution on the planner routes

**Files:**
- Modify: `routes/planner_routes.py` (the `_owner` helper inside `setup_planner_routes` + its call sites; add two module-level scope-set constants)
- Test: `tests/test_planner_owner_scope.py`

- [ ] **Step 1: Add a token-request helper to the test file**

Add near the other helpers in `tests/test_planner_owner_scope.py` (after `_request`):

```python
def _token_request(owner, scopes):
    """A bearer-API-token request as the auth middleware would shape it."""
    return SimpleNamespace(state=SimpleNamespace(
        current_user="api",
        api_token=True,
        api_token_scopes=list(scopes),
        api_token_owner=owner,
    ))
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_planner_owner_scope.py`:

```python
def test_token_with_write_scope_can_create(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SessionFactory)
    router = planner_routes.setup_planner_routes()
    create_item = _endpoint(router, "/items", "POST")

    body = planner_routes.PlanItemCreate(title="from token")
    out = create_item(_token_request("alice", ["todos:write"]), body=body)

    assert out["title"] == "from token"
    # owned by the token's owner, so it is visible to that owner's list
    list_items = _endpoint(router, "/items", "GET")
    listed = list_items(_token_request("alice", ["todos:read"]))
    assert {it["id"] for it in listed["items"]} == {out["id"]}


def test_token_missing_write_scope_is_forbidden(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SessionFactory)
    router = planner_routes.setup_planner_routes()
    create_item = _endpoint(router, "/items", "POST")

    body = planner_routes.PlanItemCreate(title="nope")
    with pytest.raises(HTTPException) as exc:
        create_item(_token_request("alice", ["todos:read"]), body=body)
    assert exc.value.status_code == 403


def test_token_read_scope_cannot_read_other_owner(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SessionFactory)
    bob_id = _seed(SessionFactory, "bob")
    router = planner_routes.setup_planner_routes()
    get_item = _endpoint(router, "/items/{item_id}", "GET")

    with pytest.raises(HTTPException) as exc:
        get_item(_token_request("alice", ["todos:read"]), item_id=bob_id)
    assert exc.value.status_code == 404


def test_token_with_no_owner_is_forbidden(monkeypatch):
    SessionFactory = _session_factory()
    monkeypatch.setattr(planner_routes, "SessionLocal", SessionFactory)
    router = planner_routes.setup_planner_routes()
    create_item = _endpoint(router, "/items", "POST")

    body = planner_routes.PlanItemCreate(title="x")
    with pytest.raises(HTTPException) as exc:
        create_item(_token_request(None, ["todos:write"]), body=body)
    assert exc.value.status_code == 403
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_planner_owner_scope.py -q`
Expected: the 4 new tests FAIL — currently `_owner` ignores tokens and `require_user` raises
`403 "API tokens must use a scope-aware API route"`, so e.g. `test_token_with_write_scope_can_create`
fails with a 403 instead of creating the item.

- [ ] **Step 4: Add the scope constants (module level)**

In `routes/planner_routes.py`, after the `ORDINAL_GAP = 1024` line, add:

```python
# API-token scope gates (browser/cookie sessions bypass these — they auth by
# cookie). Reads accept either todos scope; writes require todos:write.
TODO_READ_SCOPES = {"todos:read", "todos:write"}
TODO_WRITE_SCOPES = {"todos:write"}
```

- [ ] **Step 5: Replace `_owner` with the scope-aware version**

In `routes/planner_routes.py`, inside `setup_planner_routes`, replace the whole `_owner` function:

```python
    def _owner(request: Request, allowed: set) -> Optional[str]:
        # Resolve the data owner, honoring API-token scopes (mirrors
        # routes/codex_routes.py:_scope_owner). Bearer-token callers must carry
        # one of `allowed` and resolve to their token's owner; everyone else
        # falls back to require_user (which still fails closed for stray tokens),
        # coercing "" -> None in single-user mode so the ownership gate below
        # behaves.
        if getattr(request.state, "api_token", False):
            scopes = set(getattr(request.state, "api_token_scopes", []) or [])
            if not scopes.intersection(allowed):
                required = " or ".join(sorted(allowed))
                raise HTTPException(403, f"API token missing required scope: {required}")
            owner = getattr(request.state, "api_token_owner", None)
            if not owner:
                raise HTTPException(403, "API token has no owner")
            return owner
        return require_user(request) or None
```

- [ ] **Step 6: Pass the right scope at each call site**

In `routes/planner_routes.py`, update every `_owner(request)` call:
- `list_items`: `user = _owner(request, TODO_READ_SCOPES)`
- `get_item`: `user = _owner(request, TODO_READ_SCOPES)`
- `create_item`: `user = _owner(request, TODO_WRITE_SCOPES)`
- `complete_item`: `user = _owner(request, TODO_WRITE_SCOPES)`
- `capture`: `user = _owner(request, TODO_WRITE_SCOPES)`
- `plan_item`: `user = _owner(request, TODO_WRITE_SCOPES)`

- [ ] **Step 7: Run the planner tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_planner_owner_scope.py tests/test_planner_crud.py tests/test_planner_capture.py -q`
Expected: PASS (the 3 original owner-scope tests, the 4 new token tests, and CRUD/capture
regression all green).

- [ ] **Step 8: Syntax check + commit**

```bash
python -m compileall -q routes/planner_routes.py
git add routes/planner_routes.py tests/test_planner_owner_scope.py
git commit -m "feat(planner): make routes API-token-scope-aware for quick-capture

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# PART 2 — Tauri quick-capture app (new repo `/Users/kganpat/Projects/odysseus-app`)

> All Part 2 work happens in the new repo. `cd /Users/kganpat/Projects/odysseus-app` after Task 2.
> GUI/global-hotkey behaviour is verified by **running the app** (it can't be unit-tested); pure
> logic (config round-trip, HTTP-error mapping) gets Rust `#[cfg(test)]` unit tests.

### Task 2: Scaffold the repo and boot a tray-only app

**Files:**
- Create: the whole `odysseus-app/` tree (scaffolded, then key files replaced in later tasks)

- [ ] **Step 1: Install the Tauri CLI (one-time)**

Run: `cargo install tauri-cli --version "^2.0" --locked`
Verify: `cargo tauri --version` prints a `tauri-cli 2.x` line.

- [ ] **Step 2: Scaffold a baseline app to obtain a working icon set**

Run:
```bash
cd /Users/kganpat/Projects
cargo install create-tauri-app --locked
cargo create-tauri-app odysseus-app --template vanilla --manager npm --yes
cd odysseus-app
```
This creates `src-tauri/` (with `icons/`), a vanilla `src/` frontend, and `package.json`.
We keep **only** `src-tauri/icons/` and the Cargo/Rust skeleton; the rest is replaced below.

- [ ] **Step 3: Replace the frontend with our static `ui/` folder**

```bash
rm -rf src index.html package.json package-lock.json node_modules
mkdir -p ui
```
Create `ui/overlay.html`:
```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><link rel="stylesheet" href="style.css"></head>
<body class="overlay">
  <form id="capture-form" autocomplete="off">
    <input id="capture-input" type="text" placeholder="Capture a task…" spellcheck="false" />
  </form>
  <div id="status" class="status"></div>
  <script src="overlay.js"></script>
</body>
</html>
```
Create `ui/settings.html`:
```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><link rel="stylesheet" href="style.css"></head>
<body class="settings">
  <h1>Odysseus App — Settings</h1>
  <label>Server URL<input id="server-url" type="text" placeholder="http://127.0.0.1:7860" /></label>
  <label>API token<input id="token" type="password" placeholder="ody_…" /></label>
  <button id="save">Save</button>
  <div id="settings-status" class="status"></div>
  <script src="settings.js"></script>
</body>
</html>
```

- [ ] **Step 4: Create the stylesheet (Odysseus visual rules: dark, Fira Code, no emoji)**

Create `ui/style.css`:
```css
:root {
  --bg: #0e0f11; --fg: #e6e6e6; --card: #17191c; --border: #2a2d31; --red: #e5484d;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; font-family: "Fira Code", ui-monospace, monospace; color: var(--fg); background: transparent; }
body.overlay { display: flex; flex-direction: column; gap: 6px; padding: 10px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; }
#capture-form { margin: 0; }
#capture-input { width: 100%; padding: 12px 14px; font: inherit; font-size: 16px; color: var(--fg); background: var(--bg); border: 1px solid var(--border); border-radius: 8px; outline: none; }
#capture-input:focus { border-color: #3a82f6; }
.status { min-height: 16px; font-size: 12px; color: #9aa0a6; padding: 0 4px; }
.status.error { color: var(--red); }
body.settings { background: var(--bg); padding: 18px; }
body.settings h1 { font-size: 15px; margin: 0 0 14px; }
body.settings label { display: block; font-size: 12px; margin-bottom: 12px; color: #9aa0a6; }
body.settings input { display: block; width: 100%; margin-top: 4px; padding: 8px 10px; font: inherit; color: var(--fg); background: var(--card); border: 1px solid var(--border); border-radius: 6px; }
body.settings button { padding: 8px 16px; font: inherit; color: var(--fg); background: var(--card); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
body.settings button:hover { border-color: #3a82f6; }
```

- [ ] **Step 5: Write `src-tauri/tauri.conf.json`**

Replace `src-tauri/tauri.conf.json` with:
```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "Odysseus App",
  "version": "0.1.0",
  "identifier": "com.krishen.odysseus-app",
  "build": { "frontendDist": "../ui" },
  "app": {
    "withGlobalTauri": true,
    "windows": [
      {
        "label": "overlay",
        "url": "overlay.html",
        "title": "Capture",
        "width": 560, "height": 96,
        "decorations": false, "transparent": true, "alwaysOnTop": true,
        "center": true, "resizable": false, "skipTaskbar": true,
        "visible": false, "focus": false
      },
      {
        "label": "settings",
        "url": "settings.html",
        "title": "Odysseus App — Settings",
        "width": 420, "height": 260,
        "resizable": false, "visible": false
      }
    ],
    "security": { "csp": null }
  },
  "bundle": { "active": true, "targets": "app", "icon": [
    "icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png", "icons/icon.icns", "icons/icon.ico"
  ] }
}
```

- [ ] **Step 6: Set Cargo dependencies**

Replace the `[dependencies]` section of `src-tauri/Cargo.toml` with:
```toml
[dependencies]
tauri = { version = "2", features = ["tray-icon"] }
tauri-plugin-global-shortcut = "2"
reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```
Leave `[build-dependencies] tauri-build = { version = "2", features = [] }` as scaffolded.

- [ ] **Step 7: Minimal `main.rs` that boots a tray (no shortcut/commands yet)**

Replace `src-tauri/src/main.rs` with:
```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            let capture_i = MenuItem::with_id(app, "capture", "Capture…", true, None::<&str>)?;
            let settings_i = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&capture_i, &settings_i, &quit_i])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "settings" => {
                        if let Some(w) = app.get_webview_window("settings") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

- [ ] **Step 8: Build and boot, verify the tray appears**

Run: `cargo tauri dev`
Expected: compiles; a tray (menubar) icon appears with **Capture… / Settings… / Quit**; no dock
icon; clicking **Settings…** shows the (empty) settings window; **Quit** exits. (Capture menu item
is wired in Task 7.)

- [ ] **Step 9: Init git and commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
printf 'target/\n*.app\n.DS_Store\n' > .gitignore
git init -q && git add -A
git commit -q -m "chore: scaffold Tauri v2 menubar app (tray + windows)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Config module (load/save) with a unit test

**Files:**
- Create: `src-tauri/src/config.rs`
- Modify: `src-tauri/src/main.rs` (add `mod config;` + `get_config`/`save_config` commands + register handler)

- [ ] **Step 1: Write the config module with a round-trip unit test**

Create `src-tauri/src/config.rs`:
```rust
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Config {
    #[serde(default)]
    pub server_url: String,
    #[serde(default)]
    pub token: String,
}

pub fn config_path(dir: &PathBuf) -> PathBuf {
    dir.join("config.json")
}

pub fn load_from(dir: &PathBuf) -> Config {
    let path = config_path(dir);
    match std::fs::read_to_string(&path) {
        Ok(s) => serde_json::from_str(&s).unwrap_or_default(),
        Err(_) => Config::default(),
    }
}

pub fn save_to(dir: &PathBuf, cfg: &Config) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let json = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(config_path(dir), json).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_file_yields_default() {
        let dir = std::env::temp_dir().join("odyapp_test_missing");
        let _ = std::fs::remove_dir_all(&dir);
        let cfg = load_from(&dir);
        assert_eq!(cfg.server_url, "");
        assert_eq!(cfg.token, "");
    }

    #[test]
    fn save_then_load_round_trips() {
        let dir = std::env::temp_dir().join("odyapp_test_roundtrip");
        let _ = std::fs::remove_dir_all(&dir);
        let cfg = Config { server_url: "http://127.0.0.1:7860".into(), token: "ody_abc".into() };
        save_to(&dir, &cfg).unwrap();
        let loaded = load_from(&dir);
        assert_eq!(loaded.server_url, "http://127.0.0.1:7860");
        assert_eq!(loaded.token, "ody_abc");
    }
}
```

- [ ] **Step 2: Run the unit test to verify it passes**

Run: `cd src-tauri && cargo test config:: 2>&1 | tail -20`
Expected: `missing_file_yields_default` and `save_then_load_round_trips` PASS.
(Tests are pure filesystem — no Tauri runtime needed.)

- [ ] **Step 3: Add the commands and register them in `main.rs`**

In `src-tauri/src/main.rs`, add `mod config;` at the top (below the `#![cfg_attr...]` line), and add
these command functions above `fn main()`:
```rust
use tauri::{AppHandle, Manager};

fn config_dir(app: &AppHandle) -> std::path::PathBuf {
    app.path().app_config_dir().expect("no app config dir")
}

#[tauri::command]
fn get_config(app: AppHandle) -> config::Config {
    config::load_from(&config_dir(&app))
}

#[tauri::command]
fn save_config(app: AppHandle, server_url: String, token: String) -> Result<(), String> {
    let cfg = config::Config { server_url: server_url.trim().to_string(), token: token.trim().to_string() };
    config::save_to(&config_dir(&app), &cfg)
}
```
Then register them on the builder — change `tauri::Builder::default()` to:
```rust
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_config, save_config])
```
(Remove the now-duplicate `use tauri::Manager;` if the compiler warns about a double import — keep
the combined `use tauri::{AppHandle, Manager};`.)

- [ ] **Step 4: Verify it still builds**

Run: `cargo build 2>&1 | tail -15`
Expected: builds with no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add -A && git commit -q -m "feat: config load/save + get_config/save_config commands

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The `capture` command (Rust HTTP) + error-mapping unit test

**Files:**
- Create: `src-tauri/src/capture.rs`
- Modify: `src-tauri/src/main.rs` (`mod capture;` + register `capture` command)

- [ ] **Step 1: Write the capture module with a pure error-mapping function + test**

Create `src-tauri/src/capture.rs`:
```rust
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct CaptureOk {
    pub id: String,
    pub title: String,
}

#[derive(Debug, Serialize)]
pub struct CaptureErr {
    pub kind: String,
    pub message: String,
}

impl CaptureErr {
    pub fn new(kind: &str, message: &str) -> Self {
        CaptureErr { kind: kind.into(), message: message.into() }
    }
}

/// Pure mapping from an HTTP status code to a user-facing CaptureErr.
/// Returns None for 2xx (success is handled by the caller).
pub fn err_for_status(status: u16) -> Option<CaptureErr> {
    match status {
        200..=299 => None,
        401 | 403 => Some(CaptureErr::new("unauthorized", "Unauthorized — check the API token")),
        s => Some(CaptureErr::new("http", &format!("Server returned {s}"))),
    }
}

pub async fn post_capture(server_url: &str, token: &str, text: &str) -> Result<CaptureOk, CaptureErr> {
    if server_url.is_empty() || token.is_empty() {
        return Err(CaptureErr::new("not_configured", "Not configured — open Settings"));
    }
    let base = server_url.trim_end_matches('/');
    let url = format!("{base}/api/planner/capture");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .bearer_auth(token)
        .json(&serde_json::json!({ "text": text }))
        .send()
        .await
        .map_err(|_| CaptureErr::new("unreachable", "Server unreachable"))?;

    if let Some(e) = err_for_status(resp.status().as_u16()) {
        return Err(e);
    }
    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|_| CaptureErr::new("bad_response", "Unexpected server response"))?;
    Ok(CaptureOk {
        id: body.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        title: body.get("title").and_then(|v| v.as_str()).unwrap_or(text).to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_status_codes() {
        assert!(err_for_status(200).is_none());
        assert_eq!(err_for_status(401).unwrap().kind, "unauthorized");
        assert_eq!(err_for_status(403).unwrap().kind, "unauthorized");
        assert_eq!(err_for_status(500).unwrap().kind, "http");
    }
}
```

- [ ] **Step 2: Run the unit test to verify it passes**

Run: `cd src-tauri && cargo test capture:: 2>&1 | tail -20`
Expected: `maps_status_codes` PASSES.

- [ ] **Step 3: Add the `capture` command and register it**

In `src-tauri/src/main.rs`: add `mod capture;` near the other `mod` lines, add the command above
`fn main()`:
```rust
#[tauri::command]
async fn capture(app: AppHandle, text: String) -> Result<capture::CaptureOk, capture::CaptureErr> {
    let cfg = config::load_from(&config_dir(&app));
    capture::post_capture(&cfg.server_url, &cfg.token, text.trim()).await
}
```
and add `capture` to the handler list:
```rust
        .invoke_handler(tauri::generate_handler![get_config, save_config, capture])
```

- [ ] **Step 4: Verify it builds**

Run: `cargo build 2>&1 | tail -15`
Expected: builds clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add -A && git commit -q -m "feat: capture command posting to /api/planner/capture

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Wire the Settings window to the config commands

**Files:**
- Create: `ui/settings.js`

- [ ] **Step 1: Write `ui/settings.js`**

```js
const { invoke } = window.__TAURI__.core;

const urlEl = document.getElementById("server-url");
const tokenEl = document.getElementById("token");
const statusEl = document.getElementById("settings-status");

async function loadConfig() {
  try {
    const cfg = await invoke("get_config");
    urlEl.value = cfg.server_url || "";
    tokenEl.value = cfg.token || "";
  } catch (e) {
    statusEl.textContent = "Could not load config";
  }
}

document.getElementById("save").addEventListener("click", async () => {
  try {
    await invoke("save_config", { serverUrl: urlEl.value, token: tokenEl.value });
    statusEl.classList.remove("error");
    statusEl.textContent = "Saved";
  } catch (e) {
    statusEl.classList.add("error");
    statusEl.textContent = "Save failed";
  }
});

loadConfig();
```

- [ ] **Step 2: Verify in the running app**

Run: `cargo tauri dev`, open **Settings…** from the tray. Enter `http://127.0.0.1:7860` and a token,
click **Save** → shows "Saved". Close and reopen Settings → the values reload (proves persistence).

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -q -m "feat: settings window wired to config commands

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Wire the overlay capture bar to the `capture` command

**Files:**
- Create: `ui/overlay.js`

- [ ] **Step 1: Write `ui/overlay.js`**

```js
const { invoke } = window.__TAURI__.core;
const win = window.__TAURI__.window.getCurrentWindow();

const form = document.getElementById("capture-form");
const input = document.getElementById("capture-input");
const statusEl = document.getElementById("status");

function setStatus(msg, isError) {
  statusEl.textContent = msg || "";
  statusEl.classList.toggle("error", !!isError);
}

// Clear + focus each time the overlay is shown; hide when it loses focus.
win.onFocusChanged(({ payload: focused }) => {
  if (focused) {
    input.value = "";
    setStatus("");
    input.focus();
  } else {
    win.hide();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") win.hide();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  setStatus("Capturing…");
  try {
    const item = await invoke("capture", { text });
    setStatus("Added: " + (item.title || text));
    setTimeout(() => win.hide(), 900);
  } catch (err) {
    setStatus(err && err.message ? err.message : "Capture failed", true);
  }
});
```

- [ ] **Step 2: Temporarily show the overlay to test it (shortcut comes in Task 7)**

In `src-tauri/src/main.rs`, add a `"capture"` arm to the tray `on_menu_event` match (so we can trigger
the overlay before the hotkey exists):
```rust
                    "capture" => {
                        if let Some(w) = app.get_webview_window("overlay") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
```

- [ ] **Step 3: Verify capture end-to-end via the tray**

Pre-req: Odysseus running on `:7860` with Part 1 deployed; mint a `todos:write` token (Settings →
API tokens) and save it in the app's Settings.
Run: `cargo tauri dev` → tray **Capture…** → overlay appears focused → type `buy milk tomorrow` →
Enter → "Added: …" → overlay auto-hides. Confirm the item appears at `http://127.0.0.1:7860/planner`.
Also test the error path: clear the token in Settings, capture again → overlay shows
"Not configured — open Settings" in red.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -q -m "feat: overlay capture bar wired to capture command

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Global hotkey (Cmd+Shift+Space) toggles the overlay

**Files:**
- Modify: `src-tauri/src/main.rs` (register the global-shortcut plugin + handler)
- Modify: `src-tauri/Cargo.toml` (already has the plugin from Task 2 Step 6 — no change)

- [ ] **Step 1: Register the plugin and the shortcut in `setup`**

In `src-tauri/src/main.rs`, add these imports near the top:
```rust
use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut, ShortcutState, GlobalShortcutExt};
```
Inside `.setup(|app| { ... })`, **before** `Ok(())`, add:
```rust
            let toggle = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::Space);
            let toggle_for_handler = toggle.clone();
            app.handle().plugin(
                tauri_plugin_global_shortcut::Builder::new()
                    .with_handler(move |app, sc, event| {
                        if sc == &toggle_for_handler && event.state() == ShortcutState::Pressed {
                            if let Some(w) = app.get_webview_window("overlay") {
                                let visible = w.is_visible().unwrap_or(false);
                                if visible {
                                    let _ = w.hide();
                                } else {
                                    let _ = w.show();
                                    let _ = w.set_focus();
                                }
                            }
                        }
                    })
                    .build(),
            )?;
            app.global_shortcut().register(toggle)?;
```

- [ ] **Step 2: Build and verify the hotkey works**

Run: `cargo tauri dev`. With another app focused, press **Cmd+Shift+Space** → the capture overlay
appears centered and focused; type a task + Enter → it captures and hides; press the hotkey again
when nothing is showing → it appears; press it while showing → it hides. Clicking away (blur) also
hides it.

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -q -m "feat: global hotkey Cmd+Shift+Space toggles capture overlay

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: README + full end-to-end verification + screenshot

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Odysseus App

Native macOS menubar companion for [Odysseus](../odysseus). A global hotkey
(**Cmd+Shift+Space**) opens a quick-capture bar that creates a Planner item via
the Odysseus API.

## Prerequisites
- Rust + Tauri CLI (`cargo install tauri-cli --version "^2.0"`)
- Odysseus running locally (default `http://127.0.0.1:7860`)
- An Odysseus API token with the `todos:write` scope (Settings → API tokens)

## Run (dev)
```bash
cargo tauri dev
```
Open **Settings…** from the tray, paste the server URL + token, Save. Then press
**Cmd+Shift+Space** anywhere to capture.

## Build
```bash
cargo tauri build
```

## Config
Stored at the app config dir (`~/Library/Application Support/com.krishen.odysseus-app/config.json`).
The token is stored in plaintext for now (local single-user); OS-keychain storage is a future item.
```

- [ ] **Step 2: Full end-to-end check**

1. In odysseus repo: `./venv/bin/python -m pytest tests/test_planner_*.py -q` → all green.
2. Odysseus running on `:7860`; Ollama up.
3. In odysseus-app: `cargo tauri dev`; Settings has server URL + a `todos:write` token.
4. Press **Cmd+Shift+Space**, capture `email investors about demo tomorrow`, Enter → confirms + hides.
5. Open `http://127.0.0.1:7860/planner` → the item is present and (after ~1–2s) AI-enriched.
6. Take a screenshot of the overlay mid-capture and save to `/Users/kganpat/Projects/odysseus-app/docs-screenshot.png` (gitignored or kept — your call).

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -q -m "docs: add README

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** Part 1 = "Backend prerequisite" §; Tasks 2–8 = the Tauri app §
  (structure, tray, hotkey, overlay, settings, config, capture flow, verification). All spec
  sections map to a task.
- **Tauri 2.x API drift:** the tray/menu/global-shortcut APIs above target Tauri 2.x. If a point
  release moved an item (e.g. a `use` path or `MenuItem::with_id` signature), consult the Tauri v2
  docs (context7: `tauri-apps/tauri`) — the structure stays the same, only the symbol path changes.
- **No new backend scopes/DB migration** — `todos:read`/`todos:write` already exist in
  `ALLOWED_SCOPES`. The token is minted in the existing Settings → API tokens UI.
- **Why HTTP is in Rust:** keeps the token out of the webview and sidesteps CORS; the frontend only
  ever calls `invoke("capture", …)`.
