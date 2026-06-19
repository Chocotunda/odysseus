// static/js/areas.js
// Areas page — list of life-OS areas (Work/Personal/KM) with per-area dashboard.
// Vanilla JS, no build step. Mirrors the People module's injected-panel pattern
// and reuses the global CSS variables / base component styles.
//
// Exposes: window.openAreas() (list panel) and window.openArea(id) (dashboard).

const API_BASE = window.location.origin;

let _listOpen = false;
let _detailOpen = false;
let _escHandler = null;

// --- inline monochrome SVG icons (no emoji per project style) ---
const ICON_AREAS =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="3" y="3" width="7" height="7" rx="1"/>' +
  '<rect x="14" y="3" width="7" height="7" rx="1"/>' +
  '<rect x="3" y="14" width="7" height="7" rx="1"/>' +
  '<rect x="14" y="14" width="7" height="7" rx="1"/>' +
  '</svg>';

const ICON_AREA =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M12 2L2 7l10 5 10-5-10-5z"/>' +
  '<path d="M2 17l10 5 10-5"/>' +
  '<path d="M2 12l10 5 10-5"/>' +
  '</svg>';

const ICON_CLOSE =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M18 6 6 18M6 6l12 12"/>' +
  '</svg>';

const ICON_PERSON =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<circle cx="12" cy="7" r="4"/>' +
  '<path d="M20 21v-2a8 8 0 0 0-16 0v2"/>' +
  '</svg>';

const ICON_TASK =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="4" y="4" width="16" height="16" rx="3"/>' +
  '<path d="m8.5 12 2.5 2.5 4.5-5"/>' +
  '</svg>';

const ICON_NOTE =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="5" y="3" width="14" height="18" rx="2"/>' +
  '<path d="M9 8h6M9 12h6M9 16h4"/>' +
  '</svg>';

const ICON_MEETING =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="3" y="4" width="18" height="17" rx="2"/>' +
  '<path d="M3 9h18"/>' +
  '<path d="M8 2v4M16 2v4"/>' +
  '</svg>';

const ICON_BACK =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M19 12H5M12 19l-7-7 7-7"/>' +
  '</svg>';

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Palette of colors for the "+ Area" form color picker
const AREA_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e',
  '#3b82f6', '#8b5cf6', '#ec4899', '#14b8a6',
];

// --- API ---
async function _fetchAreas() {
  try {
    const res = await fetch(`${API_BASE}/api/areas`, { credentials: 'same-origin' });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.areas) ? data.areas : (Array.isArray(data) ? data : []);
  } catch (e) {
    console.error('areas: fetch failed', e);
    return [];
  }
}

async function _createArea(name, color) {
  const res = await fetch(`${API_BASE}/api/areas`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, color }),
  });
  if (!res.ok) throw new Error('create failed');
  return await res.json();
}

async function _fetchAreaPage(id) {
  const res = await fetch(`${API_BASE}/api/areas/${id}/page`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('fetch area page failed');
  return await res.json();
}

// --- backdrop/esc helpers ---
function _removeBackdrop(id) {
  try { document.getElementById(id)?.remove(); } catch (_) {}
}

function _removeEscHandler() {
  if (_escHandler) {
    document.removeEventListener('keydown', _escHandler);
    _escHandler = null;
  }
}

// --- List panel ---
function _colorSwatchHtml(selectedColor) {
  return AREA_COLORS.map(c =>
    `<span class="area-color-swatch${c === selectedColor ? ' selected' : ''}" ` +
    `data-color="${_esc(c)}" style="background:${_esc(c)}" title="${_esc(c)}"></span>`
  ).join('');
}

function _renderAreasList(areas) {
  const list = document.getElementById('areas-list');
  if (!list) return;
  if (!areas.length) {
    list.innerHTML = `<div class="people-empty">No areas yet. Add one below.</div>`;
    return;
  }
  list.innerHTML = areas.map(a =>
    `<div class="area-card" data-id="${_esc(a.id)}" style="cursor:pointer;">` +
      `<span class="area-dot" style="background:${_esc(a.color || '#888')}"></span>` +
      `<span class="area-card-name">${_esc(a.name || '(unnamed)')}</span>` +
    `</div>`
  ).join('');
}

async function _refreshAreasList() {
  const areas = await _fetchAreas();
  _renderAreasList(areas);
}

function _wireList(pane) {
  pane.querySelector('#areas-close')?.addEventListener('click', () => closeList());

  pane.querySelector('#areas-list')?.addEventListener('click', (e) => {
    const card = e.target.closest('.area-card');
    if (!card) return;
    const id = card.dataset.id;
    if (id) {
      closeList();
      openArea(id);
    }
  });

  // Color picker: clicking a swatch selects it
  let _pickedColor = AREA_COLORS[0];
  pane.querySelector('#areas-color-swatches')?.addEventListener('click', (e) => {
    const swatch = e.target.closest('.area-color-swatch');
    if (!swatch) return;
    _pickedColor = swatch.dataset.color || AREA_COLORS[0];
    pane.querySelectorAll('.area-color-swatch').forEach(s => s.classList.remove('selected'));
    swatch.classList.add('selected');
  });

  const nameInput = pane.querySelector('#areas-add-name');
  const addBtn = pane.querySelector('#areas-add-btn');
  const addErr = pane.querySelector('#areas-add-err');

  addBtn?.addEventListener('click', async () => {
    const name = nameInput?.value.trim();
    if (!name) {
      if (addErr) addErr.textContent = 'Name is required.';
      nameInput?.focus();
      return;
    }
    if (addErr) addErr.textContent = '';
    addBtn.disabled = true;
    addBtn.textContent = 'Adding…';
    try {
      await _createArea(name, _pickedColor);
      if (nameInput) nameInput.value = '';
      await _refreshAreasList();
    } catch (e) {
      if (addErr) addErr.textContent = 'Could not add area. Try again.';
      console.error(e);
    } finally {
      addBtn.disabled = false;
      addBtn.textContent = '+ Area';
    }
  });

  nameInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addBtn?.click(); }
  });
}

