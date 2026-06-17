# Odysseus App — backend lifecycle management (v2)

**Date:** 2026-06-18
**Status:** Design approved, pre-implementation
**Repo for implementation:** `/Users/kganpat/Projects/odysseus-app` (`master`, full-wrap v1 already shipped)
**Related:** `2026-06-17-odysseus-app-full-wrap-design.md` (v1 thin wrap — this is the deferred "v2" from its Roadmap; the gate/Retry screen was built as the seam for this).

## Goal

Make "open the Mac app" boot the whole local stack. When Odysseus isn't running, the app launches the backend itself (uvicorn + ChromaDB) and shows a "starting…" state until it's ready — turning today's "run three `nohup` commands after every reboot" into a single app launch. The app **owns the processes it starts** and cleans them up on quit; processes it didn't start are only ever stopped with the user's explicit confirmation.

## Scope

**In scope (v2):**
- Auto-start uvicorn (`:7860`) + ChromaDB (`:8100`) when the server is unreachable at launch, from a configured Odysseus folder.
- Async startup (no main-thread block — also fixes the v1 ~2s startup freeze the final review flagged).
- "Starting Odysseus…" state in the gate window; readiness gated on the `:7860` ping; failure/timeout shows a clear message + log path.
- **Managed vs. unmanaged** classification and lifecycle:
  - Managed (app-spawned) → SIGTERM automatically on quit.
  - Unmanaged (already running / orphaned) → never auto-killed; stoppable only via explicit confirmation.
- A confirm dialog on quit when an unmanaged backend is still running ("stop it too?").
- A "Stop Backend" tray item (works for managed; confirms for unmanaged).
- A `backend_dir` setting (the Odysseus folder), auto-detected when possible.

**Out of scope (deferred):**
- Managing **Ollama** — health-checked only, left as `brew services` (heavy, shared).
- Autostart-the-app-on-login, Apple-signed/notarized build, configurable-hotkey UI, Windows/Linux.
- Restarting/monitoring the backend after it crashes mid-session (v3 if wanted).

## Approach

**Spawn the two processes directly from Rust** (`std::process::Command`), track the child handles in Tauri-managed state, and SIGTERM them on quit. Chosen over: (a) calling `start-macos.sh` — too heavy (it `brew install`s llama.cpp + apfel) and it backgrounds its processes, so the app loses the PIDs it needs to clean up; (b) a launchd login-item daemon — overkill and contradicts "the app owns the lifecycle." Direct spawning gives precise child-process control, which the ownership/cleanup requirement depends on.

## Backend commands (the minimal stack)

Run from the configured Odysseus folder `<dir>` (default `~/Projects/odysseus`):
- **ChromaDB:** `<dir>/venv/bin/chroma run --host 127.0.0.1 --port 8100 --path <dir>/data/chroma`
- **uvicorn:** `<dir>/venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7860` (cwd = `<dir>`)

uvicorn's port is derived from the configured `server_url`; ChromaDB stays `:8100`. Each child's stdout/stderr is redirected to a log file (`/tmp/odysseus-app-uvicorn.log`, `/tmp/odysseus-app-chroma.log`) so a failed start is diagnosable. No `--reload` (keeps each backend a single, directly-killable process).

## Startup flow

At launch, on a **background async task** (no `block_on`; fixes the v1 main-thread stall):

1. **Ping `:7860`.**
2. **Reachable** → it was already running. Classify as **unmanaged**; resolve its listening PID(s) via `lsof`. Open the workspace. (Never tracked for auto-kill.)
3. **Unreachable + `backend_dir` valid** → **auto-start**: spawn chroma + uvicorn (classify as **managed**, store the `Child` handles), show the gate window in a **"starting Odysseus…"** state, and poll `:7860` until it answers (timeout ~30s).
   - **Up in time** → open the workspace.
   - **Timeout / spawn error** → gate shows a failure message + the log path + Retry.
4. **Unreachable + no/invalid `backend_dir`** → gate shows "Set your Odysseus folder in Settings" (+ Open Settings).

Readiness gates on `:7860` only; ChromaDB warms up alongside and Odysseus degrades to keyword fallback if it's briefly behind.

## Lifecycle & ownership

