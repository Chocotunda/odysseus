// static/js/meetingNote.js
// Meeting-note composer modal — Task 8.
// Vanilla JS, no build step. Mirrors the people.js injected-panel pattern:
// backdrop + pane mount, _esc XSS helper, Escape keydown with preventDefault,
// and exposes window.openMeetingNote(opts?).
//
// opts = { personId?, personName?, eventUid?, eventSummary? }

(function () {
  'use strict';

  const API_BASE = window.location.origin;

  let _open = false;
  let _escHandler = null;
  let _pollTimer = null;  // setInterval handle for AI enrichment polling

  // --- inline monochrome SVG icons (no emoji per project style) ---
  const ICON_CLOSE =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M18 6 6 18M6 6l12 12"/>' +
    '</svg>';

  const ICON_NOTE =
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<rect x="5" y="3" width="14" height="18" rx="2"/>' +
    '<path d="M9 8h6M9 12h6M9 16h4"/>' +
    '</svg>';

  const ICON_TASK_CHIP =
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<rect x="4" y="4" width="16" height="16" rx="3"/>' +
    '<path d="m8.5 12 2.5 2.5 4.5-5"/>' +
    '</svg>';

  const ICON_ADD_SMALL =
    '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12 5v14M5 12h14"/>' +
    '</svg>';

  const ICON_DISMISS =
    '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M18 6 6 18M6 6l12 12"/>' +
    '</svg>';

  const ICON_REMOVE =
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M18 6 6 18M6 6l12 12"/>' +
    '</svg>';

  // --- XSS escape helper ---
  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // --- cleanup helpers ---
  function _removeBackdrop() {
    try { document.getElementById('mnote-backdrop')?.remove(); } catch (_) {}
  }

  function _removeEscHandler() {
    if (_escHandler) {
      document.removeEventListener('keydown', _escHandler);
      _escHandler = null;
    }
  }

  function _stopPoll() {
    if (_pollTimer !== null) {
      clearInterval(_pollTimer);
      _pollTimer = null;
    }
  }

  // --- API helpers ---
  async function _fetchPeople() {
    try {
      const res = await fetch(`${API_BASE}/api/people`, { credentials: 'same-origin' });
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data.people) ? data.people : (Array.isArray(data) ? data : []);
    } catch (e) {
      console.error('meetingNote: fetchPeople failed', e);
      return [];
    }
  }

  async function _createPerson(name) {
    const res = await fetch(`${API_BASE}/api/people`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error('create person failed');
    return await res.json();
  }

  async function _fetchMeetings(q) {
    try {
      const url = `${API_BASE}/api/meeting-notes/meetings?q=${encodeURIComponent(q || '')}`;
      const res = await fetch(url, { credentials: 'same-origin' });
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data.meetings) ? data.meetings : [];
    } catch (e) {
      console.error('meetingNote: fetchMeetings failed', e);
      return [];
    }
  }

  async function _saveMeetingNote(payload) {
    const res = await fetch(`${API_BASE}/api/meeting-notes`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => '');
      throw new Error(`save failed (${res.status}): ${txt}`);
    }
    return await res.json();
  }

  async function _pollNote(noteId) {
    const res = await fetch(`${API_BASE}/api/meeting-notes/${encodeURIComponent(noteId)}`, {
      credentials: 'same-origin',
    });
    if (!res.ok) throw new Error('poll failed');
    return await res.json();
  }

  async function _promoteCandidate(noteId, title, personId, dueDate, areaId) {
    const payload = { title };
    if (personId) payload.person_id = personId;
    if (dueDate) payload.due_date = dueDate;
    if (areaId) payload.area_id = areaId;
    const res = await fetch(`${API_BASE}/api/meeting-notes/${encodeURIComponent(noteId)}/promote`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('promote failed');
    return await res.json();
  }

  // --- HTML builder ---
  function _buildModalHtml(opts) {
    const personName = opts.personName ? _esc(opts.personName) : '';
    const eventSummary = opts.eventSummary ? _esc(opts.eventSummary) : '';
    return (
      '<div class="mnote-header">' +
        '<span class="mnote-title">' + ICON_NOTE + '<span>Meeting Note</span></span>' +
        '<button class="mnote-close-btn" id="mnote-close" title="Close">' + ICON_CLOSE + '</button>' +
      '</div>' +
      '<div class="mnote-body">' +
        // Person picker
        '<div class="mnote-field-group">' +
          '<label class="mnote-label">Person</label>' +
          '<div class="mnote-picker-wrap">' +
            '<input type="text" id="mnote-person-input" class="mnote-input" ' +
              'placeholder="Search people or type a name to create…" autocomplete="off" ' +
              'value="' + personName + '">' +
            '<div class="mnote-picker-list" id="mnote-person-list" style="display:none;"></div>' +
          '</div>' +
          '<div class="mnote-field-hint" id="mnote-person-hint"></div>' +
        '</div>' +
        // Meeting picker
        '<div class="mnote-field-group">' +
          '<label class="mnote-label">Meeting (optional)</label>' +
          '<div class="mnote-picker-wrap">' +
            '<input type="text" id="mnote-meeting-input" class="mnote-input" ' +
              'placeholder="Search calendar events…" autocomplete="off" ' +
              'value="' + eventSummary + '">' +
            '<div class="mnote-picker-list" id="mnote-meeting-list" style="display:none;"></div>' +
          '</div>' +
          '<div class="mnote-field-hint" id="mnote-meeting-hint"></div>' +
        '</div>' +
        // Area picker (mounted dynamically after render)
        '<div class="mnote-field-group" id="mnote-area-picker-group">' +
        '</div>' +
        // Title
        '<div class="mnote-field-group">' +
          '<label class="mnote-label">Title</label>' +
          '<input type="text" id="mnote-title" class="mnote-input" placeholder="1:1 with…" autocomplete="off">' +
        '</div>' +
        // Notes textarea
        '<div class="mnote-field-group mnote-field-grow">' +
          '<label class="mnote-label">Notes</label>' +
          '<textarea id="mnote-content" class="mnote-textarea" placeholder="Discussion notes, decisions, context…"></textarea>' +
        '</div>' +
        // Action items
        '<div class="mnote-field-group">' +
          '<label class="mnote-label">Action items</label>' +
          '<div id="mnote-items-list" class="mnote-items-list"></div>' +
          '<div class="mnote-add-item-row">' +
            '<input type="text" id="mnote-new-item" class="mnote-input mnote-new-item-input" placeholder="+ Add action item…" autocomplete="off">' +
            '<button class="mnote-add-item-btn" id="mnote-add-item-btn">Add</button>' +
          '</div>' +
        '</div>' +
        // Results area (chips after save)
        '<div id="mnote-results" class="mnote-results" style="display:none;"></div>' +
        // AI candidate chips
        '<div id="mnote-candidates" class="mnote-candidates" style="display:none;">' +
          '<div class="mnote-candidates-label">AI suggestions</div>' +
          '<div id="mnote-candidates-list" class="mnote-candidates-list"></div>' +
        '</div>' +
      '</div>' +
      // Footer buttons
      '<div class="mnote-footer">' +
        '<div class="mnote-err" id="mnote-err"></div>' +
        '<div class="mnote-footer-btns">' +
          '<button class="btn mnote-btn-secondary" id="mnote-save-btn">Save note</button>' +
          '<button class="btn mnote-btn-primary" id="mnote-save-tasks-btn">Save + make tasks</button>' +
        '</div>' +
      '</div>'
    );
  }

  // --- Action items list state ---
  let _actionItems = [];

  function _renderItems(listEl) {
    if (!listEl) return;
    if (!_actionItems.length) {
      listEl.innerHTML = '<div class="mnote-items-empty">No action items yet.</div>';
      return;
    }
    listEl.innerHTML = _actionItems.map((item, idx) =>
      '<div class="mnote-item" data-idx="' + idx + '">' +
        '<input type="checkbox" class="mnote-item-check" data-idx="' + idx + '"' +
          (item.done ? ' checked' : '') + '>' +
        '<span class="mnote-item-text' + (item.done ? ' mnote-item-done' : '') + '">' +
          _esc(item.text) +
        '</span>' +
        '<button class="mnote-item-remove" data-idx="' + idx + '" title="Remove">' + ICON_REMOVE + '</button>' +
      '</div>'
    ).join('');
  }

  function _wireItems(listEl, newItemInput, addBtn) {
    // Delegation for check / remove on list
    listEl.addEventListener('change', (e) => {
      const check = e.target.closest('.mnote-item-check');
      if (!check) return;
      const idx = parseInt(check.dataset.idx, 10);
      if (isNaN(idx) || !_actionItems[idx]) return;
      _actionItems[idx].done = check.checked;
      _renderItems(listEl);
    });
    listEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.mnote-item-remove');
      if (!btn) return;
      const idx = parseInt(btn.dataset.idx, 10);
      if (isNaN(idx)) return;
      _actionItems.splice(idx, 1);
      _renderItems(listEl);
    });

    function _addItem() {
      const text = (newItemInput?.value || '').trim();
      if (!text) return;
      _actionItems.push({ text, done: false });
      if (newItemInput) newItemInput.value = '';
      _renderItems(listEl);
      newItemInput?.focus();
    }

    addBtn?.addEventListener('click', _addItem);
    newItemInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); _addItem(); }
    });
  }

  // --- Person picker ---
  let _selectedPersonId = null;
  let _selectedPersonName = '';
  let _personPickerDebounce = null;
  let _allPeople = [];

  function _filterPeople(query) {
    const q = (query || '').toLowerCase().trim();
    if (!q) return _allPeople.slice(0, 8);
    return _allPeople.filter(p =>
      (p.name || '').toLowerCase().includes(q) ||
      (p.email || '').toLowerCase().includes(q)
    ).slice(0, 8);
  }

  function _showPersonList(listEl, hintEl, query) {
    const matches = _filterPeople(query);
    const q = (query || '').trim();
    let html = '';
    if (matches.length) {
      html += matches.map(p =>
        '<div class="mnote-picker-item" data-person-id="' + _esc(p.id) + '" data-person-name="' + _esc(p.name || '') + '">' +
          '<span class="mnote-picker-item-name">' + _esc(p.name || '(unnamed)') + '</span>' +
          (p.role ? '<span class="mnote-picker-item-sub">' + _esc(p.role) + '</span>' : '') +
        '</div>'
      ).join('');
    }
    if (q && !matches.some(p => (p.name || '').toLowerCase() === q.toLowerCase())) {
      html += '<div class="mnote-picker-item mnote-picker-create" data-create-name="' + _esc(q) + '">' +
        '<span class="mnote-picker-item-name">Create &ldquo;' + _esc(q) + '&rdquo;</span>' +
        '<span class="mnote-picker-item-sub">New person</span>' +
      '</div>';
    }
    if (!html) {
      listEl.style.display = 'none';
      return;
    }
    listEl.innerHTML = html;
    listEl.style.display = 'block';
    if (hintEl) hintEl.textContent = '';
  }

  function _wirePersonPicker(pane, opts) {
    const input = pane.querySelector('#mnote-person-input');
    const listEl = pane.querySelector('#mnote-person-list');
    const hintEl = pane.querySelector('#mnote-person-hint');

    // Pre-fill from opts
    if (opts.personId) {
      _selectedPersonId = opts.personId;
      _selectedPersonName = opts.personName || '';
      if (input) input.value = _selectedPersonName;
      if (hintEl) hintEl.textContent = '';
    }

    input?.addEventListener('input', () => {
      // Clear selection when user types
      _selectedPersonId = null;
      _selectedPersonName = '';
      clearTimeout(_personPickerDebounce);
      _personPickerDebounce = setTimeout(() => {
        _showPersonList(listEl, hintEl, input.value);
      }, 150);
    });

    input?.addEventListener('focus', () => {
      _showPersonList(listEl, hintEl, input.value);
    });

    listEl?.addEventListener('mousedown', async (e) => {
      const item = e.target.closest('.mnote-picker-item');
      if (!item) return;
      e.preventDefault(); // keep focus in input briefly
      listEl.style.display = 'none';

      if (item.dataset.createName) {
        const name = item.dataset.createName;
        if (hintEl) hintEl.textContent = 'Creating…';
        try {
          const result = await _createPerson(name);
          const p = result.person || result;
          _selectedPersonId = p.id || null;
          _selectedPersonName = p.name || name;
          if (input) input.value = _selectedPersonName;
          if (hintEl) hintEl.textContent = '';
          // Refresh cache
          _allPeople = await _fetchPeople();
        } catch (err) {
          console.error('meetingNote: createPerson failed', err);
          if (hintEl) hintEl.textContent = 'Could not create person.';
        }
      } else {
        _selectedPersonId = item.dataset.personId || null;
        _selectedPersonName = item.dataset.personName || '';
        if (input) input.value = _selectedPersonName;
        if (hintEl) hintEl.textContent = '';
      }
    });

    // Hide list when focus leaves the input. The delay lets a mousedown on a
    // list item fire first. No document-level listener (would leak across
    // re-opens of the modal in a long-lived session).
    input?.addEventListener('blur', () => {
      setTimeout(() => {
        if (listEl) listEl.style.display = 'none';
      }, 200);
    });
  }

  // --- Meeting picker ---
  let _selectedEventUid = null;
  let _meetingPickerDebounce = null;

  function _wireMeetingPicker(pane, opts) {
    const input = pane.querySelector('#mnote-meeting-input');
    const listEl = pane.querySelector('#mnote-meeting-list');
    const hintEl = pane.querySelector('#mnote-meeting-hint');

    // Pre-fill from opts
    if (opts.eventUid) {
      _selectedEventUid = opts.eventUid;
      if (input) input.value = opts.eventSummary || opts.eventUid;
    }

    async function _doSearch() {
      const q = input ? input.value.trim() : '';
      const meetings = await _fetchMeetings(q);
      if (!meetings.length) {
        if (listEl) listEl.style.display = 'none';
        return;
      }
      listEl.innerHTML = meetings.map(m => {
        const date = m.dtstart ? _esc(m.dtstart.slice(0, 10)) : '';
        return (
          '<div class="mnote-picker-item" data-uid="' + _esc(m.uid || '') + '" data-summary="' + _esc(m.summary || '') + '">' +
            '<span class="mnote-picker-item-name">' + _esc(m.summary || '(no title)') + '</span>' +
            (date ? '<span class="mnote-picker-item-sub">' + date + '</span>' : '') +
          '</div>'
        );
      }).join('');
      listEl.style.display = 'block';
    }

    input?.addEventListener('input', () => {
      _selectedEventUid = null;
      clearTimeout(_meetingPickerDebounce);
      _meetingPickerDebounce = setTimeout(_doSearch, 250);
    });

    input?.addEventListener('focus', () => {
      _doSearch();
    });

    listEl?.addEventListener('mousedown', (e) => {
      const item = e.target.closest('.mnote-picker-item');
      if (!item) return;
      e.preventDefault();
      _selectedEventUid = item.dataset.uid || null;
      if (input) input.value = item.dataset.summary || '';
      listEl.style.display = 'none';
      if (hintEl) hintEl.textContent = '';
    });

    input?.addEventListener('blur', () => {
      setTimeout(() => {
        if (listEl) listEl.style.display = 'none';
      }, 200);
    });
  }

  // --- Task chip render (after save) ---
  function _renderTaskChips(tasks, resultsEl) {
    if (!resultsEl) return;
    if (!tasks || !tasks.length) return;
    resultsEl.style.display = 'block';
    const label = resultsEl.querySelector('.mnote-results-label') ||
      (() => {
        const d = document.createElement('div');
        d.className = 'mnote-results-label';
        d.textContent = 'Promoted tasks';
        resultsEl.appendChild(d);
        return d;
      })();
    const chipsWrap = resultsEl.querySelector('.mnote-results-chips') ||
      (() => {
        const d = document.createElement('div');
        d.className = 'mnote-results-chips';
        resultsEl.appendChild(d);
        return d;
      })();
    tasks.forEach(t => {
      const chip = document.createElement('span');
      chip.className = 'mnote-task-chip';
      chip.innerHTML = ICON_TASK_CHIP + ' ' + _esc(t.title || '(task)');
      chipsWrap.appendChild(chip);
    });
  }

  // --- AI candidate chips ---
  function _renderCandidates(candidates, candidatesEl, listEl, noteId, personId, areaId) {
    if (!candidates || !candidates.length) return;
    candidatesEl.style.display = 'block';
    listEl.innerHTML = '';
    candidates.forEach((c, idx) => {
      const title = c.title || c.text || '(suggestion)';
      const chip = document.createElement('div');
      chip.className = 'mnote-candidate';
      chip.dataset.idx = idx;
      chip.innerHTML =
        '<span class="mnote-candidate-title">' + _esc(title) + '</span>' +
        '<button class="mnote-candidate-add" data-idx="' + idx + '" title="Add as task">' +
          ICON_ADD_SMALL + ' add' +
        '</button>' +
        '<button class="mnote-candidate-dismiss" data-idx="' + idx + '" title="Dismiss">' +
          ICON_DISMISS +
        '</button>';
      listEl.appendChild(chip);
    });

    listEl.addEventListener('click', async (e) => {
      const addBtn = e.target.closest('.mnote-candidate-add');
      const dismissBtn = e.target.closest('.mnote-candidate-dismiss');
      if (addBtn) {
        const idx = parseInt(addBtn.dataset.idx, 10);
        const candidate = candidates[idx];
        if (!candidate) return;
        addBtn.disabled = true;
        addBtn.textContent = '…';
        try {
          await _promoteCandidate(
            noteId,
            candidate.title || candidate.text || '',
            personId,
            candidate.due_date || null,
            areaId
          );
          const chip = addBtn.closest('.mnote-candidate');
          if (chip) {
            chip.classList.add('mnote-candidate-done');
            chip.innerHTML = ICON_TASK_CHIP + ' <span class="mnote-candidate-title">' + _esc(candidate.title || candidate.text || '') + '</span><span class="mnote-candidate-promoted"> task created</span>';
          }
        } catch (err) {
          console.error('meetingNote: promote candidate failed', err);
          addBtn.disabled = false;
          addBtn.textContent = 'add';
        }
      }
      if (dismissBtn) {
        const chip = dismissBtn.closest('.mnote-candidate');
        if (chip) chip.remove();
      }
    });
  }

  // --- Poll for AI enrichment ---
  function _startPoll(noteId, candidatesEl, candidatesListEl, personId, areaId) {
    let tries = 0;
    const MAX_TRIES = 10;
    _stopPoll();
    _pollTimer = setInterval(async () => {
      tries++;
      if (tries > MAX_TRIES) {
        _stopPoll();
        return;
      }
      try {
        const data = await _pollNote(noteId);
        const note = data.note || data;
        if (note.ai_enriched) {
          _stopPoll();
          const suggestions = Array.isArray(note.suggested_action_items) ? note.suggested_action_items : [];
          if (suggestions.length) {
            _renderCandidates(suggestions, candidatesEl, candidatesListEl, noteId, personId, areaId);
          }
        }
      } catch (err) {
        console.error('meetingNote: poll failed', err);
      }
    }, 1500);
  }

  // --- Wire modal ---
  function _wireModal(pane, opts, areaPickerHandle) {
    pane.querySelector('#mnote-close')?.addEventListener('click', () => close());

    const errEl = pane.querySelector('#mnote-err');
    const resultsEl = pane.querySelector('#mnote-results');
    const candidatesEl = pane.querySelector('#mnote-candidates');
    const candidatesListEl = pane.querySelector('#mnote-candidates-list');
    const itemsListEl = pane.querySelector('#mnote-items-list');
    const newItemInput = pane.querySelector('#mnote-new-item');
    const addItemBtn = pane.querySelector('#mnote-add-item-btn');

    _renderItems(itemsListEl);
    _wireItems(itemsListEl, newItemInput, addItemBtn);
    _wirePersonPicker(pane, opts);
    _wireMeetingPicker(pane, opts);

    async function _doSave(makeTasks) {
      const titleEl = pane.querySelector('#mnote-title');
      const contentEl = pane.querySelector('#mnote-content');
      const title = (titleEl?.value || '').trim();
      const content = (contentEl?.value || '').trim();

      if (errEl) errEl.textContent = '';

      const saveBtn = pane.querySelector('#mnote-save-btn');
      const saveTasksBtn = pane.querySelector('#mnote-save-tasks-btn');
      const activeBtn = makeTasks ? saveTasksBtn : saveBtn;
      if (activeBtn) { activeBtn.disabled = true; activeBtn.textContent = 'Saving…'; }

      try {
        // Resolve areaPicker — may be a promise (from async mount) or a handle
        const picker = (areaPickerHandle && typeof areaPickerHandle.then === 'function')
          ? await areaPickerHandle
          : areaPickerHandle;
        const areaId = picker ? picker.value : null;

        const payload = {
          title: title || undefined,
          content: content || undefined,
          action_items: _actionItems.slice(),
          make_tasks: makeTasks,
        };
        if (_selectedPersonId) payload.person_id = _selectedPersonId;
        if (_selectedEventUid) payload.event_uid = _selectedEventUid;
        if (areaId) payload.area_id = areaId;

        const result = await _saveMeetingNote(payload);
        const note = result.note || result;
        const noteId = note.id;
        const tasks = Array.isArray(result.tasks) ? result.tasks : [];

        // Render task chips for promoted items
        if (tasks.length) _renderTaskChips(tasks, resultsEl);

        // Disable save buttons post-save
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saved'; }
        if (saveTasksBtn) { saveTasksBtn.disabled = true; saveTasksBtn.textContent = 'Saved'; }

        // Poll for AI enrichment — pass areaId so candidates can inherit it on promote
        if (noteId) {
          _startPoll(noteId, candidatesEl, candidatesListEl, _selectedPersonId, areaId);
        }
      } catch (e) {
        console.error('meetingNote: save failed', e);
        if (errEl) errEl.textContent = 'Could not save note. Try again.';
        if (activeBtn) {
          activeBtn.disabled = false;
          activeBtn.textContent = makeTasks ? 'Save + make tasks' : 'Save note';
        }
      }
    }

    pane.querySelector('#mnote-save-btn')?.addEventListener('click', () => _doSave(false));
    pane.querySelector('#mnote-save-tasks-btn')?.addEventListener('click', () => _doSave(true));
  }

  // --- Open / close ---
  function close() {
    if (!_open) return;
    _open = false;
    _stopPoll();
    _removeBackdrop();
    _removeEscHandler();
  }

  function openMeetingNote(opts) {
    opts = opts || {};

    // Reset per-open state
    _actionItems = [];
    _selectedPersonId = opts.personId || null;
    _selectedPersonName = opts.personName || '';
    _selectedEventUid = opts.eventUid || null;

    if (_open) close();
    _open = true;
    _removeBackdrop();

    const backdrop = document.createElement('div');
    backdrop.id = 'mnote-backdrop';
    backdrop.className = 'mnote-backdrop';
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });

    const modal = document.createElement('div');
    modal.id = 'mnote-modal';
    modal.className = 'mnote-modal';
    modal.innerHTML = _buildModalHtml(opts);

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Kick off people load in background for picker
    _fetchPeople().then(people => { _allPeople = people; });

    // Mount Area picker into its placeholder group
    const areaPickerGroup = modal.querySelector('#mnote-area-picker-group');
    const areaPickerHandle = (window.AreaPicker && areaPickerGroup)
      ? window.AreaPicker.mount(areaPickerGroup, { selectedId: opts.areaId || null })
      : Promise.resolve(null);

    _wireModal(modal, opts, areaPickerHandle);

    _removeEscHandler();
    _escHandler = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); close(); }
    };
    document.addEventListener('keydown', _escHandler);

    // Focus: title if person pre-filled, else person input
    if (opts.personId) {
      modal.querySelector('#mnote-title')?.focus();
    } else {
      modal.querySelector('#mnote-person-input')?.focus();
    }
  }

  // --- Expose globally ---
  window.openMeetingNote = openMeetingNote;
}());
