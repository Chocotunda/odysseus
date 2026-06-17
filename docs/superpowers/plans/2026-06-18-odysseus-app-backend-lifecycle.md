# Odysseus App — Backend Lifecycle Management (v2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Mac app auto-start the Odysseus backend (uvicorn + ChromaDB) when it's not running, own those processes, and stop them on quit — with a confirm dialog before ever stopping a backend it didn't start.

**Architecture:** A new `backend.rs` unit spawns the two processes via `std::process::Command` and tracks the `Child` handles in Tauri-managed state. Startup runs on an async task (ping → start-if-needed → poll-until-ready → open workspace), driving the gate window via a `backend-status` event. Quit (and a "Stop Backend" tray item) SIGTERMs managed children automatically; an unmanaged backend is only stopped after a native confirm dialog.

**Tech Stack:** Tauri v2 (Rust), `std::process`, `tauri-plugin-dialog` (native confirm), `libc` (SIGTERM), `tokio` (async sleep), `lsof` (PID detection), vanilla JS UI.

**Repo:** all work in `/Users/kganpat/Projects/odysseus-app` (branch off `master`).

---

## File Structure

- **Create** `src-tauri/src/backend.rs` — backend-lifecycle unit. Pure helpers (paths, ports, lsof parsing) + runtime fns (spawn, detect, SIGTERM) + `BackendState`.
- **Modify** `src-tauri/src/config.rs` — add persisted `backend_dir`; refactor disk read/write to a whole-Config read.
- **Modify** `src-tauri/src/lib.rs` — `mod backend;`, dialog/state plugins, async `start_flow`, quit cleanup + confirm, "Stop Backend" tray item, `retry_start` command, replace the blocking `open_or_gate`.
- **Modify** `src-tauri/Cargo.toml` — add `tauri-plugin-dialog`, `libc`, `tokio` (time).
- **Modify** `ui/settings.html` / `ui/settings.js` — "Odysseus backend folder" field.
- **Modify** `ui/unreachable.html` / `ui/unreachable.js` — event-driven "starting / failed / needs-config" states.

---

## Task 1: `backend.rs` pure helpers

**Files:**
- Create: `src-tauri/src/backend.rs`
- Modify: `src-tauri/src/lib.rs` (add `mod backend;` under `mod health;`)

- [ ] **Step 1: Write the failing tests**

Create `src-tauri/src/backend.rs` with ONLY the tests first:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn port_from_url_defaults_and_parses() {
        assert_eq!(port_from_url("http://127.0.0.1:7860"), 7860);
        assert_eq!(port_from_url("http://127.0.0.1:9000/"), 9000);
        assert_eq!(port_from_url("not a url"), 7860);
        assert_eq!(port_from_url(""), 7860);
    }

    #[test]
    fn invalid_backend_dir_when_empty_or_no_uvicorn() {
        assert!(!is_valid_backend_dir(""));
        assert!(!is_valid_backend_dir("   "));
        assert!(!is_valid_backend_dir("/definitely/not/here"));
    }

    #[test]
    fn valid_backend_dir_when_uvicorn_present() {
        let dir = std::env::temp_dir().join("odyapp_be_valid");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("venv/bin")).unwrap();
        std::fs::write(dir.join("venv/bin/uvicorn"), "#!/bin/sh\n").unwrap();
        assert!(is_valid_backend_dir(dir.to_str().unwrap()));
    }

    #[test]
    fn specs_build_expected_commands() {
        let u = uvicorn_spec("/x", 7860);
        assert_eq!(u.program, "/x/venv/bin/python");
        assert_eq!(u.args, vec!["-m","uvicorn","app:app","--host","127.0.0.1","--port","7860"]);
        assert_eq!(u.cwd, "/x");
        let c = chroma_spec("/x", 8100);
        assert_eq!(c.program, "/x/venv/bin/chroma");
        assert_eq!(c.args, vec!["run","--host","127.0.0.1","--port","8100","--path","/x/data/chroma"]);
    }

    #[test]
    fn parse_lsof_pids_filters_junk() {
        assert_eq!(parse_lsof_pids("123\n456\n"), vec![123, 456]);
        assert_eq!(parse_lsof_pids(""), Vec::<u32>::new());
        assert_eq!(parse_lsof_pids("p123\n\nabc\n789"), vec![123, 789]);
    }
}
```

Note: `parse_lsof_pids("p123")` → `123` because the test below strips a leading `p` (lsof `-t` returns bare PIDs, but `-F p` prefixes `p`; we strip it defensively).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src-tauri && cargo test --lib backend`
Expected: FAIL — `cannot find function port_from_url` etc.

