// static/js/today.js
// /today — daily day-planner. Two views (Overview / Timeline) over one day
// model (tasks + meetings + areas). Mirrors the Areas/People injected-panel
// pattern; reuses global CSS vars. No build step, no emoji.
//
// Exposes: window.openToday().

const API_BASE = window.location.origin;
const HOUR_START = 6, HOUR_END = 22, PX_PER_MIN = 0.8, DEFAULT_EST = 30;

let _open = false;
let _escHandler = null;
let _view = 'overview';                 // 'overview' | 'timeline'
let _day = null;                        // 'YYYY-MM-DD' currently shown

const ICON_TODAY =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
  '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>';
const ICON_CLOSE =
  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function _shiftDay(day, delta) {
  const [y, m, d] = day.split('-').map(Number);
  const dt = new Date(y, m - 1, d + delta);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}

async function _fetchDay(day) {
  const res = await fetch(`${API_BASE}/api/today?day=${encodeURIComponent(day)}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`today ${res.status}`);
  return res.json();
}
async function _fetchTask(id) {
  const res = await fetch(`${API_BASE}/api/today/task/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`task ${res.status}`);
  return res.json();
}
async function _completeTask(id) {
  await fetch(`${API_BASE}/api/planner/items/${encodeURIComponent(id)}/complete`,
              { method: 'POST', credentials: 'same-origin' });
}

function _fmtTime(hhmm) { return hhmm || ''; }
function _evTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return _esc(String(iso).slice(11, 16));
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function _areaChip(item) {
  if (!item.area_name) return '';
  return `<span class="today-area-chip" style="border-color:${_esc(item.area_color || '#888')}">` +
         `${_esc(item.area_name)}</span>`;
}

function _taskRow(t, cls) {
  const est = t.estimate_minutes ? ` &middot; ~${t.estimate_minutes}m` : '';
  const due = t.due_date ? ` &middot; due ${_esc(t.due_date)}` : '';
  return `<div class="today-item ${cls}" data-task="${_esc(t.id)}">` +
    `<span class="today-item-title">${_esc(t.title || '(untitled)')}</span>` +
    _areaChip(t) +
    `<span class="today-item-meta">${_esc(t.priority || '')}${est}${due}</span></div>`;
}
function _meetingRow(m) {
  return `<div class="today-item today-meeting" data-meeting="${_esc(m.uid)}">` +
    `<span class="today-item-time">${m.all_day ? 'all-day' : _evTime(m.dtstart)}</span>` +
    `<span class="today-item-title">${_esc(m.summary || '(busy)')}</span>` +
    _areaChip(m) +
    (m.location ? `<span class="today-item-meta">${_esc(m.location)}</span>` : '') + `</div>`;
}

function _section(label, count, bodyHtml, empty) {
  return `<div class="today-section"><div class="today-section-header">${_esc(label)}` +
    `<span class="today-count">${count}</span></div>` +
    `<div class="today-section-body">${count ? bodyHtml : `<div class="today-empty">${_esc(empty)}</div>`}</div></div>`;
}

function _renderOverview(v) {
  const cap = v.capacity_minutes ? `${Math.floor(v.capacity_minutes / 60)}h ${v.capacity_minutes % 60}m planned` : 'nothing planned';
  const overdue = v.is_today
    ? _section('Overdue', v.overdue_tasks.length, v.overdue_tasks.map(t => _taskRow(t, 'today-overdue')).join(''), 'no overdue tasks')
    : '';
  const meetings = _section('Meetings', v.meetings.length, v.meetings.map(_meetingRow).join(''), 'no meetings');
  const tasks = _section('Tasks', v.scheduled_tasks.length + v.unscheduled_tasks.length,
    v.scheduled_tasks.concat(v.unscheduled_tasks).map(t => _taskRow(t, '')).join(''), 'no tasks for this day');
  return `<div class="today-overview">${overdue}${meetings}${tasks}` +
    `<div class="today-capacity">${_esc(cap)}</div></div>`;
}