function openList() {
  if (_listOpen) return;
  _listOpen = true;
  _removeBackdrop('areas-list-backdrop');

  const backdrop = document.createElement('div');
  backdrop.id = 'areas-list-backdrop';
  backdrop.className = 'people-backdrop';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeList(); });

  const pane = document.createElement('div');
  pane.id = 'areas-list-pane';
  pane.className = 'people-pane';
  pane.innerHTML =
    `<div class="people-header">` +
      `<span class="people-title">${ICON_AREAS}<span>Areas</span></span>` +
      `<button class="people-close" id="areas-close" title="Close">${ICON_CLOSE}</button>` +
    `</div>` +
    `<div class="people-list-body" id="areas-list"><div class="people-empty">Loading…</div></div>` +
    `<div class="people-add-area">` +
      `<div class="people-add-row">` +
        `<input type="text" id="areas-add-name" class="people-add-input" placeholder="Area name" autocomplete="off">` +
        `<button class="people-add-btn" id="areas-add-btn">+ Area</button>` +
      `</div>` +
      `<div class="area-color-row" id="areas-color-swatches">` +
        _colorSwatchHtml(AREA_COLORS[0]) +
      `</div>` +
      `<div class="people-add-err" id="areas-add-err"></div>` +
    `</div>`;

  backdrop.appendChild(pane);
  document.body.appendChild(backdrop);
  _wireList(pane);

  _refreshAreasList();

  _removeEscHandler();
  _escHandler = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeList(); }
  };
  document.addEventListener('keydown', _escHandler);

  pane.querySelector('#areas-add-name')?.focus();
}

function closeList() {
  if (!_listOpen) return;
  _listOpen = false;
  _removeBackdrop('areas-list-backdrop');
  _removeEscHandler();
}

// --- Detail panel ---
function _sectionHtml(icon, label, id, items, emptyMsg, rowFn) {
  return (
    `<div class="person-section area-section">` +
      `<div class="person-section-header" data-toggle="${_esc(id)}">` +
        `<span class="person-section-icon">${icon}</span>` +
        `<span class="person-section-label">${_esc(label)}</span>` +
        `<span class="person-section-count">${items.length}</span>` +
      `</div>` +
      `<div class="person-section-body" id="${_esc(id)}">` +
        (items.length
          ? items.map(rowFn).join('')
          : `<div class="person-section-empty">${_esc(emptyMsg)}</div>`) +
      `</div>` +
    `</div>`
  );
}

function _personRow(p) {
  return (
    `<div class="person-note-row">` +
      `<span class="person-note-title">${_esc(p.name || '(unnamed)')}</span>` +
      (p.role ? `<span class="person-task-meta"> &middot; ${_esc(p.role)}</span>` : '') +
    `</div>`
  );
}

function _taskRow(t) {
  const due = t.due_date ? ` &middot; due ${_esc(t.due_date)}` : '';
  const overdue = t.overdue ? ' person-task-overdue' : '';
  return (
    `<div class="person-task-row${overdue}">` +
      `<span class="person-task-title">${_esc(t.title || '(untitled)')}</span>` +
      `<span class="person-task-meta">${due}</span>` +
    `</div>`
  );
}

function _noteRow(n) {
  return (
    `<div class="person-note-row">` +
      `<span class="person-note-title">${_esc(n.title || '(untitled note)')}</span>` +
    `</div>`
  );
}

