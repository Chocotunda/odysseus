// static/js/areaPicker.js
// Reusable Area picker widget — Task 7.
// Exposes window.AreaPicker.mount(containerEl, opts) which fetches /api/areas,
// injects a labelled <select>, and returns { el, value }.
// Dependency-free, safe to mount once per open.

(function () {
  'use strict';

  const API_BASE = window.location.origin;

  // XSS escape — same helper used across areas.js, people.js, meetingNote.js
  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function _fetchAreas() {
    try {
      const res = await fetch(`${API_BASE}/api/areas`, { credentials: 'same-origin' });
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data.areas) ? data.areas : (Array.isArray(data) ? data : []);
    } catch (e) {
      console.error('areaPicker: fetch failed', e);
      return [];
    }
  }

  /**
   * Mount an Area picker into containerEl.
   *
   * @param {HTMLElement} containerEl  — the wrapper element to inject into
   * @param {object}      opts
   * @param {string|null} [opts.selectedId]  — pre-select this area id
   * @param {Function}    [opts.onChange]    — called with the new value (string|null) on change
   * @returns {{ el: HTMLElement, value: string|null }}
   */
  async function mount(containerEl, opts) {
    opts = opts || {};

    // Build wrapper + label + select immediately (select populated after fetch)
    const wrapper = document.createElement('div');
    wrapper.className = 'area-picker-wrap';

    const label = document.createElement('label');
    label.className = 'area-picker-label';
    label.textContent = 'Area';

    const select = document.createElement('select');
    select.className = 'area-picker-select';

    // Empty / unassigned option
    const emptyOpt = document.createElement('option');
    emptyOpt.value = '';
    emptyOpt.textContent = '— Unassigned —';
    select.appendChild(emptyOpt);

    wrapper.appendChild(label);
    wrapper.appendChild(select);
    containerEl.appendChild(wrapper);

    // Fetch areas and populate
    const areas = await _fetchAreas();
    areas.forEach(function (a) {
      const opt = document.createElement('option');
      opt.value = String(a.id);
      opt.textContent = _esc(a.name || '(unnamed)');
      select.appendChild(opt);
    });

    // Pre-select
    if (opts.selectedId != null && opts.selectedId !== '') {
      select.value = String(opts.selectedId);
    }

    // Optional change callback
    if (typeof opts.onChange === 'function') {
      select.addEventListener('change', function () {
        opts.onChange(select.value || null);
      });
    }

    const handle = {
      el: wrapper,
      get value() { return select.value || null; },
    };

    return handle;
  }

  window.AreaPicker = { mount: mount };
}());
