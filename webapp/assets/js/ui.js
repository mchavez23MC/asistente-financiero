/* ============================================================
   LUCA — UI helpers: íconos (Lucide inline), toasts, modales, tema
   ============================================================ */
window.LUCA = window.LUCA || {};

/* ---- Íconos: subset de Lucide (MIT), stroke, 24x24 ---- */
LUCA.icons = {
  home: '<path d="M3 9.5 12 3l9 6.5V21a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
  'arrow-left-right': '<path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/>',
  chat: '<path d="M21 11.5a8.5 8.5 0 0 1-11.9 7.8L3 21l1.7-6.1A8.5 8.5 0 1 1 21 11.5z"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  bell: '<path d="M6 9a6 6 0 0 1 12 0c0 5 2.5 6 2.5 6H3.5S6 14 6 9z"/><path d="M10.3 20a2 2 0 0 0 3.4 0"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H1a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 2.6 7a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H7a1.6 1.6 0 0 0 1-1.5V1a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V7a1.6 1.6 0 0 0 1.5 1H23a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  'log-out': '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  'chevron-right': '<path d="m9 6 6 6-6 6"/>',
  'chevron-left': '<path d="m15 6-6 6 6 6"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  'chevron-up': '<path d="m6 15 6-6 6 6"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  headphones: '<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><path d="M21 16a2 2 0 0 1-2 2h-1v-6h1a2 2 0 0 1 2 2zM3 16a2 2 0 0 0 2 2h1v-6H5a2 2 0 0 0-2 2z"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  shield: '<path d="M12 2 4 5v6c0 5 3.4 8.5 8 10 4.6-1.5 8-5 8-10V5z"/>',
  'credit-card': '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
  trash: '<path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M6 6l1 14a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-14"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  'eye-off': '<path d="M9.9 4.2A9.8 9.8 0 0 1 12 4c6 0 10 8 10 8a18 18 0 0 1-2.3 3.3M6.6 6.6A18 18 0 0 0 2 12s4 8 10 8a9.8 9.8 0 0 0 4.4-1M1 1l22 22"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  tv: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="m7 3 5 4 5-4"/>',
  chart: '<path d="M3 3v18h18"/><rect x="7" y="10" width="3" height="7"/><rect x="12" y="6" width="3" height="11"/><rect x="17" y="13" width="3" height="4"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 9 5-5 5 5"/><path d="M12 4v12"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  filter: '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
  'arrow-up': '<path d="M12 19V5M5 12l7-7 7 7"/>',
  'arrow-down': '<path d="M12 5v14M19 12l-7 7-7-7"/>',
  wallet: '<path d="M3 6a2 2 0 0 1 2-2h13v4"/><path d="M3 6v12a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-3"/><path d="M22 11h-5a2 2 0 0 0 0 4h5z"/>',
  repeat: '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
  send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3M20 14v.01M14 20h.01M17 20h.01M20 17h.01M20 20h.01"/>',
  sparkles: '<path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m2 7 10 6 10-6"/>',
  phone: '<path d="M4 4h4l2 5-3 2a12 12 0 0 0 6 6l2-3 5 2v4a2 2 0 0 1-2 2A18 18 0 0 1 2 6a2 2 0 0 1 2-2z"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  ticket: '<path d="M3 9a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2 2 2 0 0 0 0 4 2 2 0 0 1-2 2H5a2 2 0 0 1-2-2 2 2 0 0 0 0-4z"/><path d="M13 7v10"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  smartphone: '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/>',
  'thumbs-up': '<path d="M7 10v11H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z"/><path d="M7 10l4-7a2 2 0 0 1 3 2l-1 5h5a2 2 0 0 1 2 2.3l-1.3 7A2 2 0 0 1 16.7 21H7z"/>',
  'thumbs-down': '<path d="M17 14V3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1z"/><path d="M17 14l-4 7a2 2 0 0 1-3-2l1-5H6a2 2 0 0 1-2-2.3l1.3-7A2 2 0 0 1 7.3 3H17z"/>',
  'pie': '<path d="M12 3v9l7.5 4.5A9 9 0 1 0 12 3z"/><path d="M21.5 12A9.5 9.5 0 0 0 12 2.5"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  'chevron-r-sm': '<path d="m9 6 6 6-6 6"/>'
};