function _meetingRow(m) {
  const date = m.dtstart || m.date || '';
  const summary = m.summary || m.title || '(no summary)';
  return (
    `<div class="person-meeting-row">` +
      `<span class="person-meeting-summary">${_esc(summary)}</span>` +
      (date ? `<span class="person-meeting-date">${_esc(date)}</span>` : '') +
    `</div>`
  );
}

function _wireDetail(pane) {
  pane.querySelector('#area-detail-close')?.addEventListener('click', () => closeDetail());

  pane.querySelector('#area-back')?.addEventListener('click', () => {
    closeDetail();
    openList();
  });

  pane.querySelectorAll('.person-section-header[data-toggle]').forEach(hdr => {
    hdr.addEventListener('click', () => {
      const bodyId = hdr.dataset.toggle;
      const body = document.getElementById(bodyId);
      if (body) body.classList.toggle('collapsed');
      hdr.classList.toggle('collapsed');
    });
  });
}

function _buildDetailPane(data) {
  const area = data.area || {};
  const people = Array.isArray(data.people) ? data.people : [];
  const openTasks = Array.isArray(data.open_tasks) ? data.open_tasks : [];
  const notes = Array.isArray(data.notes) ? data.notes : [];
  const meetings = Array.isArray(data.meetings) ? data.meetings : [];

  const name = area.name || '(unnamed area)';
  const color = area.color || '#888';

  return (
    `<div class="people-header">` +
      `<div class="person-header-left">` +
        `<button class="people-close" id="area-back" title="Back to Areas">${ICON_BACK}</button>` +
        `<span class="people-title">` +
          `${ICON_AREA}` +
          `<span class="area-dot" style="background:${_esc(color)}"></span>` +
          `<span>${_esc(name)}</span>` +
        `</span>` +
      `</div>` +
      `<button class="people-close" id="area-detail-close" title="Close">${ICON_CLOSE}</button>` +
    `</div>` +
    `<div class="person-detail-body">` +
      _sectionHtml(ICON_PERSON, 'People', 'area-people-body', people, 'No people linked', _personRow) +
      _sectionHtml(ICON_TASK, 'Open tasks', 'area-tasks-body', openTasks, 'No open tasks', _taskRow) +
      _sectionHtml(ICON_NOTE, 'Notes', 'area-notes-body', notes, 'No notes', _noteRow) +
      _sectionHtml(ICON_MEETING, 'Meetings', 'area-meetings-body', meetings, 'No meetings', _meetingRow) +
    `</div>`
  );
}

async function openDetail(id) {
  if (_detailOpen) closeDetail();
  _detailOpen = true;
  _removeBackdrop('areas-detail-backdrop');

  const backdrop = document.createElement('div');
  backdrop.id = 'areas-detail-backdrop';
  backdrop.className = 'people-backdrop';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeDetail(); });

  const pane = document.createElement('div');
  pane.id = 'areas-detail-pane';
  pane.className = 'people-pane';
  pane.innerHTML =
    `<div class="people-header">` +
      `<span class="people-title">${ICON_AREA}<span>Loading…</span></span>` +
      `<button class="people-close" id="area-detail-close" title="Close">${ICON_CLOSE}</button>` +
    `</div>` +
    `<div class="person-detail-body"><div class="people-empty">Loading…</div></div>`;

  backdrop.appendChild(pane);
  document.body.appendChild(backdrop);

  // Wire close before fetch so ESC works during load
  pane.querySelector('#area-detail-close')?.addEventListener('click', () => closeDetail());

  _removeEscHandler();
  _escHandler = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeDetail(); }
  };
  document.addEventListener('keydown', _escHandler);

  try {
    const data = await _fetchAreaPage(id);
    pane.innerHTML = _buildDetailPane(data);
    _wireDetail(pane);
  } catch (e) {
    console.error('areas: detail fetch failed', e);
    pane.innerHTML =
      `<div class="people-header">` +
        `<span class="people-title">${ICON_AREA}<span>Error</span></span>` +
        `<button class="people-close" id="area-detail-close" title="Close">${ICON_CLOSE}</button>` +
      `</div>` +
      `<div class="person-detail-body"><div class="people-empty">Could not load area data.</div></div>`;
    pane.querySelector('#area-detail-close')?.addEventListener('click', () => closeDetail());
  }
}

function closeDetail() {
  if (!_detailOpen) return;
  _detailOpen = false;
  _removeBackdrop('areas-detail-backdrop');
  _removeEscHandler();
}

// --- Public API ---
export function openAreas() {
  if (_detailOpen) closeDetail();
  openList();
}

export function openArea(id) {
  if (_listOpen) closeList();
  openDetail(id);
}

export function closeAreas() {
  closeList();
  closeDetail();
}

window.openAreas = openAreas;
window.openArea = openArea;

const areasModule = { openAreas, openArea, closeAreas };
export default areasModule;
