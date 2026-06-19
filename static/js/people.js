// static/js/people.js
// People page — list of contacts/stakeholders with per-person detail view.
// Vanilla JS, no build step. Mirrors the Planner module's injected-panel pattern
// and reuses the global CSS variables / base component styles.
//
// Exposes: window.openPeople() (list panel) and window.openPerson(id) (detail panel).

const API_BASE = window.location.origin;

let _listOpen = false;
let _detailOpen = false;
let _escHandler = null;

// --- inline monochrome SVG icons (no emoji per project style) ---
const ICON_PEOPLE =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<circle cx="9" cy="7" r="4"/>' +
  '<path d="M2 21v-2a7 7 0 0 1 7-7"/>' +
  '<circle cx="17" cy="10" r="3"/>' +
  '<path d="M22 21v-2a5 5 0 0 0-5-5"/>' +
  '</svg>';

const ICON_PERSON =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<circle cx="12" cy="7" r="4"/>' +
  '<path d="M20 21v-2a8 8 0 0 0-16 0v2"/>' +
  '</svg>';

const ICON_CLOSE =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M18 6 6 18M6 6l12 12"/>' +
  '</svg>';

const ICON_TASK =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="4" y="4" width="16" height="16" rx="3"/>' +
  '<path d="m8.5 12 2.5 2.5 4.5-5"/>' +
  '</svg>';

const ICON_MEETING =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="3" y="4" width="18" height="17" rx="2"/>' +
  '<path d="M3 9h18"/>' +
  '<path d="M8 2v4M16 2v4"/>' +
  '</svg>';

const ICON_NOTE =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="5" y="3" width="14" height="18" rx="2"/>' +
  '<path d="M9 8h6M9 12h6M9 16h4"/>' +
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

// --- API ---
async function _fetchPeople() {
  try {
    const res = await fetch(`${API_BASE}/api/people`, { credentials: 'same-origin' });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.people) ? data.people : (Array.isArray(data) ? data : []);
  } catch (e) {
    console.error('people: fetch failed', e);
    return [];
  }
}

async function _createPerson(name, role, email, area_id) {
  const body = { name, role, email };
  if (area_id) body.area_id = area_id;
  const res = await fetch(`${API_BASE}/api/people`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error('create failed');
  return await res.json();
}

async function _fetchPerson(id) {
  const res = await fetch(`${API_BASE}/api/people/${encodeURIComponent(id)}`, {
    credentials: 'same-origin',
  });
  if (!res.ok) throw new Error('fetch person failed');
  return await res.json();
}

async function _updatePerson(id, fields) {
  const res = await fetch(`${API_BASE}/api/people/${encodeURIComponent(id)}`, {
    method: 'PUT', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
  if (!res.ok) throw new Error('update person failed');
  return await res.json();
}

async function _fetchPersonPage(id) {
  const res = await fetch(`${API_BASE}/api/people/${id}/page`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('fetch person page failed');
  return await res.json();
}

// --- remove backdrop helper ---
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
function _renderPeopleList(people) {
  const list = document.getElementById('people-list');
  if (!list) return;
  if (!people.length) {
    list.innerHTML = `<div class="people-empty">No people yet. Add one below.</div>`;
    return;
  }
  list.innerHTML = people.map(p =>
    `<div class="person-card" data-id="${_esc(p.id)}" style="cursor:pointer;">` +
      `<div class="person-card-name">${_esc(p.name || '(unnamed)')}</div>` +
      (p.role ? `<div class="person-card-role">${_esc(p.role)}</div>` : '') +
      (p.email ? `<div class="person-card-email">${_esc(p.email)}</div>` : '') +
    `</div>`
  ).join('');
}

async function _refreshPeopleList() {
  const people = await _fetchPeople();
  _renderPeopleList(people);
}

function _wireList(pane, createAreaPicker) {
  pane.querySelector('#people-close')?.addEventListener('click', () => closeList());

  pane.querySelector('#people-list')?.addEventListener('click', (e) => {
    const card = e.target.closest('.person-card');
    if (!card) return;
    const id = card.dataset.id;
    if (id) {
      closeList();
      openPerson(id);
    }
  });

  const nameInput = pane.querySelector('#people-add-name');
  const roleInput = pane.querySelector('#people-add-role');
  const emailInput = pane.querySelector('#people-add-email');
  const addBtn = pane.querySelector('#people-add-btn');
  const addErr = pane.querySelector('#people-add-err');

  // createAreaPicker is a promise that resolves to the picker handle
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
      const picker = await createAreaPicker;
      await _createPerson(
        name,
        roleInput?.value.trim() || '',
        emailInput?.value.trim() || '',
        picker ? picker.value : null
      );
      if (nameInput) nameInput.value = '';
      if (roleInput) roleInput.value = '';
      if (emailInput) emailInput.value = '';
      await _refreshPeopleList();
    } catch (e) {
      if (addErr) addErr.textContent = 'Could not add person. Try again.';
      console.error(e);
    } finally {
      addBtn.disabled = false;
      addBtn.textContent = '+ Person';
    }
  });

  // Allow Enter on name/role/email fields to submit
  [nameInput, roleInput, emailInput].forEach(inp => {
    inp?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); addBtn?.click(); }
    });
  });
}

