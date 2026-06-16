// static/js/planner.js
// Planner page — Phase 1: NL quick-capture + a filterable task list.
// Vanilla JS, no build step. Mirrors the Notes module's injected-panel pattern
// and reuses the global CSS variables / base component styles.
//
// The planner is the user-facing daily-planning surface; it is deliberately
// separate from the scheduler's Tasks page (ScheduledTask automation).

const API_BASE = window.location.origin;

let _open = false;
let _items = [];
let _filter = 'all';   // all | today | backlog | done
let _capturing = false;
const _enriching = new Set();   // ids currently being polled for AI enrichment

// --- inline monochrome SVG icons (no emoji per project style) ---
const ICON_PLANNER =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18"/>' +
  '<path d="M8 2v4M16 2v4"/><path d="m9 15 2 2 4-4"/></svg>';
const ICON_CLOSE =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';
const ICON_CHECK_EMPTY =
  '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="3"/></svg>';
const ICON_CHECK_DONE =
  '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="3"/>' +
  '<path d="m8.5 12 2.5 2.5 4.5-5"/></svg>';

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _todayStr() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// --- API ---
async function _fetchItems() {
  try {
    const res = await fetch(`${API_BASE}/api/planner/items`, { credentials: 'same-origin' });
    if (!res.ok) { _items = []; return; }
    const data = await res.json();
    _items = data.items || [];
  } catch (e) {
    console.error('planner: fetch failed', e);
    _items = [];
  }
}

