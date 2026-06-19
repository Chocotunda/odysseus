# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Odysseus is a self-hosted, local-first AI workspace (chat, autonomous agents, deep research, model serving via Cookbook, email, calendar, notes/tasks, documents). FastAPI + SQLite (SQLAlchemy) backend, ChromaDB vector store, vanilla-JS frontend served from `static/`. Python 3.11+.

**Branch model:** PRs land on `dev` (the default branch, may be unstable). `main` is the curated, user-facing release branch. Never target `main` directly.

> **Agent-generated PR policy (from CONTRIBUTING.md):** maintainers ask LLM agents to open an *issue* describing the problem rather than opening PRs directly; bulk agent PRs that don't match the project's visual style/format are closed without review. Keep this in mind before proposing to open PRs.

## Commands

Setup (manual dev):
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python setup.py                                   # creates data dirs, DB, admin user (safe to re-run)
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```
First run prints a temporary admin password to the terminal. Docker: `docker compose up -d --build` (also starts ChromaDB, SearXNG, ntfy), UI at `http://localhost:7000`.

Tests (pytest, `asyncio_mode = auto`):
```bash
python -m pytest                                  # full suite
python -m pytest tests/test_<file>.py             # single file
python -m pytest tests/test_<file>.py::test_name  # single test
python -m pytest -m area_security                  # by taxonomy area marker
python -m pytest -m "area_services and sub_cookbook"
python3 tests/run_focus.py --area services --sub-area cookbook   # preferred focused runner
python3 tests/run_focus.py --fast                  # fast lane (excludes `slow`-marked)
python3 tests/run_focus.py --last-failed
```
Tests are auto-tagged at collection by `tests/conftest.py` / `tests/_taxonomy.py` with an `area_*` and a finer `sub_*` marker derived from the filename. Read `tests/TESTING_STANDARD.md` (the standard) and `tests/README.md` (helper reference) before adding tests or helpers. CI runs `python -m pytest -q` but it is currently `continue-on-error` (known flaky); don't assume green CI means a clean suite.

Lint / fast checks (what CI actually enforces — there is no formatter/linter config):
```bash
python -m compileall -q app.py core routes src services scripts tests   # syntax only
node --check static/js/<file-you-changed>.js                            # JS syntax (skip static/lib vendored)
docker compose config                                                   # validate compose changes
```

CLI: `scripts/odysseus` is a git-style dispatcher for the `scripts/odysseus-*` subcommands (mail, calendar, cookbook, memory, skills, notes, tasks, research, backup, …). Run `scripts/odysseus` with no args to list them.

## Architecture

**`app.py` is a slim orchestrator.** It wires middleware (security headers, CORS, gzip), serves `static/`, and registers ~45 routers via `include_router(...)`. It deliberately holds almost no business logic — feature work happens in `routes/`, `src/`, and `services/`.

**Request flow:** `routes/*` (HTTP layer, thin) → `src/*` (business logic) → `core/database.py` (SQLAlchemy models + `SessionLocal`) and `services/*`. Most route modules export a `setup_<feature>_routes(...)` factory that `app.py` calls; a few export a ready router object (`auth_router`, `email_router`, `calendar_router`, `memory_router`, `document_router`, `upload_router`).

**Key `src/` modules** (largest = most central):
- `agent_loop.py` — the streaming agent loop. Wraps `stream_llm()` with multi-round tool execution; the LLM emits fenced tool blocks that get parsed and executed (`src/agent_tools/`: filesystem, subprocess, web, document tools). `MAX_AGENT_ROUNDS` bounds iterations.
- `llm_core.py` — provider-agnostic LLM streaming/dispatch (vLLM, llama.cpp, Ollama, OpenAI, OpenRouter, GitHub Copilot). `endpoint_resolver.py` / `model_discovery.py` / `model_context.py` resolve which backend+model to call.
- `ai_interaction.py`, `chat_handler.py`, `chat_processor.py` — chat orchestration and message handling.
- `builtin_actions.py` — registry of automation actions the **task scheduler** runs *without* an LLM call (email triage, calendar tidy, research, etc.). `TaskNoop(BaseException)` signals "nothing to do" without tripping `except Exception`.
- `deep_research.py` + `services/research/` — multi-step gather/read/synthesize, rendered via `visual_report.py` (markdown → `nh3`-sanitized HTML under a relaxed CSP, since report content is untrusted LLM-over-crawled-pages output).
- Memory/RAG: `memory.py`, `memory_vector.py`, `memory_provider.py`, `embeddings.py`, `embedding_lanes.py`, `chroma_client.py`, `rag_manager.py` — vector + keyword retrieval over ChromaDB with local fastembed (ONNX) embeddings; degrades to keyword fallback if vector deps are missing.
- `mcp_manager.py` / `mcp_oauth.py` / `builtin_mcp.py` + `mcp_servers/` — MCP client + bundled servers (email, memory, image gen, RAG).
- `caldav_sync.py` / `caldav_writeback.py` — CalDAV against Radicale/Nextcloud/Apple/Fastmail.
- Cookbook (hardware-aware model recommend/download/serve): `services/hwfit/` fit scoring + `services/hwfit/data/hf_models.json` catalog, `cookbook_serve_lifecycle.py`. Needs `tmux` for background downloads/serves.