LUCA.icon = function (name, cls) {
  var p = LUCA.icons[name] || LUCA.icons.info;
  return '<svg class="ico ' + (cls || '') + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + p + '</svg>';
};

/* ---- Tema ---- */
LUCA.initTheme = function () {
  var saved = localStorage.getItem('luca-theme');
  if (saved === 'dark' || saved === 'light') document.documentElement.setAttribute('data-theme', saved);
  else document.documentElement.removeAttribute('data-theme');
};
LUCA.setTheme = function (mode) {
  if (mode === 'system') { localStorage.removeItem('luca-theme'); document.documentElement.removeAttribute('data-theme'); }
  else { localStorage.setItem('luca-theme', mode); document.documentElement.setAttribute('data-theme', mode); }
};
LUCA.toggleTheme = function () {
  var cur = document.documentElement.getAttribute('data-theme');
  var isDark = cur === 'dark' || (!cur && window.matchMedia('(prefers-color-scheme: dark)').matches);
  LUCA.setTheme(isDark ? 'light' : 'dark');
};
LUCA.initTheme();

/* ---- Toasts ---- */
LUCA.toast = function (msg, opts) {
  opts = opts || {};
  var wrap = document.querySelector('.toasts');
  if (!wrap) { wrap = document.createElement('div'); wrap.className = 'toasts'; document.body.appendChild(wrap); }
  var el = document.createElement('div');
  el.className = 'toast toast--' + (opts.type || 'success');
  var ic = opts.type === 'error' ? 'alert' : (opts.type === 'info' ? 'info' : 'check-circle');
  var action = opts.action ? '<button class="toast__action">' + opts.action + '</button>' : '';
  el.innerHTML = LUCA.icon(ic) + '<span class="toast__text">' + msg + '</span>' + action;
  wrap.appendChild(el);
  var t = setTimeout(remove, opts.duration || 4000);
  function remove() { clearTimeout(t); el.style.opacity = '0'; setTimeout(function () { el.remove(); }, 180); }
  if (opts.action && opts.onAction) el.querySelector('.toast__action').addEventListener('click', function () { opts.onAction(); remove(); });
  return el;
};

/* ---- Modal / overlay genérico por id ---- */
LUCA.openModal = function (id) { var m = document.getElementById(id); if (m) m.classList.add('is-open'); };
LUCA.closeModal = function (id) { var m = document.getElementById(id); if (m) m.classList.remove('is-open'); };
LUCA.bindOverlays = function () {
  document.querySelectorAll('.overlay, .drawer-overlay').forEach(function (o) {
    o.addEventListener('click', function (e) { if (e.target === o) o.classList.remove('is-open'); });
  });
  document.querySelectorAll('[data-close]').forEach(function (b) {
    b.addEventListener('click', function () {
      var t = b.getAttribute('data-close');
      var el = document.getElementById(t); if (el) el.classList.remove('is-open');
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') document.querySelectorAll('.overlay.is-open, .drawer-overlay.is-open, .drawer.is-open').forEach(function (o) { o.classList.remove('is-open'); });
  });
};

/* ---- OTP inputs: autoavance ---- */
LUCA.bindOtp = function (container) {
  var inputs = Array.prototype.slice.call(container.querySelectorAll('input'));
  inputs.forEach(function (inp, i) {
    inp.addEventListener('input', function () {
      inp.value = inp.value.replace(/\D/g, '').slice(0, 1);
      if (inp.value && inputs[i + 1]) inputs[i + 1].focus();
    });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Backspace' && !inp.value && inputs[i - 1]) inputs[i - 1].focus();
    });
  });
};

/* ---- Password toggle ---- */
LUCA.bindPasswordToggles = function () {
  document.querySelectorAll('[data-pw-toggle]').forEach(function (btn) {
    btn.innerHTML = LUCA.icon('eye');
    btn.addEventListener('click', function () {
      var inp = document.getElementById(btn.getAttribute('data-pw-toggle'));
      if (!inp) return;
      var show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      btn.innerHTML = LUCA.icon(show ? 'eye-off' : 'eye');
    });
  });
};