function openList() {
  if (_listOpen) return;
  _listOpen = true;
  _removeBackdrop('people-list-backdrop');

  const backdrop = document.createElement('div');
  backdrop.id = 'people-list-backdrop';
  backdrop.className = 'people-backdrop';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeList(); });

  const pane = document.createElement('div');
  pane.id = 'people-list-pane';
  pane.className = 'people-pane';
  pane.innerHTML =
    `<div class="people-header">` +
      `<span class="people-title">${ICON_PEOPLE}<span>People</span></span>` +
      `<button class="people-close" id="people-close" title="Close">${ICON_CLOSE}</button>` +
    `</div>` +
    `<div class="people-list-body" id="people-list"><div class="people-empty">Loading…</div></div>` +
    `<div class="people-add-area">` +
      `<div class="people-add-row">` +
        `<input type="text" id="people-add-name" class="people-add-input" placeholder="Name" autocomplete="off">` +
        `<input type="text" id="people-add-role" class="people-add-input" placeholder="Role (optional)" autocomplete="off">` +
        `<input type="email" id="people-add-email" class="people-add-input" placeholder="Email (optional)" autocomplete="off">` +
        `<button class="people-add-btn" id="people-add-btn">+ Person</button>` +
      `</div>` +
      `<div id="people-add-area-picker"></div>` +
      `<div class="people-add-err" id="people-add-err"></div>` +
    `</div>`;

  backdrop.appendChild(pane);
  document.body.appendChild(backdrop);

  // Mount area picker into the placeholder div; pass the promise so _wireList
  // can await it when the user clicks "+ Person".
  const pickerContainer = pane.querySelector('#people-add-area-picker');
  const createAreaPicker = (window.AreaPicker && pickerContainer)
    ? window.AreaPicker.mount(pickerContainer, {})
    : Promise.resolve(null);

  _wireList(pane, createAreaPicker);

  _refreshPeopleList();

  _removeEscHandler();
  _escHandler = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeList(); }
  };
  document.addEventListener('keydown', _escHandler);

  pane.querySelector('#people-add-name')?.focus();
}

function closeList() {
  if (!_listOpen) return;
  _listOpen = false;
  _removeBackdrop('people-list-backdrop');
  _removeEscHandler();
}

