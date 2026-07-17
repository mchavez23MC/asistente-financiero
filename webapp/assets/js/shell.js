/* ============================================================
   LUCA — Shell de la app: sidebar (desktop) + bottom nav (mobile)
   + topbar (campana, tema, avatar). Uso: LUCA.shell({active,title})
   ============================================================ */
window.LUCA = window.LUCA || {};

LUCA.nav = [
  { id: 'inicio', href: '/app/inicio', label: 'Inicio', icon: 'home' },
  { id: 'chat', href: '/app/chat', label: 'Chat con Luca', icon: 'chat' },
  { id: 'movimientos', href: '/app/movimientos', label: 'Movimientos', icon: 'arrow-left-right' },
  { id: 'documentos', href: '/app/documentos', label: 'Mis respaldos', icon: 'file' },
  { id: 'perfil', href: '/app/perfil', label: 'Mi perfil', icon: 'target' },
  { id: 'presupuestos', href: '/app/presupuestos', label: 'Presupuestos', icon: 'target' },
  { id: 'soporte', href: '/app/tickets', label: 'Mis tickets', icon: 'headphones' }
];

LUCA.shell = function (opts) {
  opts = opts || {};
  var active = opts.active;
  var u = LUCA.user;

  /* ---- Sidebar (desktop) ---- */
  var sb = document.getElementById('app-sidebar');
  if (sb) {
    var items = LUCA.nav.map(function (n) {
      return '<a class="nav-item ' + (n.id === active ? 'is-active' : '') + '" href="' + n.href + '">' +
        LUCA.icon(n.icon) + '<span>' + n.label + '</span>' +
        (n.badge ? '<span class="nav-item__badge">' + n.badge + '</span>' : '') + '</a>';
    }).join('');
    sb.className = 'sidebar';
    sb.innerHTML =
      '<div class="sidebar__brand"><div class="avatar avatar--gold">L</div><span class="wordmark">Luca</span></div>' +
      '<nav class="nav-list">' + items + '</nav>' +
      '<div class="sidebar__footer nav-list">' +
        '<a class="nav-item" href="/legal">' + LUCA.icon('shield') + '<span>Privacidad y términos</span></a>' +
        '<a class="nav-item" href="/app/inicio">' + '<span class="avatar" style="width:20px;height:20px;font-size:11px">' + u.initials + '</span><span>' + u.name + '</span></a>' +
        (LUCA.logout ? '<button class="nav-item" id="nav-logout" style="width:100%;background:none;border:0;cursor:pointer;text-align:left">' + LUCA.icon('x') + '<span>Salir</span></button>' : '') +
      '</div>';
    var lo = sb.querySelector('#nav-logout'); if (lo) lo.addEventListener('click', function () { LUCA.logout(); });
  }

  /* ---- Topbar ---- */
  var tb = document.getElementById('app-topbar');
  if (tb) {
    tb.className = 'topbar';
    var unread = (LUCA.notifications || []).filter(function (n) { return n.unread; }).length;
    tb.innerHTML =
      '<button class="iconbtn hide-desktop" id="btn-menu" aria-label="Menú">' + LUCA.icon('menu') + '</button>' +
      '<div class="topbar__title">' + (opts.title || '') + '</div>' +
      '<div class="topbar__spacer"></div>' +
      '<button class="iconbtn" id="btn-bell" aria-label="Notificaciones">' + LUCA.icon('bell') + (unread ? '<span class="dot-badge"></span>' : '') + '</button>' +
      '<button class="iconbtn hide-mobile" id="btn-theme" aria-label="Cambiar tema">' + LUCA.icon('moon') + '</button>' +
      '<button class="iconbtn" id="btn-avatar" aria-label="Cuenta"><span class="avatar" style="width:30px;height:30px;font-size:12px">' + u.initials + '</span></button>';
    tb.querySelector('#btn-theme') && tb.querySelector('#btn-theme').addEventListener('click', LUCA.toggleTheme);
    tb.querySelector('#btn-avatar').addEventListener('click', function () { location.href = '/app/inicio'; });
    tb.querySelector('#btn-bell').addEventListener('click', LUCA.toggleNotif);
    var bm = tb.querySelector('#btn-menu'); if (bm) bm.addEventListener('click', LUCA.openMoreSheet);
  }

  /* ---- Bottom nav (mobile) ---- */
  var bn = document.getElementById('app-bottomnav');
  if (bn) {
    bn.className = 'bottomnav';
    function bitem(n) {
      return '<a class="bottomnav__item ' + (n.id === active ? 'is-active' : '') + '" href="' + n.href + '">' +
        LUCA.icon(n.icon) + '<span>' + n.short + '</span>' + (n.badge ? '<span class="nav-item__badge">' + n.badge + '</span>' : '') + '</a>';
    }
    bn.innerHTML =
      bitem({ id: 'inicio', href: '/app/inicio', icon: 'home', short: 'Inicio' }) +
      bitem({ id: 'movimientos', href: '/app/movimientos', icon: 'arrow-left-right', short: 'Movim.' }) +
      '<div class="bottomnav__fab"><a class="fab" href="/app/chat" aria-label="Chat con Luca">' + LUCA.icon('chat') + '</a></div>' +
      bitem({ id: 'presupuestos', href: '/app/presupuestos', icon: 'target', short: 'Presup.' }) +
      '<button class="bottomnav__item" id="bn-more">' + LUCA.icon('menu') + '<span>Más</span></button>';
    bn.querySelector('#bn-more').addEventListener('click', LUCA.openMoreSheet);
  }

  LUCA.buildNotif();
  LUCA.buildMoreSheet();
  LUCA.bindOverlays && LUCA.bindOverlays();

};