- [ ] **Step 3: Write the minimal implementation**

Prepend to `src-tauri/src/backend.rs` (above the test module):

```rust
use std::path::Path;

/// A process to spawn: program + args + working directory.
pub struct ProcSpec {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: String,
}

/// The uvicorn (app server) port, parsed from the configured server URL.
pub fn port_from_url(server_url: &str) -> u16 {
    reqwest::Url::parse(server_url.trim())
        .ok()
        .and_then(|u| u.port())
        .unwrap_or(7860)
}

/// A backend dir is usable only if it contains `venv/bin/uvicorn`.
pub fn is_valid_backend_dir(dir: &str) -> bool {
    let d = dir.trim();
    !d.is_empty() && Path::new(d).join("venv/bin/uvicorn").exists()
}

/// Best-effort default: `$HOME/Projects/odysseus` if it's a valid backend dir.
pub fn default_backend_dir() -> String {
    match std::env::var("HOME") {
        Ok(home) => {
            let cand = format!("{home}/Projects/odysseus");
            if is_valid_backend_dir(&cand) {
                cand
            } else {
                String::new()
            }
        }
        Err(_) => String::new(),
    }
}

pub fn uvicorn_spec(dir: &str, port: u16) -> ProcSpec {
    ProcSpec {
        program: format!("{dir}/venv/bin/python"),
        args: vec![
            "-m".into(), "uvicorn".into(), "app:app".into(),
            "--host".into(), "127.0.0.1".into(), "--port".into(), port.to_string(),
        ],
        cwd: dir.to_string(),
    }
}

pub fn chroma_spec(dir: &str, port: u16) -> ProcSpec {
    ProcSpec {
        program: format!("{dir}/venv/bin/chroma"),
        args: vec![
            "run".into(), "--host".into(), "127.0.0.1".into(),
            "--port".into(), port.to_string(), "--path".into(), format!("{dir}/data/chroma"),
        ],
        cwd: dir.to_string(),
    }
}

/// Parse newline-separated PIDs from `lsof` output (tolerates a leading `p` and junk).
pub fn parse_lsof_pids(output: &str) -> Vec<u32> {
    output
        .lines()
        .filter_map(|l| l.trim().trim_start_matches('p').parse::<u32>().ok())
        .collect()
}
```

Then add the module declaration to `src-tauri/src/lib.rs`, directly under `mod health;` (line 3):

```rust
mod backend;
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src-tauri && cargo test --lib backend`
Expected: PASS (5 tests). A `dead_code` warning on the not-yet-used items is expected (wired up in Tasks 3–4).

- [ ] **Step 5: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add src-tauri/src/backend.rs src-tauri/src/lib.rs
git commit -m "feat: backend module pure helpers (specs, ports, dir validation, lsof)"
```

---

## Task 2: `backend.rs` runtime (spawn, detect, SIGTERM, state)

**Files:**
- Modify: `src-tauri/src/backend.rs`
- Modify: `src-tauri/Cargo.toml`

- [ ] **Step 1: Add `libc` to Cargo.toml**

In `src-tauri/Cargo.toml`, under the macOS target deps block (where `objc2 = "0.6"` is), add:

```toml
libc = "0.2"
```

- [ ] **Step 2: Add the runtime fns + state to `backend.rs`**

Add to `src-tauri/src/backend.rs` (above the test module, below the pure helpers):

```rust
use std::fs::File;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::AtomicBool;
use std::sync::Mutex;

/// Tracks the backend processes THIS app spawned this session.
/// `cleaned` makes shutdown idempotent (tray-quit + ExitRequested can both fire).
#[derive(Default)]
pub struct BackendState {
    pub managed: Mutex<Vec<Child>>,
    pub cleaned: AtomicBool,
}

/// Redirect a child's stdout+stderr to `/tmp/odysseus-app-<name>.log`.
fn log_stdio(name: &str) -> Result<(Stdio, Stdio), String> {
    let path = format!("/tmp/odysseus-app-{name}.log");
    let out = File::create(&path).map_err(|e| format!("{path}: {e}"))?;
    let err = out.try_clone().map_err(|e| e.to_string())?;
    Ok((Stdio::from(out), Stdio::from(err)))
}

fn spawn_one(spec: &ProcSpec, log_name: &str) -> Result<Child, String> {
    let (out, err) = log_stdio(log_name)?;
    Command::new(&spec.program)
        .args(&spec.args)
        .current_dir(&spec.cwd)
        .stdout(out)
        .stderr(err)
        .spawn()
        .map_err(|e| format!("spawn {}: {e}", spec.program))
}