function _renderTimeline(v) {
  let hours = '';
  for (let h = HOUR_START; h <= HOUR_END; h++) {
    hours += `<div class="today-hour" style="height:${60 * PX_PER_MIN}px"><span class="today-hour-label">` +
      `${String(h).padStart(2, '0')}:00</span></div>`;
  }
  let blocks = '';
  const place = (startMin, mins, title, cls, attr) => {
    const top = (startMin - HOUR_START * 60) * PX_PER_MIN;
    const height = Math.max(18, mins * PX_PER_MIN);
    return `<div class="today-block ${cls}" ${attr} style="top:${top}px;height:${height}px">` +
      `<span class="today-block-title">${_esc(title)}</span></div>`;
  };
  v.meetings.filter(m => !m.all_day).forEach(m => {
    const t = _evTime(m.dtstart); if (!t) return;
    const [hh, mm] = t.split(':').map(Number);
    const durMin = 60; // visual default; real end parsed below if available
    let end = m.dtend ? _evTime(m.dtend) : '';
    let dm = durMin;
    if (end) { const [eh, em] = end.split(':').map(Number); dm = Math.max(15, (eh * 60 + em) - (hh * 60 + mm)); }
    blocks += place(hh * 60 + mm, dm, m.summary || '(busy)', 'today-block-meeting', `data-meeting="${_esc(m.uid)}"`);
  });
  v.scheduled_tasks.forEach(t => {
    const [hh, mm] = (t.planned_start || '00:00').split(':').map(Number);
    blocks += place(hh * 60 + mm, t.estimate_minutes || DEFAULT_EST, t.title || '(task)',
                    'today-block-task', `data-task="${_esc(t.id)}"`);
  });
  const rail = v.unscheduled_tasks.concat(v.is_today ? v.overdue_tasks : [])
    .map(t => _taskRow(t, 'today-rail-item')).join('') || `<div class="today-empty">nothing unscheduled</div>`;
  return `<div class="today-timeline-wrap"><div class="today-grid"><div class="today-hours">${hours}` +
    `<div class="today-blocks">${blocks}</div></div></div>` +
    `<div class="today-rail"><div class="today-section-header">Unscheduled</div>${rail}</div></div>`;
}

function _render(v) {
  const panel = document.getElementById('today-body');
  if (!panel) return;
  document.getElementById('today-date-label').textContent =
    v.is_today ? `Today · ${v.day}` : v.day;
  panel.innerHTML = _view === 'overview' ? _renderOverview(v) : _renderTimeline(v);
  document.querySelectorAll('#today-body [data-task]').forEach(elm =>
    elm.addEventListener('click', () => _showTaskDetail(elm.getAttribute('data-task'))));
  document.querySelectorAll('#today-body [data-meeting]').forEach(elm =>
    elm.addEventListener('click', () => _showMeetingDetail(v, elm.getAttribute('data-meeting'))));
}

async function _reload() {
  try { _render(await _fetchDay(_day)); }
  catch (e) { const b = document.getElementById('today-body'); if (b) b.innerHTML = `<div class="today-empty">failed to load</div>`; }
}

async function _showTaskDetail(id) {
  let d; try { d = await _fetchTask(id); } catch (e) { return; }
  const t = d.task;
  const ppl = d.people.length ? `<div class="today-detail-row">People: ${d.people.map(p => _esc(p.name)).join(', ')}</div>` : '';
  const note = d.source_note ? `<div class="today-detail-row">From note: ${_esc(d.source_note.title)}</div>` : '';
  const area = d.area ? `<div class="today-detail-row">Area: ${_esc(d.area.name)}</div>` : '';
  _openDetail(t.title, [
    t.due_date ? `<div class="today-detail-row">Due ${_esc(t.due_date)}</div>` : '',
    t.priority ? `<div class="today-detail-row">Priority: ${_esc(t.priority)}</div>` : '',
    t.estimate_minutes ? `<div class="today-detail-row">Estimate: ~${t.estimate_minutes}m</div>` : '',
    area, ppl, note,
    t.notes ? `<div class="today-detail-notes">${_esc(t.notes)}</div>` : '',
  ].join(''), `<button class="today-complete-btn" data-complete="${_esc(t.id)}">Complete</button>`);
  const btn = document.querySelector('[data-complete]');
  if (btn) btn.addEventListener('click', async () => { await _completeTask(t.id); _closeDetail(); _reload(); });
}