// --- Detail panel ---
function _sectionHtml(icon, label, id, items, emptyMsg, rowFn) {
  return (
    `<div class="person-section">` +
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

function _taskRow(t) {
  const overdue = t.overdue ? ' person-task-overdue' : '';
  const fromNote = t.note_title ? ` &middot; from <em>${_esc(t.note_title)}</em>` : '';
  const due = t.due_date ? ` &middot; due ${_esc(t.due_date)}` : '';
  return (
    `<div class="person-task-row${overdue}">` +
      `<span class="person-task-title">${_esc(t.title || '(untitled)')}</span>` +
      `<span class="person-task-meta">${fromNote}${due}</span>` +
    `</div>`
  );
}

function _meetingRow(m) {
  const date = m.date || m.meeting_date || '';
  return (
    `<div class="person-meeting-row">` +
      `<span class="person-meeting-summary">${_esc(m.summary || m.title || '(no summary)')}</span>` +
      (date ? `<span class="person-meeting-date">${_esc(date)}</span>` : '') +
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

function _wireDetail(pane, personId, personName) {
  pane.querySelector('#person-detail-close')?.addEventListener('click', () => closeDetail());

  pane.querySelector('#person-back')?.addEventListener('click', () => {
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

  pane.querySelector('#person-meeting-note-btn')?.addEventListener('click', () => {
    if (typeof window.openMeetingNote === 'function') {
      window.openMeetingNote?.({ personId, personName });
    }
  });
}

function _buildDetailPane(data) {
  const p = data.person || {};
  const openTasks = Array.isArray(data.open_tasks) ? data.open_tasks : [];
  const meetings = Array.isArray(data.meetings) ? data.meetings : [];
  const notes = Array.isArray(data.notes) ? data.notes : [];

  const name = p.name || '(unnamed)';
  const role = p.role || '';
  const email = p.email || '';

  return (
    `<div class="people-header">` +
      `<div class="person-header-left">` +
        `<button class="people-close" id="person-back" title="Back to People">${ICON_BACK}</button>` +
        `<span class="people-title">${ICON_PERSON}<span>${_esc(name)}</span></span>` +
      `</div>` +
      `<button class="people-close" id="person-detail-close" title="Close">${ICON_CLOSE}</button>` +
    `</div>` +
    `<div class="person-meta-bar">` +
      (role ? `<span class="person-meta-role">${_esc(role)}</span>` : '') +
      (email ? `<span class="person-meta-email">${_esc(email)}</span>` : '') +
    `</div>` +
    `<div id="person-area-picker-wrap" class="person-area-picker-wrap"></div>` +
    `<div class="person-detail-body">` +
      _sectionHtml(ICON_TASK, 'Open items', 'person-tasks-body', openTasks, 'No open items', _taskRow) +
      _sectionHtml(ICON_MEETING, 'Meetings', 'person-meetings-body', meetings, 'No meetings recorded', _meetingRow) +
      _sectionHtml(ICON_NOTE, 'Notes', 'person-notes-body', notes, 'No notes', _noteRow) +
    `</div>` +
    `<div class="person-actions-bar">` +
      `<button class="btn" id="person-meeting-note-btn">+ Meeting note</button>` +
    `</div>`
  );
}

async function openDetail(id) {
  if (_detailOpen) closeDetail();
  _detailOpen = true;
  _removeBackdrop('people-detail-backdrop');

  const backdrop = document.createElement('div');
  backdrop.id = 'people-detail-backdrop';
  backdrop.className = 'people-backdrop';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeDetail(); });

  const pane = document.createElement('div');
  pane.id = 'people-detail-pane';
  pane.className = 'people-pane';
  pane.innerHTML =
    `<div class="people-header">` +
      `<span class="people-title">${ICON_PERSON}<span>Loading…</span></span>` +
      `<button class="people-close" id="person-detail-close" title="Close">${ICON_CLOSE}</button>` +
    `</div>` +
    `<div class="person-detail-body"><div class="people-empty">Loading…</div></div>`;

  backdrop.appendChild(pane);
  document.body.appendChild(backdrop);

  // Wire close before fetch so ESC works during load
  pane.querySelector('#person-detail-close')?.addEventListener('click', () => closeDetail());

  _removeEscHandler();
  _escHandler = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeDetail(); }
  };
  document.addEventListener('keydown', _escHandler);

  try {
    // Fetch both the page (tasks/meetings/notes) and the person record (area_id)
    const [data, personRecord] = await Promise.all([
      _fetchPersonPage(id),
      _fetchPerson(id).catch(() => null),
    ]);
    pane.innerHTML = _buildDetailPane(data);
    const personName = (data.person || {}).name || '';
    _wireDetail(pane, id, personName);

    // Mount Area picker in the detail pane
    const pickerWrap = pane.querySelector('#person-area-picker-wrap');
    if (window.AreaPicker && pickerWrap) {
      const areaId = (personRecord && (personRecord.area_id || (personRecord.person || {}).area_id)) || null;
      window.AreaPicker.mount(pickerWrap, {
        selectedId: areaId,
        onChange: async function (newVal) {
          try {
            await _updatePerson(id, { area_id: newVal });
          } catch (err) {
            console.error('people: area update failed', err);
          }
        },
      });
    }
  } catch (e) {
    console.error('people: detail fetch failed', e);
    pane.innerHTML =
      `<div class="people-header">` +
        `<span class="people-title">${ICON_PERSON}<span>Error</span></span>` +
        `<button class="people-close" id="person-detail-close" title="Close">${ICON_CLOSE}</button>` +
      `</div>` +
      `<div class="person-detail-body"><div class="people-empty">Could not load person data.</div></div>`;
    pane.querySelector('#person-detail-close')?.addEventListener('click', () => closeDetail());
  }
}

function closeDetail() {
  if (!_detailOpen) return;
  _detailOpen = false;
  _removeBackdrop('people-detail-backdrop');
  _removeEscHandler();
}

// --- Public API ---
export function openPeople() {
  if (_detailOpen) closeDetail();
  openList();
}

export function openPerson(id) {
  if (_listOpen) closeList();
  openDetail(id);
}

export function closePeople() {
  closeList();
  closeDetail();
}

window.openPeople = openPeople;
window.openPerson = openPerson;

const peopleModule = { openPeople, openPerson, closePeople };
export default peopleModule;