/// Spawn ChromaDB (:8100) + uvicorn (`uvicorn_port`) from `dir`. Returns the
/// child handles (chroma first). If uvicorn fails to spawn, chroma is killed.
pub fn spawn(dir: &str, uvicorn_port: u16) -> Result<Vec<Child>, String> {
    let mut chroma = spawn_one(&chroma_spec(dir, 8100), "chroma")?;
    match spawn_one(&uvicorn_spec(dir, uvicorn_port), "uvicorn") {
        Ok(uvicorn) => Ok(vec![chroma, uvicorn]),
        Err(e) => {
            let _ = chroma.kill();
            Err(e)
        }
    }
}

/// PIDs currently listening on the given TCP ports (via `lsof`).
pub fn listening_pids(ports: &[u16]) -> Vec<u32> {
    let mut pids = Vec::new();
    for p in ports {
        if let Ok(out) = Command::new("lsof")
            .args(["-ti", &format!("tcp:{p}"), "-sTCP:LISTEN"])
            .output()
        {
            pids.extend(parse_lsof_pids(&String::from_utf8_lossy(&out.stdout)));
        }
    }
    pids.sort_unstable();
    pids.dedup();
    pids
}

/// Send SIGTERM to a PID (graceful shutdown). No-op on non-unix.
#[cfg(unix)]
pub fn sigterm(pid: u32) {
    unsafe {
        libc::kill(pid as libc::pid_t, libc::SIGTERM);
    }
}
#[cfg(not(unix))]
pub fn sigterm(_pid: u32) {}
```

- [ ] **Step 3: Verify it builds**

Run: `cd src-tauri && cargo build`
Expected: clean (dead-code warnings on not-yet-wired fns are fine).

- [ ] **Step 4: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add src-tauri/src/backend.rs src-tauri/Cargo.toml
git commit -m "feat: backend spawn/detect/sigterm + BackendState"
```

---

## Task 3: `config.rs` — persist `backend_dir`

**Files:**
- Modify: `src-tauri/src/config.rs`

- [ ] **Step 1: Replace the Config struct + disk read/write + tests**

Replace the `Config` struct and the `read_server_url`/`write_server_url` functions and the `#[cfg(test)]` module in `src-tauri/src/config.rs` with the following (keep the keychain `get_token`/`set_token` code and `load_from`/`save_to` signatures untouched except as shown):

Struct — replace:
```rust
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Config {
    #[serde(default)]
    pub server_url: String,
    // Held in memory / passed over IPC, but never serialized to disk.
    #[serde(skip)]
    pub token: String,
}
```
with:
```rust
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Config {
    #[serde(default)]
    pub server_url: String,
    #[serde(default)]
    pub backend_dir: String,
    // Held in memory / passed over IPC, but never serialized to disk.
    #[serde(skip)]
    pub token: String,
}
```

Disk read/write — replace `read_server_url` and `write_server_url` with whole-Config versions:
```rust
fn read_disk(dir: &PathBuf) -> Config {
    match std::fs::read_to_string(config_path(dir)) {
        Ok(s) => serde_json::from_str::<Config>(&s).unwrap_or_default(),
        Err(_) => Config::default(),
    }
}

fn write_disk(dir: &PathBuf, cfg: &Config) -> Result<(), String> {
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    // token is #[serde(skip)] so it never reaches disk.
    let on_disk = Config {
        server_url: cfg.server_url.clone(),
        backend_dir: cfg.backend_dir.clone(),
        token: String::new(),
    };
    let json = serde_json::to_string_pretty(&on_disk).map_err(|e| e.to_string())?;
    std::fs::write(config_path(dir), json).map_err(|e| e.to_string())
}
```

`load_from` — replace its body:
```rust
pub fn load_from(dir: &PathBuf) -> Config {
    let mut cfg = read_disk(dir);
    cfg.token = get_token();
    cfg
}
```

`save_to` — replace its body:
```rust
pub fn save_to(dir: &PathBuf, cfg: &Config) -> Result<(), String> {
    write_disk(dir, cfg)?;
    set_token(&cfg.token)
}
```