**`core/`** is the stable base layer: `database.py` (all SQLAlchemy models + session factory), `auth.py` (`AuthManager`, 2FA via pyotp), `session_manager.py`, `middleware.py` (security headers / CORS preflight), `platform_compat.py` (Windows/macOS/Linux quirks), `constants.py` (re-exports from `src/constants.py` for back-compat).

### Routes ↔ logic map (`routes/` is HTTP-thin, logic lives in `src/`/`services/`)

Routes follow a `<feature>_routes.py` (+ often `<feature>_helpers.py`) naming convention. The largest/most central surfaces:
- **chat** — `chat_routes.py` (~80K), `chat_helpers.py` → `src/chat_*`, `src/agent_loop.py`, `src/ai_interaction.py`, `src/llm_core.py`.
- **cookbook** (model serving) — `cookbook_routes.py` (~160K, the single biggest module), `cookbook_helpers.py` → `services/hwfit/` (`fit.py`, `hardware.py`, `image_models.py`) + `src/cookbook_serve_lifecycle.py`; `hwfit_routes.py` for fit scoring.
- **email** — `email_routes.py` (~156K), `email_helpers.py`, `email_pollers.py` → `src/email_thread_parser.py` + `mcp_servers/email_server.py`.
- **model config** — `model_routes.py` (~100K), `copilot_routes.py`, `chatgpt_subscription_routes.py`, `device_flow.py` → `src/endpoint_resolver.py`, `model_discovery.py`, `integrations.py`.
- **documents/gallery** — `document_routes.py`, `gallery_routes.py`, `editor_draft_routes.py` → `src/document_processor.py`, `document_actions.py`, `pdf_forms.py`, `personal_docs.py`.
- **research** — `research_routes.py` → `src/deep_research.py` + `services/research/`.
- **calendar/contacts** — `calendar_routes.py`, `contacts_routes.py` → `src/caldav_sync.py`, `caldav_writeback.py`.
- **memory/skills/notes/tasks** — `memory_routes.py`, `skills_routes.py`, `note_routes.py`, `task_routes.py` → `src/memory*.py`, `src/builtin_actions.py` (scheduler actions), `services/memory/`.
- **shell/MCP/webhooks/sessions/auth** — `shell_routes.py` (→ `services/shell/`), `mcp_routes.py` (→ `src/mcp_manager.py`), `webhook_routes.py`, `session_routes.py`, `history_routes.py`, `auth_routes.py` (→ `core/auth.py`).
- Coding-agent bridges: `codex_routes.py` + `integrations/codex/`, `claude_routes` + `integrations/claude/` (skills/plugins for external CLI agents).

### `services/` — self-contained service packages (each typically exposes `service.py`)
`hwfit` (hardware detection + model fit scoring, catalog in `hwfit/data/hf_models.json`), `memory` (`memory_extractor.py` + vector lane), `research` (`research_handler.py`), `search` (`core.py`/`content.py`/`cache.py`/`analytics.py`, talks to SearXNG), `shell`, `stt`, `tts`, `youtube` (transcript fetch), `docs`, `faces`.

### Agent tool protocol (`src/agent_tools/`)
The LLM drives tools by emitting fenced tool blocks that `agent_loop.py` parses and dispatches through `TOOL_HANDLERS` (and native function-calling via `FUNCTION_TOOL_SCHEMAS`). The facade `src/agent_tools/__init__.py` re-exports from `tool_parsing.py`, `tool_schemas.py`, `tool_execution.py`, `tool_implementations.py`. A tool is only callable if its tag is in **`TOOL_TAGS`** — adding a new tool means registering it in both the handler map/schemas *and* `TOOL_TAGS`, or native function calls get rejected as "Unknown function call". Tool families span filesystem, bash/python subprocess, web search/fetch, documents, sessions/pipeline, memory/skills/tasks, email, calendar/contacts, MCP/endpoints/webhooks/tokens, and the full cookbook serving surface. `MAX_AGENT_ROUNDS=50`, `SHELL_TIMEOUT=60`, `PYTHON_TIMEOUT=30`. Per-owner/plan-mode tool gating lives in `src/tool_security.py` / `src/tool_policy.py`.

