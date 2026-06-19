// static/js/areaPicker.js
// Stub for the Area picker widget (Task 7). Provides a no-op mount so nothing
// errors if this file is loaded before the full implementation lands.
window.AreaPicker = window.AreaPicker || {
  mount() {
    return {
      el: document.createElement('span'),
      get value() { return null; },
    };
  },
};