Tests — replace the entire `#[cfg(test)] mod tests { ... }` block with:
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_file_yields_empty_config() {
        let dir = std::env::temp_dir().join("odyapp_test_missing");
        let _ = std::fs::remove_dir_all(&dir);
        let cfg = read_disk(&dir);
        assert_eq!(cfg.server_url, "");
        assert_eq!(cfg.backend_dir, "");
    }

    #[test]
    fn server_url_and_backend_dir_round_trip() {
        let dir = std::env::temp_dir().join("odyapp_test_roundtrip");
        let _ = std::fs::remove_dir_all(&dir);
        let cfg = Config {
            server_url: "http://127.0.0.1:7860".into(),
            backend_dir: "/Users/me/Projects/odysseus".into(),
            token: String::new(),
        };
        write_disk(&dir, &cfg).unwrap();
        let back = read_disk(&dir);
        assert_eq!(back.server_url, "http://127.0.0.1:7860");
        assert_eq!(back.backend_dir, "/Users/me/Projects/odysseus");
    }

    #[test]
    fn token_is_not_written_to_disk() {
        let dir = std::env::temp_dir().join("odyapp_test_notoken");
        let _ = std::fs::remove_dir_all(&dir);
        let cfg = Config { server_url: "http://x".into(), backend_dir: "/y".into(), token: "ody_secret".into() };
        write_disk(&dir, &cfg).unwrap();
        let raw = std::fs::read_to_string(config_path(&dir)).unwrap();
        assert!(!raw.contains("ody_secret"));
        assert!(!raw.contains("\"token\""));
    }
}
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `cd src-tauri && cargo test --lib config`
Expected: PASS (3 tests).

- [ ] **Step 3: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add src-tauri/src/config.rs
git commit -m "feat: persist backend_dir in config (token still keychain-only)"
```

---

## Task 4: `lib.rs` — async startup flow + state + deps

**Files:**
- Modify: `src-tauri/Cargo.toml`
- Modify: `src-tauri/src/lib.rs`

- [ ] **Step 1: Add deps to Cargo.toml**

In `src-tauri/Cargo.toml` `[dependencies]` (the cross-platform block), add:

```toml
tauri-plugin-dialog = "2"
tokio = { version = "1", features = ["time"] }
```

- [ ] **Step 2: Add imports + emit helper + async `start_flow` + `open_main_hide_gate`**

In `src-tauri/src/lib.rs`, extend the `use tauri::{...}` line (line 9) to add `Emitter`:

```rust
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
```

Add this `use` near the other top-level `use` lines:

```rust
use std::time::Duration;
```

Replace the existing `open_or_gate` function (the one using `block_on`) with the async flow + helper:

```rust
/// Emit a backend-status update to the gate window's JS.
fn emit_status(app: &AppHandle, state: &str, detail: &str) {
    let _ = app.emit_to(
        "gate",
        "backend-status",
        serde_json::json!({ "state": state, "detail": detail }),
    );
}

fn open_main_hide_gate(app: &AppHandle, server_url: &str) {
    if let Err(e) = show_or_create_main(app, server_url) {
        eprintln!("open workspace error: {e}");
        return;
    }
    if let Some(g) = app.get_webview_window("gate") {
        let _ = g.hide();
    }
}