async function _capture(text) {
  const res = await fetch(`${API_BASE}/api/planner/capture`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error('capture failed');
  return await res.json();
}

async function _getItem(id) {
  const res = await fetch(`${API_BASE}/api/planner/items/${id}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('get failed');
  return await res.json();
}

async function _complete(id) {
  const res = await fetch(`${API_BASE}/api/planner/items/${id}/complete`, {
    method: 'POST', credentials: 'same-origin',
  });
  if (!res.ok) throw new Error('complete failed');
  return await res.json();
}

async function _plan(id, day) {
  const res = await fetch(`${API_BASE}/api/planner/items/${id}/plan`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ planned_day: day }),
  });
  if (!res.ok) throw new Error('plan failed');
  return await res.json();
}

// --- rendering ---
function _matchesFilter(it) {
  const today = _todayStr();
  if (_filter === 'done') return it.status === 'done';
  if (it.status === 'done') return false;
  if (_filter === 'today') return it.planned_day === today;
  if (_filter === 'backlog') return !it.planned_day;
  return true; // all (open)
}

function _itemRow(it) {
  const done = it.status === 'done';
  const prio = it.priority === 'urgent' ? 'prio-urgent'
    : it.priority === 'important' ? 'prio-important' : '';
  const meta = [];
  if (it.due_date) meta.push(`due ${_esc(it.due_date)}`);
  else if (it.planned_day) meta.push(_esc(it.planned_day));
  if (it.estimate_minutes) meta.push(`~${it.estimate_minutes}m`);
  if (it.priority && it.priority !== 'normal' && it.priority !== 'none') meta.push(_esc(it.priority));
  if (!done && _enriching.has(it.id)) {
    meta.push('<span class="planner-ai-pending">enriching…</span>');
  }
  return (
    `<div class="planner-item ${prio} ${done ? 'done' : ''}" data-id="${_esc(it.id)}">` +
      `<button class="planner-check" data-act="toggle" title="${done ? 'Completed' : 'Mark done'}">` +
        `${done ? ICON_CHECK_DONE : ICON_CHECK_EMPTY}</button>` +
      `<div class="planner-item-main">` +
        `<div class="planner-item-title">${_esc(it.title) || '<span style="opacity:.4">(untitled)</span>'}</div>` +
        (meta.length ? `<div class="planner-item-meta">${meta.map(m => `<span>${m}</span>`).join('')}</div>` : '') +
      `</div>` +
    `</div>`
  );
}

function _render() {
  const list = document.getElementById('planner-list');
  if (!list) return;
  const shown = _items.filter(_matchesFilter);
  if (!shown.length) {
    list.innerHTML = `<div class="planner-empty">Nothing here yet. Capture a task above.</div>`;
  } else {
    list.innerHTML = shown.map(_itemRow).join('');
  }
  document.querySelectorAll('#planner-filters .planner-filter').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === _filter);
  });
}

async function _onCapture() {
  const input = document.getElementById('planner-capture-input');
  if (!input || _capturing) return;
  const text = input.value.trim();
  if (!text) return;
  _capturing = true;
  input.disabled = true;
  const hint = document.getElementById('planner-hint');
  if (hint) hint.textContent = 'Capturing…';
  try {
    const created = await _capture(text);   // returns instantly (raw item)
    input.value = '';
    await _fetchItems();                      // raw item visible immediately
    _render();
    if (created && created.id) _pollEnrichment(created.id);
  } catch (e) {
    if (hint) hint.textContent = 'Could not capture — is the model running?';
    console.error(e);
  } finally {
    _capturing = false;
    input.disabled = false;
    if (hint && hint.textContent === 'Capturing…') {
      hint.textContent = 'Enter to capture · the local model fills in priority, estimate & due date';
    }
    input.focus();
  }
}

// Poll a freshly-captured item until the background AI pass lands (or give up).
async function _pollEnrichment(id) {
  _enriching.add(id);
  _render();
  try {
    for (let attempt = 0; attempt < 12; attempt++) {
      await new Promise(r => setTimeout(r, 700));
      if (!_open) return;
      let it;
      try { it = await _getItem(id); }
      catch { return; }
      const idx = _items.findIndex(x => x.id === id);
      if (idx >= 0) _items[idx] = it;
      if (it.ai_enriched) return;   // enrichment finished (success or handled failure)
      _render();
    }
  } finally {
    _enriching.delete(id);
    _render();
  }
}

function _wire(pane) {
  pane.querySelector('#planner-close')?.addEventListener('click', () => closePanel());
  const input = pane.querySelector('#planner-capture-input');
  input?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _onCapture(); }
  });
  pane.querySelector('#planner-filters')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.planner-filter');
    if (!btn) return;
    _filter = btn.dataset.filter;
    _render();
  });
  pane.querySelector('#planner-list')?.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const row = btn.closest('.planner-item');
    const id = row?.dataset.id;
    if (!id) return;
    if (btn.dataset.act === 'toggle') {
      try { await _complete(id); await _fetchItems(); _render(); }
      catch (err) { console.error(err); }
    }
  });
}

export function openPanel() {
  if (_open) return;
  _open = true;
  try { document.getElementById('planner-backdrop')?.remove(); } catch {}

  const backdrop = document.createElement('div');
  backdrop.id = 'planner-backdrop';
  backdrop.className = 'planner-backdrop';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closePanel(); });

  const pane = document.createElement('div');
  pane.id = 'planner-pane';
  pane.className = 'planner-pane';
  pane.innerHTML =
    `<div class="planner-header">` +
      `<span class="planner-title">${ICON_PLANNER}<span>Planner</span></span>` +
      `<button class="planner-close" id="planner-close" title="Close">${ICON_CLOSE}</button>` +
    `</div>` +
    `<div class="planner-capture">` +
      `<input type="text" id="planner-capture-input" class="planner-capture-input" ` +
        `placeholder="Capture a task…  e.g. ship report friday ~45m urgent" autocomplete="off">` +
      `<div class="planner-hint" id="planner-hint">Enter to capture · the local model fills in priority, estimate &amp; due date</div>` +
    `</div>` +
    `<div class="planner-filters" id="planner-filters">` +
      `<button class="planner-filter active" data-filter="all">All</button>` +
      `<button class="planner-filter" data-filter="today">Today</button>` +
      `<button class="planner-filter" data-filter="backlog">Backlog</button>` +
      `<button class="planner-filter" data-filter="done">Done</button>` +
    `</div>` +
    `<div class="planner-list" id="planner-list"><div class="planner-empty">Loading…</div></div>`;

  backdrop.appendChild(pane);
  document.body.appendChild(backdrop);
  _wire(pane);
  document.getElementById('planner-capture-input')?.focus();

  _fetchItems().then(_render);

  _escHandler = (e) => { if (e.key === 'Escape') closePanel(); };
  document.addEventListener('keydown', _escHandler);
}

let _escHandler = null;

export function closePanel() {
  if (!_open) return;
  _open = false;
  try { document.getElementById('planner-backdrop')?.remove(); } catch {}
  if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
}

export function togglePanel() { if (_open) closePanel(); else openPanel(); }
export function isPanelOpen() { return _open; }

const plannerModule = { openPanel, closePanel, togglePanel, isPanelOpen };
export default plannerModule;