### Data model (`core/database.py`)
SQLite by default at `./data/app.db` (override with `DATABASE_URL`). Models are owner-scoped. Notable: `Session`/`ChatMessage` (conversations), `Document`/`DocumentVersion`/`EditorDraft`, `GalleryAlbum`/`GalleryImage`, `EmailAccount`, `ModelEndpoint`/`ProviderAuthSession` (model config + OAuth), `McpServer`, `UserTool`/`UserToolData` (custom skills), `CrewMember` (multi-agent "crew"), `ScheduledTask`/`TaskRun` (scheduler), `Memory`, `Note`, `CalendarCal`/`CalendarEvent`, `Webhook`, `ApiToken`, `Comparison`, `Signature`, `Integration`. Sensitive columns use the `EncryptedText` type (key derived via `cryptography`).

### MCP servers (`mcp_servers/`) and companion
Bundled MCP servers: `email_server.py` (~89K), `memory_server.py`, `rag_server.py`, `image_gen_server.py`. `companion/` is the phone-pairing companion (`pairing.py`, `routes.py`) registered via `setup_companion_routes`.

### Frontend (`static/`)
Vanilla JS, no build step. `index.html` (~208K) + `app.js` (~175K) bootstrap; ~87 feature modules in `static/js/` (e.g. `chat.js`, `document.js`, `settings.js`, `emailLibrary.js`, `notes.js`, `slashCommands.js`, `galleryEditor.js`). Single global stylesheet `static/style.css` (~37K) holds all CSS variables. Vendored third-party libs live in `static/lib/` (excluded from `node --check`). PWA via `manifest.json`; separate `login.html`.

### Runtime services & key env vars
Docker Compose bundles four services: **odysseus**, **chromadb**, **searxng** (web search), **ntfy** (push). All bind to `127.0.0.1` by default. Config is env-driven (`.env`, parsed with `utf-8-sig`); see `.env.example`. Frequently relevant vars: `APP_BIND`/`APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL`, `ODYSSEUS_DATA_DIR` (the one place that reads the data root), `ODYSSEUS_ADMIN_USER`/`ODYSSEUS_ADMIN_PASSWORD`, `LLM_HOSTS`/`OLLAMA_BASE_URL`/`LM_STUDIO_URL`/`OPENAI_API_KEY`, `EMBEDDING_URL`/`EMBEDDING_MODEL`/`FASTEMBED_MODEL`, `CHROMADB_HOST`/`CHROMADB_PORT`, `SEARXNG_INSTANCE`/`SEARXNG_SECRET`, `NTFY_BASE_URL`, `ALLOWED_ORIGINS`/`SECURE_COOKIES`/`LOCALHOST_BYPASS`, `COMPOSE_FILE` (GPU overlays). The various `ODYSSEUS_*_MAX_BYTES` cap upload sizes. `ODYSSEUS_INPROCESS_TASKS`/`ODYSSEUS_INPROCESS_POLLERS` control whether the scheduler and email pollers run in-process.

## Conventions that matter here

- **Paths and config go through constants, never literals.** `src/constants.py` is the single source of truth for every persisted path and config value (`AUTH_FILE`, `SETTINGS_FILE`, `DATA_DIR`, `CHROMA_DIR`, `TTS_CACHE_DIR`, …). Do **not** build writable paths from `Path(__file__)`, hardcode `/app/...`, or use relative `"data/..."` strings — the source tree is read-only in Docker. Use `DATA_DIR` directly only for dynamic per-owner paths with no fixed name. If a path/value has no constant and is used in >1 place, add one to `src/constants.py`. For loopback URLs use `internal_api_base()` (honors `ODYSSEUS_INTERNAL_BASE`/`APP_PORT`), never `http://localhost:7000`.
- **Multi-user / owner scoping is a security boundary.** Data is owner-scoped; use `owner_filter` (`src/auth_helpers.py`) on queries. Owner-isolation has its own `area_security` / `sub_owner_scope` tests — preserve it.
- **Untrusted-content handling is deliberate.** LLM output, crawled pages, and rendered reports are treated as untrusted (`prompt_security.py`, `nh3` sanitization, tool-block confinement in `tool_security.py` / `tool_policy.py`). Don't loosen sanitization or tool gating.
- **Visual style is enforced and PRs ignoring it are closed.** For any change touching `static/` (CSS/HTML/SVG/JS that draws DOM): reuse existing CSS variables (`--red`, `--fg`, `--bg`, `--card`, `--border`) and existing button/input/card classes, **no Unicode emoji in UI or code** (use inline monochrome SVG), monospaced `Fira Code`, dark theme default. Run the app and attach a screenshot for visual changes — type-checks/tests are not sufficient.
- **Commits:** Conventional Commits (`type(scope): summary`), e.g. `fix(search): ...`, `feat(notes): ...`. Keep PRs small and single-purpose; don't mix file moves with logic changes.
- **Cross-platform:** code runs on Linux/macOS/Windows and both native + Docker. Note the Windows MIME/symlink and UTF-8-BOM `.env` workarounds at the top of `app.py`; guard directory creation so an unwritable path degrades gracefully instead of crashing at import.