/// Async: ping the server; if down and a backend dir is configured, start it,
/// show the gate in a "starting" state, poll until ready, then open the
/// workspace. Drives the gate via the `backend-status` event. Runs on a
/// background task so the main thread never blocks (fixes the v1 startup stall).
async fn start_flow(app: AppHandle) {
    let cfg = config::load_from(&config_dir(&app));
    let port = backend::port_from_url(&cfg.server_url);

    emit_status(&app, "checking", "");
    if health::ping(&cfg.server_url).await {
        open_main_hide_gate(&app, &cfg.server_url);
        return;
    }

    if !backend::is_valid_backend_dir(&cfg.backend_dir) {
        emit_status(&app, "needs-config", "");
        return;
    }

    emit_status(&app, "starting", "");
    let children = match backend::spawn(&cfg.backend_dir, port) {
        Ok(c) => c,
        Err(e) => {
            emit_status(&app, "failed", &format!("{e} — see /tmp/odysseus-app-uvicorn.log"));
            return;
        }
    };
    {
        let st = app.state::<backend::BackendState>();
        st.managed.lock().unwrap().extend(children);
    }

    // Poll readiness for ~30s (60 × 500ms).
    let mut up = false;
    for _ in 0..60 {
        if health::ping(&cfg.server_url).await {
            up = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    if up {
        open_main_hide_gate(&app, &cfg.server_url);
    } else {
        emit_status(
            &app,
            "failed",
            "Odysseus didn't start in time — see /tmp/odysseus-app-uvicorn.log",
        );
    }
}
```

- [ ] **Step 3: Replace `open_workspace`/`check_server` commands with `retry_start`**

Delete the `check_server` command (lines ~98-102) and the `open_workspace` command (lines ~104-112). Add in their place:

```rust
#[tauri::command]
fn retry_start(app: AppHandle) {
    tauri::async_runtime::spawn(start_flow(app));
}
```

(`open_settings` stays as-is.)

- [ ] **Step 4: Register the dialog plugin, manage state, and drive startup async**

In `run()`, after the `tauri_nspanel::init()` plugin line, add the dialog plugin (cross-platform):

```rust
    let builder = builder.plugin(tauri_plugin_dialog::init());
```

In `.setup(|app| { ... })`, replace the line `open_or_gate(app.handle());` with:

```rust
            app.manage(backend::BackendState::default());
            let _ = show_or_create_gate(app.handle());
            tauri::async_runtime::spawn(start_flow(app.handle().clone()));
```

In the tray `on_menu_event`, replace the `"open" => open_or_gate(app),` arm with:

```rust
                    "open" => {
                        tauri::async_runtime::spawn(start_flow(app.clone()));
                    }
```

- [ ] **Step 5: Update `invoke_handler` (drop removed commands, add `retry_start`)**

Replace both `generate_handler!` arms. macOS:
```rust
                tauri::generate_handler![
                    get_config, save_config, capture, show_overlay, hide_overlay,
                    retry_start, open_settings
                ]
```
non-macOS:
```rust
                tauri::generate_handler![
                    get_config, save_config, capture,
                    retry_start, open_settings
                ]
```

- [ ] **Step 6: Verify it builds**

Run: `cd src-tauri && cargo build`
Expected: clean. (`start_flow` is now used; `backend` runtime fns used. If `Emitter`/`emit_to` path differs, it is `tauri::Emitter`.)

- [ ] **Step 7: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add src-tauri/Cargo.toml src-tauri/src/lib.rs
git commit -m "feat: async startup flow that auto-starts the backend (no main-thread block)"
```

---

## Task 5: `lib.rs` — quit cleanup, confirm dialog, "Stop Backend" tray item

**Files:**
- Modify: `src-tauri/src/lib.rs`

- [ ] **Step 1: Add the dialog import**

Add near the top `use` lines of `src-tauri/src/lib.rs`:

```rust
use std::sync::atomic::Ordering;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
```

- [ ] **Step 2: Add the shutdown + stop-backend functions**

Add these functions to `src-tauri/src/lib.rs` (e.g. below `start_flow`):

```rust
/// SIGTERM the managed children we spawned. Returns their PIDs (so callers can
/// exclude them when scanning for "unmanaged" listeners). Idempotent via the
/// `cleaned` flag.
fn stop_managed(app: &AppHandle) -> Vec<u32> {
    let st = app.state::<backend::BackendState>();
    if st.cleaned.swap(true, Ordering::SeqCst) {
        return Vec::new();
    }
    let mut managed = st.managed.lock().unwrap();
    let pids: Vec<u32> = managed.iter().map(|c| c.id()).collect();
    for pid in &pids {
        backend::sigterm(*pid);
    }
    managed.clear();
    pids
}

/// Quit path: stop managed children, then — if a backend WE DIDN'T start is
/// still listening — ask whether to stop it too.
fn shutdown_for_quit(app: &AppHandle) {
    let managed_pids = stop_managed(app);
    let cfg = config::load_from(&config_dir(app));
    let port = backend::port_from_url(&cfg.server_url);
    let external: Vec<u32> = backend::listening_pids(&[port, 8100])
        .into_iter()
        .filter(|p| !managed_pids.contains(p))
        .collect();
    if external.is_empty() {
        return;
    }
    let stop = app
        .dialog()
        .message("An Odysseus backend that this app didn't start is still running. Stop it too?")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Stop it".into(),
            "Leave it running".into(),
        ))
        .blocking_show();
    if stop {
        for pid in external {
            backend::sigterm(pid);
        }
    }
}

/// Tray "Stop Backend": stop managed immediately; for an unmanaged backend,
/// confirm first. Does NOT quit the app.
fn stop_backend_action(app: &AppHandle) {
    let st = app.state::<backend::BackendState>();
    let has_managed = !st.managed.lock().unwrap().is_empty();
    if has_managed {
        // Reset the idempotency flag so a later quit can still run.
        st.cleaned.store(false, Ordering::SeqCst);
        let _ = stop_managed(app);
        st.cleaned.store(false, Ordering::SeqCst);
        return;
    }
    let cfg = config::load_from(&config_dir(app));
    let port = backend::port_from_url(&cfg.server_url);
    let pids = backend::listening_pids(&[port, 8100]);
    if pids.is_empty() {
        return;
    }
    let stop = app
        .dialog()
        .message("This Odysseus backend wasn't started by the app. Stop it anyway?")
        .buttons(MessageDialogButtons::OkCancelCustom("Stop it".into(), "Cancel".into()))
        .blocking_show();
    if stop {
        for pid in pids {
            backend::sigterm(pid);
        }
    }
}
```

- [ ] **Step 3: Add the "Stop Backend" tray item + wire its handler + quit cleanup**

In `.setup`, add the menu item and include it in the menu. Replace:
```rust
            let open_i = MenuItem::with_id(app, "open", "Open Odysseus", true, None::<&str>)?;
            let capture_i = MenuItem::with_id(app, "capture", "Capture\u{2026}", true, None::<&str>)?;
            let settings_i = MenuItem::with_id(app, "settings", "Settings\u{2026}", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_i, &capture_i, &settings_i, &quit_i])?;
```
with:
```rust
            let open_i = MenuItem::with_id(app, "open", "Open Odysseus", true, None::<&str>)?;
            let capture_i = MenuItem::with_id(app, "capture", "Capture\u{2026}", true, None::<&str>)?;
            let stop_i = MenuItem::with_id(app, "stop_backend", "Stop Backend", true, None::<&str>)?;
            let settings_i = MenuItem::with_id(app, "settings", "Settings\u{2026}", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_i, &capture_i, &stop_i, &settings_i, &quit_i])?;
```

Add a `"stop_backend"` arm and change the `"quit"` arm in `on_menu_event`:
```rust
                    "stop_backend" => stop_backend_action(app),
```
```rust
                    "quit" => {
                        shutdown_for_quit(app);
                        app.exit(0);
                    }
```

- [ ] **Step 4: Intercept Cmd-Q / window-driven exit via RunEvent**

Replace the tail of `run()`:
```rust
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
```
with:
```rust
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                shutdown_for_quit(app_handle);
            }
        });
```

(`shutdown_for_quit` is idempotent via `cleaned`, so the tray-quit path + this handler can't double-prompt.)

- [ ] **Step 5: Verify it builds**

Run: `cd src-tauri && cargo build`
Expected: clean. If `RunEvent::ExitRequested` doesn't fire for tray `app.exit(0)` (it routes to `RunEvent::Exit`), that's fine — the tray "quit" arm already calls `shutdown_for_quit` directly; the RunEvent handler is there to catch Cmd-Q. Confirm both paths in Task 7.

- [ ] **Step 6: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add src-tauri/src/lib.rs
git commit -m "feat: quit cleanup + unmanaged confirm dialog + Stop Backend tray item"
```

---

## Task 6: UI — settings backend folder + event-driven gate

**Files:**
- Modify: `ui/settings.html`, `ui/settings.js`
- Modify: `ui/unreachable.html`, `ui/unreachable.js`
- Modify: `src-tauri/src/lib.rs` (the `save_config` command signature)

- [ ] **Step 1: Add the backend-folder field to `ui/settings.html`**

Replace the body's form region. Change:
```html
  <label>Server URL<input id="server-url" type="text" placeholder="http://127.0.0.1:7860" /></label>
  <label>API token<input id="token" type="password" placeholder="ody_…" /></label>
  <button id="save">Save</button>
```
to:
```html
  <label>Server URL<input id="server-url" type="text" placeholder="http://127.0.0.1:7860" /></label>
  <label>API token<input id="token" type="password" placeholder="ody_…" /></label>
  <label>Odysseus backend folder<input id="backend-dir" type="text" placeholder="/Users/you/Projects/odysseus" /></label>
  <button id="save">Save</button>
```

- [ ] **Step 2: Wire `backend-dir` in `ui/settings.js`**

Replace the contents of `ui/settings.js` with:
```javascript
const { invoke } = window.__TAURI__.core;

const urlEl = document.getElementById("server-url");
const tokenEl = document.getElementById("token");
const backendEl = document.getElementById("backend-dir");
const statusEl = document.getElementById("settings-status");

async function loadConfig() {
  try {
    const cfg = await invoke("get_config");
    urlEl.value = cfg.server_url || "";
    tokenEl.value = cfg.token || "";
    backendEl.value = cfg.backend_dir || "";
  } catch (e) {
    statusEl.textContent = "Could not load config";
  }
}

document.getElementById("save").addEventListener("click", async () => {
  try {
    await invoke("save_config", {
      serverUrl: urlEl.value,
      token: tokenEl.value,
      backendDir: backendEl.value,
    });
    statusEl.classList.remove("error");
    statusEl.textContent = "Saved";
  } catch (e) {
    statusEl.classList.add("error");
    statusEl.textContent = "Save failed";
  }
});

loadConfig();
```

- [ ] **Step 3: Update the `save_config` command to accept `backend_dir`**

In `src-tauri/src/lib.rs`, replace the `save_config` command:
```rust
#[tauri::command]
fn save_config(app: AppHandle, server_url: String, token: String) -> Result<(), String> {
    let cfg = config::Config {
        server_url: server_url.trim().to_string(),
        token: token.trim().to_string(),
    };
    config::save_to(&config_dir(&app), &cfg)
}
```
with:
```rust
#[tauri::command]
fn save_config(
    app: AppHandle,
    server_url: String,
    token: String,
    backend_dir: String,
) -> Result<(), String> {
    let cfg = config::Config {
        server_url: server_url.trim().to_string(),
        backend_dir: backend_dir.trim().to_string(),
        token: token.trim().to_string(),
    };
    config::save_to(&config_dir(&app), &cfg)
}
```

- [ ] **Step 4: Make the gate event-driven in `ui/unreachable.html`**

Replace the `<main class="gate-card">…</main>` block with a state-driven layout:
```html
  <main class="gate-card">
    <svg class="gate-icon" width="40" height="40" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5" />
      <path d="M12 16h.01" />
    </svg>
    <h1 class="gate-title" id="title">Connecting to Odysseus…</h1>
    <p class="gate-msg" id="msg">Checking whether the server is running.</p>
    <div class="gate-actions">
      <button id="retry" class="gate-btn gate-btn-primary">Retry</button>
      <button id="settings" class="gate-btn">Open Settings</button>
    </div>
  </main>
```

- [ ] **Step 5: Rewrite `ui/unreachable.js` to listen for `backend-status`**

Replace the contents of `ui/unreachable.js` with:
```javascript
const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const titleEl = document.getElementById("title");
const msgEl = document.getElementById("msg");
const retryBtn = document.getElementById("retry");
const settingsBtn = document.getElementById("settings");

function render(state, detail) {
  if (state === "checking") {
    titleEl.textContent = "Connecting to Odysseus…";
    msgEl.textContent = "Checking whether the server is running.";
    retryBtn.disabled = true;
  } else if (state === "starting") {
    titleEl.textContent = "Starting Odysseus…";
    msgEl.textContent = "Launching the backend. This can take a few seconds.";
    retryBtn.disabled = true;
  } else if (state === "needs-config") {
    titleEl.textContent = "Set your Odysseus folder";
    msgEl.textContent = "Open Settings and set the Odysseus backend folder so the app can start it.";
    retryBtn.disabled = false;
  } else if (state === "failed") {
    titleEl.textContent = "Couldn't start Odysseus";
    msgEl.textContent = detail || "The backend didn't come up. Check the logs and try again.";
    retryBtn.disabled = false;
  }
}

listen("backend-status", (e) => render(e.payload.state, e.payload.detail));

retryBtn.addEventListener("click", () => {
  render("checking", "");
  invoke("retry_start");
});
settingsBtn.addEventListener("click", () => invoke("open_settings"));

// Kick a check on load in case we missed the initial event.
render("checking", "");
invoke("retry_start");
```

- [ ] **Step 6: Verify build + JS syntax**

Run: `cd src-tauri && cargo build` (expect clean) and `node --check ui/unreachable.js && node --check ui/settings.js` (expect no output).

- [ ] **Step 7: Confirm gate has event permission**

The gate JS uses `window.__TAURI__.event.listen`. The `gate` window already has `core:default` in `capabilities/default.json`, which includes the core event permissions. Verify by checking the build emits no capability error; if `listen` is denied at runtime (Task 7), add `"core:event:default"` to the `permissions` array in `capabilities/default.json` and rebuild.

- [ ] **Step 8: Commit**

```bash
cd /Users/kganpat/Projects/odysseus-app
git add ui/settings.html ui/settings.js ui/unreachable.html ui/unreachable.js src-tauri/src/lib.rs
git commit -m "feat: backend-folder setting + event-driven gate (starting/failed/needs-config)"
```

---

## Task 7: Live verification (GUI + process) and ship

Run from a real Terminal. No automated tests cover the spawn/SIGTERM/dialog/GUI — verify by running. Record pass/fail.

- [ ] **Step 1: Auto-start from cold**
  - Stop the backend: `lsof -ti tcp:7860 -sTCP:LISTEN | xargs kill; lsof -ti tcp:8100 -sTCP:LISTEN | xargs kill` (or however it's running).
  - Set the backend folder once: launch `cargo tauri dev`, tray → Settings…, set "Odysseus backend folder" to `/Users/kganpat/Projects/odysseus`, Save.
  - Quit and relaunch `cargo tauri dev`. Expected: gate shows "Starting Odysseus…", then the workspace opens within ~30s. Verify with `lsof -iTCP:7860 -sTCP:LISTEN` that uvicorn is up; `lsof -iTCP:8100` that chroma is up.

- [ ] **Step 2: Managed cleanup on quit**
  - With the app having started the backend, **Quit** (tray → Quit). Expected: no confirm dialog (it's managed); `lsof -iTCP:7860`/`:8100` show nothing afterward (uvicorn + chroma terminated).

- [ ] **Step 3: Unmanaged → confirm dialog on quit**
  - Start the backend manually first (the `nohup` commands from HANDOFF), then launch the app → workspace opens (no spawn). **Quit.** Expected: the confirm dialog "…didn't start…stop it too?" appears. Click **Leave it running** → `lsof -iTCP:7860` still shows it. Relaunch + quit again, click **Stop it** → it's gone.

- [ ] **Step 4: Cmd-Q path** — repeat Step 2 but quit with **Cmd-Q** instead of the tray. Expected: same cleanup (confirms the `RunEvent::ExitRequested` handler fires). If cleanup did NOT happen on Cmd-Q, note it — the fix is to also handle `RunEvent::Exit` in the run closure.

- [ ] **Step 5: "Stop Backend" tray item** — start via the app, then tray → **Stop Backend**: managed processes stop, app stays running, gate reappears (the workspace's server is gone). With a manually-started backend, tray → Stop Backend shows the confirm first.

- [ ] **Step 6: Failure + needs-config**
  - Clear the backend folder in Settings → relaunch with server down → gate shows "Set your Odysseus folder".
  - Set the folder to a bad path → relaunch → gate shows "Couldn't start Odysseus" + the log hint.

- [ ] **Step 7: No startup freeze** — confirm the dock icon / gate appears immediately on launch (no ~2s stall).

- [ ] **Step 8: Build, sign, install, push**

```bash
cd /Users/kganpat/Projects/odysseus-app
cargo tauri build \
  && codesign --force --deep --sign - "src-tauri/target/release/bundle/macos/Odysseus.app" \
  && rm -rf "/Applications/Odysseus.app" \
  && cp -R "src-tauri/target/release/bundle/macos/Odysseus.app" /Applications/
git push origin <branch>
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Auto-start uvicorn + chroma when unreachable → Task 2 (`spawn`) + Task 4 (`start_flow`).
- Async startup / no main-thread block → Task 4 (`tauri::async_runtime::spawn(start_flow)`); verified Task 7 Step 7.
- "Starting…" gate state + readiness on `:7860` ping → Task 4 (`emit_status`, poll loop) + Task 6 (gate JS).
- Managed vs unmanaged + only-manage-what-we-spawned → Task 5 (`stop_managed` tracks spawned PIDs; `listening_pids` minus managed = external).
- Quit confirm for unmanaged → Task 5 (`shutdown_for_quit`); verified Task 7 Step 3.
- "Stop Backend" tray item (confirm for external) → Task 5 (`stop_backend_action`).
- `backend_dir` setting + auto-detect → Task 1 (`default_backend_dir`) + Task 3 (persist) + Task 6 (Settings field).
- Ollama health-check-only / not managed → not spawned anywhere (only chroma + uvicorn in `spawn`); no Ollama code added. ✓
- Log files for diagnosability → Task 2 (`log_stdio` → `/tmp/odysseus-app-*.log`).
- Failure / timeout / invalid-dir handling → Task 4 (`failed`/`needs-config` emits) + Task 6 (render).

**Placeholder scan:** none — all code blocks complete; the two conditional notes (Cmd-Q→Exit fallback, `core:event:default` fallback) give explicit instructions, not TODOs.

**Type consistency:** `BackendState { managed: Mutex<Vec<Child>>, cleaned: AtomicBool }` is defined in Task 2 and used in Tasks 4–5. `port_from_url`/`is_valid_backend_dir`/`spawn`/`listening_pids`/`sigterm`/`uvicorn_spec`/`chroma_spec`/`parse_lsof_pids` defined in Tasks 1–2, used consistently in Tasks 4–5. `start_flow`/`emit_status`/`open_main_hide_gate`/`stop_managed`/`shutdown_for_quit`/`stop_backend_action`/`retry_start` names match across definition and call sites. `save_config` gains `backend_dir` (Task 6 Step 3) matching the JS `backendDir` arg (Task 6 Step 2) and the `Config` field (Task 3). Gate `backend-status` event name matches between `emit_status` (Task 4) and the JS listener (Task 6).