The app holds a Tauri-managed `BackendState` tracking the spawned `Child` handles and whether a backend is **managed** (app-spawned this session) or **unmanaged** (detected already-running, with resolved PIDs).

- **Only manage what we spawned this session.** A backend that was already up at launch, or one orphaned by a previous app crash (detected because `:7860` answers at launch), is classified **unmanaged**.
- **Manual "Stop Backend" tray item** — enabled whenever a backend is running:
  - **Managed** → SIGTERM our children immediately.
  - **Unmanaged** → label reads "Stop Backend (external)"; clicking shows a confirm dialog before SIGTERM-ing the external PID(s).
- **On quit** (Cmd-Q / tray Quit) — intercept `RunEvent::ExitRequested` (`api.prevent_exit()`), then:
  1. SIGTERM managed children automatically (no prompt).
  2. If an **unmanaged** backend is still running → native confirm dialog *"An Odysseus backend that this app didn't start is still running. Stop it too?"* → **[Stop it]** SIGTERMs the external PID(s) · **[Leave it running]** leaves them.
  3. `app.exit(0)`.
  - If nothing unmanaged is running, quit is silent (just stops managed children).

SIGTERM (not SIGKILL) so uvicorn/chroma shut down cleanly. If the app itself crashes, managed children may orphan; the next launch detects them as unmanaged (`:7860` answers) and won't auto-kill them — matching the "only manage what I spawned this session" rule.

## Components (files in `odysseus-app`)

- **Create** `src-tauri/src/backend.rs` — the backend-lifecycle unit:
  - pure helpers (TDD'd): build the chroma/uvicorn commands from `<dir>` + ports; `default_backend_dir()` auto-detect; validate a dir (has `venv/bin/uvicorn`); parse `lsof` output → PIDs.
  - runtime fns: `spawn(dir, port)` → managed `Child`s with stdio to logs; `detect_unmanaged(ports)` → PIDs; `stop(targets)` → SIGTERM.
  - `BackendState` struct (managed children + classification), stored via `app.manage(...)`.
- **Modify** `src-tauri/src/lib.rs` — async startup task (ping → start-if-needed → poll → open workspace, emitting progress to the gate); `RunEvent::ExitRequested` interception + quit confirm; "Stop Backend" tray item; manage `BackendState`.
- **Modify** `src-tauri/src/config.rs` — add `backend_dir: String` (persisted to `config.json`); `default_backend_dir()` used when unset.
- **Modify** `ui/unreachable.html` / `unreachable.js` — "starting Odysseus…" state (listens for a `backend-status` event), and a failure state showing the log path + Retry.
- **Modify** `ui/settings.html` / `settings.js` — an "Odysseus backend folder" field wired through `get_config`/`save_config`.
- **Modify** `src-tauri/Cargo.toml` — add `tauri-plugin-dialog` (native confirm) and `libc` (SIGTERM).

## Error handling
- Invalid/missing `backend_dir` (no `venv/bin/uvicorn`) → gate "Set your Odysseus folder in Settings", never a spawn attempt.
- Spawn failure → gate error + log path.
- Readiness timeout (~30s) → gate "Odysseus didn't start in time — see `<log>`" + Retry (Retry re-pings; if still down and managed start already attempted, offers to restart).
- `lsof` missing/empty for an unmanaged backend → the Stop action reports "couldn't resolve the process" rather than failing silently.

## Verification (v2)
Automated (TDD): command construction, `default_backend_dir`/validation, `lsof` PID parsing. Runtime/manual (OS-process + GUI, like v1's live-verification task):
1. Server down at launch → app auto-starts it, gate shows "starting…", workspace opens when ready.
2. Quit → managed uvicorn + chroma actually terminate (verify with `lsof -iTCP:7860`/`:8100`).
3. Start the backend manually first, then launch the app → workspace opens; quit → **confirm dialog** appears; "Leave it running" leaves `:7860` up; "Stop it" takes it down.
4. "Stop Backend" tray item: stops managed immediately; confirms for unmanaged.
5. Invalid folder → gate prompts for Settings; bad start → failure + log path.
6. No main-thread freeze at launch (the v1 ~2s stall is gone).

## Prerequisites
- The Odysseus folder has a working `venv` (uvicorn + chroma installed) — already true at `~/Projects/odysseus`.
- v1 (full wrap) shipped — gate window + health ping already exist and are extended here.