/* ---- Panel de notificaciones ---- */
LUCA.buildNotif = function () {
  if (document.getElementById('notif-overlay')) return;
  var list = (LUCA.notifications || []).map(function (n) {
    var ic = { alert: 'alert', copy: 'copy', ticket: 'ticket', tv: 'tv' }[n.icon] || 'info';
    return '<a class="lrow" href="' + n.href + '" style="text-decoration:none;color:inherit">' +
      '<div class="cat-chip cat-chip--sm">' + LUCA.icon(ic) + '</div>' +
      '<div class="lrow__main"><div class="lrow__title">' + n.title + (n.unread ? ' <span class="dot-badge" style="position:static;display:inline-block;vertical-align:middle;border:0"></span>' : '') + '</div>' +
      '<div class="lrow__meta">' + n.text + ' · ' + n.ago + '</div></div></a>';
  }).join('');
  var el = document.createElement('div');
  el.className = 'overlay'; el.id = 'notif-overlay';
  el.style.alignItems = 'flex-start'; el.style.justifyContent = 'flex-end'; el.style.padding = '70px 20px';
  el.innerHTML = '<div class="modal" style="max-width:380px" onclick="event.stopPropagation()">' +
    '<div class="row-between mb-16"><div class="modal__title" style="margin:0">Notificaciones</div>' +
    '<button class="btn btn--ghost btn--sm" id="notif-read">Marcar leídas</button></div>' +
    '<div>' + list + '</div></div>';
  document.body.appendChild(el);
  el.addEventListener('click', function (e) { if (e.target === el) el.classList.remove('is-open'); });
  el.querySelector('#notif-read').addEventListener('click', function () {
    el.querySelectorAll('.dot-badge').forEach(function (d) { d.remove(); });
    var db = document.querySelector('#btn-bell .dot-badge'); if (db) db.remove();
    LUCA.toast('Notificaciones marcadas como leídas');
  });
};
LUCA.toggleNotif = function () { var o = document.getElementById('notif-overlay'); if (o) o.classList.toggle('is-open'); };

/* ---- Hoja "Más" (mobile) ---- */
LUCA.buildMoreSheet = function () {
  if (document.getElementById('more-overlay')) return;
  var extra = [
    { href: '/app/tickets', label: 'Mis tickets', icon: 'ticket' },
    { href: '/legal', label: 'Privacidad y términos', icon: 'shield' }
  ].map(function (n) {
    return '<a class="lrow" href="' + n.href + '" style="text-decoration:none;color:inherit">' +
      '<div class="cat-chip cat-chip--sm">' + LUCA.icon(n.icon) + '</div><div class="lrow__main"><div class="lrow__title">' + n.label + '</div></div>' + LUCA.icon('chevron-right', 'muted') + '</a>';
  }).join('');
  var el = document.createElement('div');
  el.className = 'overlay'; el.id = 'more-overlay';
  el.style.alignItems = 'flex-end';
  el.innerHTML = '<div class="modal" style="max-width:100%;border-radius:16px 16px 0 0" onclick="event.stopPropagation()">' +
    '<div class="row-between mb-8"><div class="modal__title" style="margin:0">Más</div>' +
    '<button class="iconbtn" data-close="more-overlay">' + LUCA.icon('x') + '</button></div>' + extra +
    '<button class="btn btn--outline btn--block mt-16" onclick="LUCA.toggleTheme()">' + LUCA.icon('moon') + 'Cambiar tema</button></div>';
  document.body.appendChild(el);
  el.addEventListener('click', function (e) { if (e.target === el) el.classList.remove('is-open'); });
};
LUCA.openMoreSheet = function () { var o = document.getElementById('more-overlay'); if (o) o.classList.add('is-open'); };