function _showMeetingDetail(v, uid) {
  const m = v.meetings.find(x => x.uid === uid); if (!m) return;
  _openDetail(m.summary || '(busy)', [
    `<div class="today-detail-row">${m.all_day ? 'All day' : _evTime(m.dtstart) + ' – ' + _evTime(m.dtend)}</div>`,
    m.location ? `<div class="today-detail-row">${_esc(m.location)}</div>` : '',
    m.area_name ? `<div class="today-detail-row">Area: ${_esc(m.area_name)}</div>` : '',
    m.description ? `<div class="today-detail-notes">${_esc(m.description)}</div>` : '',
  ].join(''), '');
}

function _openDetail(title, bodyHtml, actionsHtml) {
  let p = document.getElementById('today-detail');
  if (!p) {
    p = document.createElement('div'); p.id = 'today-detail'; p.className = 'today-detail';
    document.getElementById('today-panel').appendChild(p);
  }
  p.innerHTML = `<div class="today-detail-head"><span>${_esc(title)}</span>` +
    `<button id="today-detail-close" class="icon-btn">${ICON_CLOSE}</button></div>` +
    `<div class="today-detail-body">${bodyHtml}</div><div class="today-detail-actions">${actionsHtml}</div>`;
  p.classList.add('open');
  document.getElementById('today-detail-close').addEventListener('click', _closeDetail);
}
function _closeDetail() { const p = document.getElementById('today-detail'); if (p) p.classList.remove('open'); }

function _close() {
  _open = false;
  const p = document.getElementById('today-panel'); if (p) p.remove();
  if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
}

function openToday() {
  if (_open) return;
  _open = true; _day = _day || _localToday();
  const panel = document.createElement('div');
  panel.id = 'today-panel'; panel.className = 'today-panel';
  panel.innerHTML =
    `<div class="today-header">` +
      `<div class="today-title">${ICON_TODAY}<span id="today-date-label">Today</span></div>` +
      `<div class="today-nav">` +
        `<button id="today-prev" class="icon-btn">&#8592;</button>` +
        `<button id="today-now" class="today-now-btn">Today</button>` +
        `<button id="today-next" class="icon-btn">&#8594;</button>` +
        `<input type="date" id="today-date-input" class="today-date-input">` +
      `</div>` +
      `<div class="today-views">` +
        `<button id="today-view-overview" class="today-view-btn active">Overview</button>` +
        `<button id="today-view-timeline" class="today-view-btn">Timeline</button>` +
      `</div>` +
      `<button id="today-close" class="icon-btn">${ICON_CLOSE}</button>` +
    `</div><div id="today-body" class="today-body"></div>`;
  document.body.appendChild(panel);

  const setView = (vw) => {
    _view = vw;
    document.getElementById('today-view-overview').classList.toggle('active', vw === 'overview');
    document.getElementById('today-view-timeline').classList.toggle('active', vw === 'timeline');
    _reload();
  };
  document.getElementById('today-close').addEventListener('click', _close);
  document.getElementById('today-view-overview').addEventListener('click', () => setView('overview'));
  document.getElementById('today-view-timeline').addEventListener('click', () => setView('timeline'));
  document.getElementById('today-prev').addEventListener('click', () => { _day = _shiftDay(_day, -1); _reload(); });
  document.getElementById('today-next').addEventListener('click', () => { _day = _shiftDay(_day, 1); _reload(); });
  document.getElementById('today-now').addEventListener('click', () => { _day = _localToday(); _reload(); });
  document.getElementById('today-date-input').addEventListener('change', (e) => {
    if (e.target.value) { _day = e.target.value; _reload(); }
  });
  _escHandler = (e) => { if (e.key === 'Escape') { if (document.getElementById('today-detail')?.classList.contains('open')) _closeDetail(); else _close(); } };
  document.addEventListener('keydown', _escHandler);
  _reload();
}

window.openToday = openToday;
export default { openToday };
