/* ============================================================
   LUCA — Datos mock (fixtures)
   Reproduce el schema de E2 y los 10 patrones del CSV de demo (E2 §15)
   para que alertas e insights se vean en vivo. Fecha de referencia: 2026-07-11.
   Este archivo se reemplaza por llamadas reales al backend sin tocar la UI.
   ============================================================ */
window.LUCA = window.LUCA || {};

LUCA.today = '2026-07-11';

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
  SALARY:           { emoji: '💵', name: 'Sueldo' },
  FREELANCE:        { emoji: '💻', name: 'Freelance' },
  BUSINESS_SALES:   { emoji: '🏪', name: 'Ventas y negocio' },
  REMITTANCE_SUPPORT:{ emoji: '💌', name: 'Remesas y ayuda' },
  OTHER_INCOME:     { emoji: '➕', name: 'Otros ingresos' },
  TRANSFER:         { emoji: '⇄',  name: 'Transferencia' }
};

LUCA.paymentMethods = {
  CASH: 'Efectivo', DEBIT_CARD: 'Débito', CREDIT_CARD: 'Crédito',
  TRANSFER: 'Transferencia', DIGITAL_WALLET: 'Billetera', OTHER: 'Otro'
};

LUCA.user = {
  user_id: 'usr_001', name: 'Mateo', phone: '+593 98 765 1234', phoneMasked: '+593 •• ••• 1234',
  email: 'mateo@example.ec', country: 'EC', currency: 'USD', timezone: 'America/Guayaquil',
  locale: 'es-EC', whatsapp: true, initials: 'M'
};

