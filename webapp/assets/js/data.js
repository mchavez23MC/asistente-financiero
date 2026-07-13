/* ============================================================
   LUCA — Catálogo y helpers de formato (SIN datos de ejemplo).
   Los datos reales los carga assets/js/api.js desde el backend
   (GET /api/estado con sesión Bearer). Aquí solo vive lo estático:
   taxonomía de categorías, métodos de pago y utilidades.
   ============================================================ */
window.LUCA = window.LUCA || {};

LUCA.today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD local

LUCA.categories = {
  FOOD_GROCERIES:   { emoji: '🛒', name: 'Súper y mercado' },
  FOOD_OUT:         { emoji: '🍔', name: 'Restaurantes' },
  HOUSING:          { emoji: '🏠', name: 'Vivienda' },
  UTILITIES:        { emoji: '💡', name: 'Servicios básicos' },
  TELECOM:          { emoji: '📱', name: 'Internet y telefonía' },
  TRANSPORT:        { emoji: '🚕', name: 'Transporte' },
  HEALTH:           { emoji: '💊', name: 'Salud' },
  EDUCATION:        { emoji: '📚', name: 'Educación' },
  PERSONAL_CARE:    { emoji: '✂️', name: 'Cuidado personal' },
  SHOPPING:         { emoji: '👕', name: 'Ropa y compras' },
  ENTERTAINMENT:    { emoji: '🎬', name: 'Entretenimiento' },
  SUBSCRIPTIONS:    { emoji: '📺', name: 'Suscripciones' },
  FAMILY_SUPPORT:   { emoji: '👨‍👩‍👧', name: 'Familia y ayudas' },
  PETS:             { emoji: '🐾', name: 'Mascotas' },
  DEBT_FEES:        { emoji: '💳', name: 'Deudas y comisiones' },
  TAXES_PROCEDURES: { emoji: '🧾', name: 'Impuestos y trámites' },
  OTHER:            { emoji: '💸', name: 'Otros' },
  TRANSFER:         { emoji: '⇄',  name: 'Transferencia' }
};

LUCA.paymentMethods = {
  CASH: 'Efectivo', DEBIT_CARD: 'Débito', CREDIT_CARD: 'Crédito',
  TRANSFER: 'Transferencia', DIGITAL_WALLET: 'Billetera', OTHER: 'Otro'
};

/* Estado vacío por defecto — api.js lo llena con datos reales del backend. */
LUCA.user = { name: '', phone: '', initials: 'U' };
LUCA.transactions = [];
LUCA.budgets = [];
LUCA.insights = [];
LUCA.tickets = [];
LUCA.notifications = [];
LUCA.categoryBreakdown = [];
LUCA.monthSummary = { month: '', income: 0, expenses: 0, net: 0, prevNet: null, deltaPct: null };

/* Helpers de formato */
LUCA.fmt = function (n) {
  if (n === null || n === undefined) return '—';
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
LUCA.signed = function (t, amount) {
  var v = LUCA.fmt(amount);
  if (t === 'INCOME') return '+' + v;
  if (t === 'REFUND') return '+' + v;
  if (t === 'TRANSFER') return v;
  return '−' + v; // gasto
};
LUCA.cat = function (code) { return LUCA.categories[code] || { emoji: '💸', name: code }; };
LUCA.relDate = function (d) {
  if (d === LUCA.today) return 'Hoy';
  var dt = new Date(d + 'T00:00:00'), tod = new Date(LUCA.today + 'T00:00:00');
  var diff = Math.round((tod - dt) / 86400000);
  if (diff === 1) return 'Ayer';
  var meses = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
  return dt.getDate() + ' ' + meses[dt.getMonth()];
};
