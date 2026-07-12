/* ============================================================
   LUCA — Capa de API real (rama webapp)
   Reemplaza los datos mock de data.js con datos del backend:
     GET  /api/estado?telefono=...   → transacciones, presupuestos, tickets
     POST /api/chat {telefono,texto} → pipeline real (guardrail + agente Claude)

   Identidad (demo): teléfono E.164 guardado en localStorage ('luca_phone')
   tras el "login". Sin contraseñas — la identidad natural es el teléfono,
   igual que en WhatsApp. Las páginas cableadas envuelven su render en
   LUCA.ready(fn); las no cableadas siguen mostrando el mock de data.js.
   ============================================================ */
window.LUCA = window.LUCA || {};

(function () {
  LUCA.phone = function () { return localStorage.getItem('luca_phone') || ''; };
  LUCA.setPhone = function (p) { localStorage.setItem('luca_phone', p); };
  LUCA.logout = function () { localStorage.removeItem('luca_phone'); location.href = '/index.html'; };

  // Guardia de sesión para páginas de la app (llámalo antes de renderizar).
  LUCA.requirePhone = function () {
    if (!LUCA.phone()) { location.href = '/index.html'; return false; }
    return true;
  };

  // --- mapeo categoría del backend (texto libre en español) → código del kit ---
  var CAT_MAP = {
    comida: 'FOOD_OUT', restaurante: 'FOOD_OUT', restaurantes: 'FOOD_OUT', almuerzo: 'FOOD_OUT',
    super: 'FOOD_GROCERIES', supermercado: 'FOOD_GROCERIES', mercado: 'FOOD_GROCERIES',
    transporte: 'TRANSPORT', taxi: 'TRANSPORT', uber: 'TRANSPORT', gasolina: 'TRANSPORT',
    vivienda: 'HOUSING', arriendo: 'HOUSING', renta: 'HOUSING',
    servicios: 'UTILITIES', luz: 'UTILITIES', agua: 'UTILITIES',
    internet: 'TELECOM', telefono: 'TELECOM', 'teléfono': 'TELECOM',
    salud: 'HEALTH', medicina: 'HEALTH', medicinas: 'HEALTH',
    educacion: 'EDUCATION', 'educación': 'EDUCATION',
    ropa: 'SHOPPING', compras: 'SHOPPING',
    entretenimiento: 'ENTERTAINMENT', cine: 'ENTERTAINMENT',
    suscripciones: 'SUBSCRIPTIONS', suscripcion: 'SUBSCRIPTIONS', 'suscripción': 'SUBSCRIPTIONS',
    mascotas: 'PETS', familia: 'FAMILY_SUPPORT'
  };
  LUCA.mapCat = function (nombre) {
    if (!nombre) return 'OTHER';
    var k = String(nombre).toLowerCase().trim();
    if (CAT_MAP[k]) return CAT_MAP[k];
    // Categoría no catalogada: registrarla al vuelo para que LUCA.cat la muestre.
    var code = 'X_' + k.toUpperCase().replace(/\s+/g, '_');
    LUCA.categories[code] = LUCA.categories[code] || { emoji: '💸', name: nombre };
    return code;
  };

  // Código del kit → nombre canónico que usa el agente al clasificar (ver
  // ejemplos de la tool registrar_gasto). Así los presupuestos creados aquí
  // matchean con los gastos que registra Luca por chat/WhatsApp.
  var CAT_CANON = {
    FOOD_OUT: 'comida', FOOD_GROCERIES: 'super', TRANSPORT: 'transporte',
    HOUSING: 'vivienda', UTILITIES: 'servicios', TELECOM: 'internet',
    HEALTH: 'salud', EDUCATION: 'educacion', SHOPPING: 'ropa',
    ENTERTAINMENT: 'entretenimiento', SUBSCRIPTIONS: 'suscripciones',
    PETS: 'mascotas', FAMILY_SUPPORT: 'familia'
  };
  LUCA.catToBackend = function (code) {
    if (CAT_CANON[code]) return CAT_CANON[code];
    if (code && code.indexOf('X_') === 0) return code.slice(2).toLowerCase().replace(/_/g, ' ');
    return ((LUCA.categories[code] || {}).name || code || 'otros').toLowerCase();
  };

  // --- transformaciones backend → formato que las páginas ya renderizan ---
  function mapTx(t) {
    return {
      id: t.id,
      type: 'EXPENSE',                       // el backend registra gastos (H1)
      amount: t.monto,
      date: t.fecha || (t.created_at || '').slice(0, 10),
      category: LUCA.mapCat(t.categoria),
      merchant: t.comercio || '',
      desc: t.comercio || (t.categoria || 'Movimiento'),
      method: null,
      status: t.status === 'pendiente_confirmacion' ? 'PENDING_CONFIRMATION'
            : t.status === 'anulada' ? 'VOID' : 'CONFIRMED',
      source: 'CHAT',
      confidence: null,
      needs: t.status === 'pendiente_confirmacion',
      question: t.monto == null ? '¿Cuánto fue el monto?' : null
    };
  }
  function mapBudget(b) {
    return { id: b.id, code: LUCA.mapCat(b.categoria), limit: b.monto_limite,
             spent: b.gastado, threshold: b.umbral_alerta, periodo: b.periodo };
  }
  var MOTIVO = {
    reclamo: 'Reclamo — requiere revisión humana',
    regulatorio: 'Tema regulatorio — derivado a humano',
    consejo_inversion: 'Pedido de asesoría de inversión — derivado',
    fraude: 'Posible fraude — prioridad alta',
    fuera_de_corpus: 'Consulta fuera de la base de conocimiento',
    guardrail_fail_closed: 'Clasificador no disponible — escalado por seguridad',
    otro: 'Escalado al equipo humano'
  };
  var ESTADO = { abierto: 'open', en_proceso: 'wip', resuelto: 'done', cerrado: 'done' };
  function mapTicket(t) {
    return {
      id: (t.id || '').slice(0, 8),
      subject: MOTIVO[t.motivo] || t.motivo,
      status: ESTADO[t.estado] || 'open',
      priority: t.prioridad === 'alta' ? 'high' : t.prioridad === 'baja' ? 'low' : 'mid',
      ago: LUCA.relDate((t.created_at || '').slice(0, 10)),
      reason: MOTIVO[t.motivo] || t.motivo,
      summary: t.contexto,
      transcript: [], replies: []
    };
  }

  // --- carga del estado real; sobreescribe los datos mock antes del render ---
  var _p = null;
  LUCA.loadReal = function () {
    if (_p) return _p;
    var tel = LUCA.phone();
    _p = fetch('/api/estado?telefono=' + encodeURIComponent(tel))
      .then(function (r) { if (!r.ok) throw new Error('estado ' + r.status); return r.json(); })
      .then(function (d) {
        LUCA.today = new Date().toISOString().slice(0, 10);
        LUCA.user.name = d.user.nombre || 'pana';
        LUCA.user.phone = d.user.telefono;
        LUCA.user.initials = (d.user.nombre || 'L')[0].toUpperCase();

        LUCA.transactions = d.transactions.map(mapTx);
        LUCA.budgets = d.budgets.map(mapBudget);
        LUCA.tickets = d.tickets.map(mapTicket);

        // Resumen del mes: gastos reales del sistema. (Ingresos: roadmap H1.)
        var gastado = d.resumen.gastado_mes || 0;
        LUCA.monthSummary = { month: LUCA.today.slice(0, 7), income: 0,
                              expenses: gastado, net: -gastado, prevNet: null, deltaPct: null };

        // Donut por categoría desde las transacciones confirmadas del mes.
        var mes = LUCA.today.slice(0, 7), porCat = {};
        LUCA.transactions.forEach(function (t) {
          if (t.status !== 'CONFIRMED' || !t.amount) return;
          if ((t.date || '').slice(0, 7) !== mes) return;
          porCat[t.category] = (porCat[t.category] || 0) + t.amount;
        });
        var colores = ['#1F3A5F', '#2E5A8F', '#6FA0D6', '#F5B301', '#B8860B', '#0E7A50', '#9AA7B5'];
        LUCA.categoryBreakdown = Object.keys(porCat)
          .sort(function (a, b) { return porCat[b] - porCat[a]; })
          .map(function (c, i) { return { code: c, amount: porCat[c], color: colores[i % colores.length] }; });

        // Insights reales mínimos: presupuestos sobre umbral (H2, calculado aquí
        // desde números del sistema — nunca del modelo).
        LUCA.insights = LUCA.budgets
          .filter(function (b) { return b.limit > 0 && b.spent / b.limit >= b.threshold; })
          .map(function (b, i) {
            var cat = LUCA.cat(b.code), pct = Math.round(b.spent / b.limit * 100);
            return { id: 'ins_r' + i, type: 'budget_warn',
                     severity: b.spent >= b.limit ? 'alert' : 'info', date: LUCA.today, cat: b.code,
                     title: 'Ojo con ' + cat.name,
                     body: 'Vas ' + LUCA.fmt(b.spent) + ' de ' + LUCA.fmt(b.limit) + ' (' + pct + '%).',
                     evidence: 'Umbral configurado: ' + Math.round(b.threshold * 100) + '%.' };
          });
        LUCA.notifications = LUCA.insights.map(function (i, n) {
          return { id: 'n_r' + n, unread: true, icon: 'alert', title: i.title, text: i.body, ago: 'ahora', href: 'presupuestos.html' };
        });

        // El shell ya se pintó con el mock: parchear nombre/iniciales reales.
        document.querySelectorAll('.sidebar__footer .nav-item span:last-child').forEach(function (s) {
          if (s.previousElementSibling && s.previousElementSibling.classList.contains('avatar')) s.textContent = LUCA.user.name;
        });
        document.querySelectorAll('.avatar').forEach(function (a) {
          if (a.textContent.trim().length === 1 && a.textContent !== 'L') a.textContent = LUCA.user.initials;
        });
        return d;
      });
    return _p;
  };

  // Las páginas cableadas envuelven su render: LUCA.ready(function(){ ... })
  LUCA.ready = function (fn) {
    if (!LUCA.requirePhone()) return;
    LUCA.loadReal().then(fn).catch(function (e) {
      console.error('Luca API:', e);
      if (LUCA.toast) LUCA.toast('No pude conectar con el servidor 😕');
      fn(); // degradar al mock para no dejar la página en blanco
    });
  };

  // Chat real contra el pipeline (guardrail + agente Claude).
  LUCA.sendChat = function (texto) {
    return fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telefono: LUCA.phone(), texto: texto })
    }).then(function (r) { if (!r.ok) throw new Error('chat ' + r.status); return r.json(); })
      .then(function (d) { _p = null; return d.respuestas || []; }); // invalida caché de estado
  };
})();