/* Movimientos de julio 2026 (mes actual). Incluye los patrones de E2 §15. */
LUCA.transactions = [
  // --- pendientes de confirmar (bandeja) ---
  { id: 'txn_090', type: 'EXPENSE', amount: 15.00, date: '2026-07-11', category: 'OTHER', merchant: '', desc: 'Gasté 15 en algo', method: 'CASH', status: 'PENDING_CONFIRMATION', source: 'CHAT', confidence: 0.42, needs: true, question: '¿En qué gastaste los $15?' },
  { id: 'txn_089', type: 'EXPENSE', amount: null, date: '2026-07-10', category: 'UTILITIES', merchant: 'Empresa Eléctrica', desc: 'Ayer pagué la luz', method: null, status: 'PENDING_CONFIRMATION', source: 'CHAT', confidence: 0.71, needs: true, question: '¿Cuánto pagaste por la luz?' },
  { id: 'txn_088', type: 'EXPENSE', amount: 42.30, date: '2026-07-09', category: 'FOOD_GROCERIES', merchant: 'Coral', desc: 'Compré cosas en Coral', method: 'DEBIT_CARD', status: 'PENDING_CONFIRMATION', source: 'CSV', confidence: 0.68, needs: false, question: '¿Esto fue súper o restaurante?' },
  // --- duplicado (patrón §11.6) ---
  { id: 'txn_087', type: 'EXPENSE', amount: 18.50, date: '2026-07-11', time: '13:20', category: 'FOOD_OUT', merchant: 'KFC', desc: 'Almuerzo KFC', method: 'DEBIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.96, flag: 'duplicate' },
  { id: 'txn_086', type: 'EXPENSE', amount: 18.50, date: '2026-07-11', time: '13:31', category: 'FOOD_OUT', merchant: 'KFC', desc: 'Ayer gasté 18.50 en KFC', method: 'DEBIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.96, flag: 'duplicate' },
  // --- confirmados varios julio ---
  { id: 'txn_085', type: 'EXPENSE', amount: 25.00, date: '2026-07-11', time: '20:05', category: 'FOOD_OUT', merchant: 'La Nutria', desc: 'Cena con amigos', method: 'CREDIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.94 },
  { id: 'txn_084', type: 'INCOME',  amount: 400.00, date: '2026-07-10', category: 'FREELANCE', merchant: '', desc: 'Me pagaron por un diseño', method: 'TRANSFER', status: 'CONFIRMED', source: 'CHAT', confidence: 0.97 },
  { id: 'txn_083', type: 'EXPENSE', amount: 8.75, date: '2026-07-10', category: 'TRANSPORT', merchant: 'Uber', desc: 'Uber al centro', method: 'DIGITAL_WALLET', status: 'CONFIRMED', source: 'CHAT', confidence: 0.95 },
  { id: 'txn_082', type: 'EXPENSE', amount: 62.40, date: '2026-07-09', category: 'FOOD_GROCERIES', merchant: 'Supermaxi', desc: 'Compra de la semana', method: 'DEBIT_CARD', status: 'CONFIRMED', source: 'CSV', confidence: 0.99 },
  // --- suscripción olvidada (§15.2, §11.7) ---
  { id: 'txn_081', type: 'EXPENSE', amount: 12.99, date: '2026-07-08', category: 'SUBSCRIPTIONS', merchant: 'Netflix', desc: 'Netflix', method: 'CREDIT_CARD', status: 'CONFIRMED', source: 'CSV', confidence: 0.99, recurring: true },
  { id: 'txn_080', type: 'EXPENSE', amount: 6.99, date: '2026-07-07', category: 'SUBSCRIPTIONS', merchant: 'Spotify', desc: 'Spotify', method: 'CREDIT_CARD', status: 'CONFIRMED', source: 'CSV', confidence: 0.99, recurring: true },
  { id: 'txn_079', type: 'EXPENSE', amount: 22.00, date: '2026-07-07', category: 'FOOD_OUT', merchant: 'Sweet&Coffee', desc: 'Café y postre', method: 'CASH', status: 'CONFIRMED', source: 'CHAT', confidence: 0.9 },
  { id: 'txn_078', type: 'EXPENSE', amount: 45.00, date: '2026-07-06', category: 'FOOD_OUT', merchant: 'Il Capo', desc: 'Cena italiana', method: 'CREDIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.92 },
  { id: 'txn_077', type: 'EXPENSE', amount: 30.00, date: '2026-07-05', category: 'FOOD_OUT', merchant: 'Menestras del Negro', desc: 'Almuerzo familiar', method: 'CASH', status: 'CONFIRMED', source: 'CHAT', confidence: 0.93 },
  // --- alquiler recurrente (§15.5) ---
  { id: 'txn_076', type: 'EXPENSE', amount: 320.00, date: '2026-07-05', category: 'HOUSING', merchant: 'Arriendo depto', desc: 'Pagué el arriendo', method: 'TRANSFER', status: 'CONFIRMED', source: 'CHAT', confidence: 0.98, recurring: true },
  { id: 'txn_075', type: 'EXPENSE', amount: 28.90, date: '2026-07-04', category: 'UTILITIES', merchant: 'Interagua', desc: 'Pagué el agua', method: 'TRANSFER', status: 'CONFIRMED', source: 'CHAT', confidence: 0.97 },
  { id: 'txn_074', type: 'EXPENSE', amount: 39.90, date: '2026-07-04', category: 'TELECOM', merchant: 'Netlife', desc: 'Internet del mes', method: 'CREDIT_CARD', status: 'CONFIRMED', source: 'CSV', confidence: 0.99, recurring: true },
  { id: 'txn_073', type: 'EXPENSE', amount: 12.50, date: '2026-07-03', category: 'TRANSPORT', merchant: 'Gasolinera', desc: 'Puse gasolina', method: 'DEBIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.9 },
  // --- retiro de cajero: transferencia, no gasto (§15.7) ---
  { id: 'txn_072', type: 'TRANSFER', amount: 100.00, date: '2026-07-03', category: 'TRANSFER', merchant: 'Cajero Banco', desc: 'Retiré 100 del cajero', method: 'CASH', status: 'CONFIRMED', source: 'CHAT', confidence: 0.95 },
  // --- transferencia propia a ahorro (§15.8) ---
  { id: 'txn_071', type: 'TRANSFER', amount: 50.00, date: '2026-07-02', category: 'TRANSFER', merchant: 'Ahorros', desc: 'Pasé 50 a ahorros', method: 'TRANSFER', status: 'CONFIRMED', source: 'CHAT', confidence: 0.94 },
  // --- reembolso asociado (§15.9) ---
  { id: 'txn_070', type: 'REFUND', amount: 20.00, date: '2026-07-02', category: 'SHOPPING', merchant: 'Marathon', desc: 'Me devolvieron lo de la camiseta', method: 'CREDIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.9, related: 'txn_069' },
  { id: 'txn_069', type: 'EXPENSE', amount: 45.00, date: '2026-07-01', category: 'SHOPPING', merchant: 'Marathon', desc: 'Compré una camiseta', method: 'CREDIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.95 },
  { id: 'txn_068', type: 'INCOME',  amount: 900.00, date: '2026-07-01', category: 'SALARY', merchant: '', desc: 'Depósito de sueldo', method: 'TRANSFER', status: 'CONFIRMED', source: 'CSV', confidence: 0.99, recurring: true },
  { id: 'txn_067', type: 'EXPENSE', amount: 15.00, date: '2026-07-01', category: 'FAMILY_SUPPORT', merchant: '', desc: 'Le di 15 a mi mamá', method: 'CASH', status: 'CONFIRMED', source: 'CHAT', confidence: 0.9 },
  { id: 'txn_066', type: 'EXPENSE', amount: 9.90, date: '2026-07-08', category: 'PETS', merchant: 'Pet Center', desc: 'Comida del perro', method: 'DEBIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.93 },
  { id: 'txn_065', type: 'EXPENSE', amount: 55.00, date: '2026-07-06', category: 'HEALTH', merchant: 'Fybeca', desc: 'Medicinas', method: 'DEBIT_CARD', status: 'CONFIRMED', source: 'CHAT', confidence: 0.94 }
];

/* Resumen del mes actual (calculado a mano para el mock; en real lo hace el backend) */
LUCA.monthSummary = {
  month: 'Julio 2026',
  income: 1300.00,   // sueldo 900 + freelance 400
  expenses: 916.03,  // gastos netos (incluye 2 KFC dup, menos reembolso 20)
  net: 383.97,
  prevNet: 205.10,
  deltaPct: 87
};

/* Series para gráficos */
LUCA.categoryBreakdown = [
  { code: 'FOOD_OUT',       amount: 199.00, color: '#1F3A5F' },
  { code: 'HOUSING',        amount: 320.00, color: '#2E5A8F' },
  { code: 'FOOD_GROCERIES', amount: 104.70, color: '#6FA0D6' },
  { code: 'UTILITIES',      amount: 28.90,  color: '#F5B301' },
  { code: 'TELECOM',        amount: 39.90,  color: '#B8860B' },
  { code: 'HEALTH',         amount: 55.00,  color: '#0E7A50' },
  { code: 'OTHER',          amount: 168.63, color: '#9AA7B5' }
];

LUCA.cashflow6m = [
  { m: 'Feb', income: 1180, expense: 1010 },
  { m: 'Mar', income: 1250, expense: 1120 },
  { m: 'Abr', income: 1200, expense: 980 },
  { m: 'May', income: 1300, expense: 1180 },
  { m: 'Jun', income: 1210, expense: 1005 },
  { m: 'Jul', income: 1300, expense: 916 }
];

/* Presupuestos (E2 §9.3) */
LUCA.budgets = [
  { id: 'bud_001', code: 'FOOD_OUT',      limit: 200, spent: 199.00, threshold: 0.80 },
  { id: 'bud_002', code: 'TRANSPORT',     limit: 80,  spent: 21.25, threshold: 0.80 },
  { id: 'bud_003', code: 'FOOD_GROCERIES',limit: 260, spent: 104.70, threshold: 0.80 },
  { id: 'bud_004', code: 'ENTERTAINMENT', limit: 60,  spent: 0.00, threshold: 0.80 },
  { id: 'bud_005', code: 'SHOPPING',      limit: 120, spent: 45.00, threshold: 0.75 }
];

/* Insights (E2 §11) */
LUCA.insights = [
  { id: 'ins_1', type: 'budget_warn', severity: 'alert', date: '2026-07-11', cat: 'FOOD_OUT',
    title: 'Ojo con Restaurantes', body: 'Vas en $199 de $200 este mes. Te queda $1.', evidence: 'Presupuesto casi agotado.' },
  { id: 'ins_2', type: 'duplicate', severity: 'alert', date: '2026-07-11', cat: 'FOOD_OUT',
    title: 'Posible cargo duplicado', body: 'Encontré dos cargos de $18.50 en KFC con 11 minutos de diferencia. ¿Ambos son correctos?', evidence: 'Mismo comercio, mismo monto, <24h.' },
  { id: 'ins_3', type: 'projection', severity: 'info', date: '2026-07-10', cat: 'FOOD_OUT',
    title: 'Proyección de fin de mes', body: 'Si mantienes el ritmo, podrías cerrar el mes con ~$430 en Restaurantes.', evidence: 'Gasto actual proyectado al día 31.' },
  { id: 'ins_4', type: 'subscription', severity: 'info', date: '2026-07-08', cat: 'SUBSCRIPTIONS',
    title: 'Suscripción detectada', body: 'Netflix se cobra ~$12.99 cada mes. ¿La marcamos como recurrente?', evidence: '3 cargos iguales, intervalo ~30 días.' },
  { id: 'ins_5', type: 'unusual', severity: 'info', date: '2026-07-06', cat: 'HEALTH',
    title: 'Gasto inusual en Salud', body: 'Gastaste $55 en Fybeca, más del doble de tu mediana en Salud.', evidence: 'Monto ≥ 2× mediana de la categoría.' }
];

/* Recurrentes / suscripciones (E2 §11.7) */
LUCA.recurring = {
  confirmed: [
    { name: 'Netflix', emoji: '📺', amount: 12.99, next: '2026-08-08', freq: 'Mensual', cat: 'SUBSCRIPTIONS' },
    { name: 'Spotify', emoji: '📺', amount: 6.99, next: '2026-08-07', freq: 'Mensual', cat: 'SUBSCRIPTIONS' },
    { name: 'Netlife (internet)', emoji: '📱', amount: 39.90, next: '2026-08-04', freq: 'Mensual', cat: 'TELECOM' },
    { name: 'Arriendo', emoji: '🏠', amount: 320.00, next: '2026-08-05', freq: 'Mensual', cat: 'HOUSING' }
  ],
  detected: [
    { name: 'Microsoft 365', emoji: '📺', amount: 6.99, freq: '~30 días', cat: 'SUBSCRIPTIONS' }
  ]
};

/* Recordatorios (E2 §12) */
LUCA.reminders = [
  { id: 'rem_1', type: 'pago', title: 'Pago de internet', text: 'Tu pago de internet suele llegar en 3 días.', when: 'En 3 días', on: true },
  { id: 'rem_2', type: 'resumen', title: 'Resumen semanal', text: 'Cada domingo a las 8 pm.', when: 'Domingo 20:00', on: true },
  { id: 'rem_3', type: 'presupuesto', title: 'Presupuesto por vencer', text: 'Aviso en los últimos 5 días del mes.', when: 'Últimos 5 días', on: true },
  { id: 'rem_4', type: 'inactividad', title: 'Recordatorio de registro', text: 'Si no registras nada en 3 días.', when: 'Requiere tu permiso', on: false }
];

/* Tickets de soporte (H3) */
LUCA.tickets = [
  { id: 142, subject: 'No reconozco un cargo en mi estado de cuenta', status: 'wip', priority: 'high', ago: 'hace 2 h',
    reason: 'Reclamo sobre movimiento — requiere revisión humana',
    summary: 'El cliente reporta un cargo de $54.00 que no reconoce en su cuenta de la casa de valores.',
    transcript: [
      { from: 'user', text: 'Hola, veo un cargo de 54 dólares que no hice' },
      { from: 'bot', text: 'Entiendo. Un cargo que no reconoces es un tema serio y prefiero que lo vea una persona del equipo. Te creo un ticket con prioridad alta.' },
      { from: 'bot', text: 'Esto se lo paso a una persona del equipo — te contactan pronto. Ticket #142 creado.' }
    ],
    replies: [ { from: 'agent', name: 'Equipo Luca', text: 'Hola Mateo, estamos revisando el cargo de $54.00 con el área correspondiente. Te confirmamos hoy mismo.', ago: 'hace 1 h' } ] },
  { id: 138, subject: '¿Cómo actualizo mis datos de contacto?', status: 'done', priority: 'low', ago: 'ayer',
    reason: 'Consulta de proceso resuelta',
    summary: 'Consulta sobre actualización de datos de contacto.',
    transcript: [ { from: 'user', text: '¿Cómo cambio mi correo registrado?' } ],
    replies: [ { from: 'agent', name: 'Equipo Luca', text: 'Puedes cambiarlo en Cuenta → Perfil. Ya quedó documentado, cualquier cosa nos escribes.', ago: 'ayer' } ] }
];

/* Base de conocimiento (H3) */
LUCA.helpCategories = [
  { icon: 'user', title: 'Mi cuenta', count: 8 },
  { icon: 'refresh', title: 'Procesos', count: 12 },
  { icon: 'file', title: 'Documentos', count: 6 },
  { icon: 'shield', title: 'Seguridad', count: 5 }
];
LUCA.helpArticles = [
  { id: 'a1', cat: 'Mi cuenta', title: '¿Cómo vinculo mi WhatsApp con Luca?', body: 'Ve a Cuenta → Canales conectados y escanea el código QR, o toca el botón para abrir WhatsApp con un mensaje precargado. En cuanto envíes tu primer mensaje, quedará vinculado.' },
  { id: 'a2', cat: 'Procesos', title: '¿Cómo registro un gasto?', body: 'Escríbele a Luca algo como "gasté 5 en almuerzo" y él lo clasifica. También puedes registrarlo manualmente en Movimientos → Nuevo.' },
  { id: 'a3', cat: 'Procesos', title: '¿Cómo creo un presupuesto?', body: 'En Presupuestos → Nuevo, elige la categoría, el límite mensual y el umbral de alerta (por defecto 80%). Luca te avisará antes de que lo superes.' },
  { id: 'a4', cat: 'Seguridad', title: '¿Qué datos guarda Luca de mí?', body: 'Solo lo necesario para llevar tus cuentas: tus movimientos, presupuestos y el historial de chat. Puedes verlo y exportarlo o borrarlo cuando quieras en Cuenta → Datos y privacidad.' },
  { id: 'a5', cat: 'Documentos', title: '¿Cómo importo mi estado de cuenta?', body: 'En Movimientos → Importar CSV, arrastra tu archivo del banco. Luca detecta duplicados y te avisa de lo que necesite confirmación.' }
];

/* Notificaciones (campana) */
LUCA.notifications = [
  { id: 'n1', unread: true, icon: 'alert', title: 'Ojo con Restaurantes', text: 'Vas $199 de $200 este mes.', ago: 'hace 1 h', href: 'insights.html' },
  { id: 'n2', unread: true, icon: 'copy', title: 'Posible cargo duplicado', text: 'Dos cargos de $18.50 en KFC.', ago: 'hace 1 h', href: 'insights.html' },
  { id: 'n3', unread: true, icon: 'ticket', title: 'Respuesta a tu ticket #142', text: 'El equipo está revisando tu cargo.', ago: 'hace 1 h', href: 'tickets.html' },
  { id: 'n4', unread: false, icon: 'tv', title: 'Suscripción detectada', text: 'Netflix ~$12.99 al mes.', ago: 'hace 3 d', href: 'recurrentes.html' }
];

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
