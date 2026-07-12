/* ============================================================
   LUCA — Gráficos en SVG puro (sin dependencias externas).
   Colores desde los datos; todos con resumen textual accesible.
   ============================================================ */
window.LUCA = window.LUCA || {};

/* Donut: data = [{label, value, color}] */
LUCA.donut = function (data, opts) {
  opts = opts || {};
  var total = data.reduce(function (s, d) { return s + d.value; }, 0) || 1;
  var size = opts.size || 180, sw = opts.stroke || 22, r = (size - sw) / 2, cx = size / 2, cy = size / 2;
  var circ = 2 * Math.PI * r, offset = 0;
  var segs = data.map(function (d) {
    var frac = d.value / total, len = frac * circ;
    var s = '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="' + d.color + '" stroke-width="' + sw + '" ' +
      'stroke-dasharray="' + len + ' ' + (circ - len) + '" stroke-dashoffset="' + (-offset) + '" transform="rotate(-90 ' + cx + ' ' + cy + ')"><title>' + d.label + ': ' + LUCA.fmt(d.value) + '</title></circle>';
    offset += len; return s;
  }).join('');
  var center = opts.centerLabel ?
    '<text x="' + cx + '" y="' + (cy - 4) + '" text-anchor="middle" font-size="11" fill="var(--luca-text-2)" font-family="var(--luca-font)">' + opts.centerLabel + '</text>' +
    '<text x="' + cx + '" y="' + (cy + 14) + '" text-anchor="middle" font-size="18" font-weight="700" fill="var(--luca-text)" font-family="var(--luca-font)" style="font-feature-settings:\'tnum\'">' + LUCA.fmt(total) + '</text>' : '';
  return '<svg viewBox="0 0 ' + size + ' ' + size + '" width="' + size + '" height="' + size + '" role="img" aria-label="Gasto por categoría, total ' + LUCA.fmt(total) + '">' + segs + center + '</svg>';
};

/* Barras agrupadas ingreso/gasto + línea de flujo neto. data=[{m,income,expense}] */
LUCA.cashflow = function (data, opts) {
  opts = opts || {};
  var w = opts.width || 560, h = opts.height || 200, pad = 28, gap = 10;
  var max = Math.max.apply(null, data.map(function (d) { return Math.max(d.income, d.expense); })) * 1.1 || 1;
  var bw = (w - pad * 2) / data.length;
  var barW = (bw - gap) / 2;
  function y(v) { return h - pad - (v / max) * (h - pad * 1.5); }
  var bars = '', line = '', pts = [];
  data.forEach(function (d, i) {
    var x0 = pad + i * bw + gap / 2;
    bars += '<rect x="' + x0 + '" y="' + y(d.income) + '" width="' + barW + '" height="' + (h - pad - y(d.income)) + '" rx="3" fill="var(--luca-income)"><title>' + d.m + ' ingresos ' + LUCA.fmt(d.income) + '</title></rect>';
    bars += '<rect x="' + (x0 + barW) + '" y="' + y(d.expense) + '" width="' + barW + '" height="' + (h - pad - y(d.expense)) + '" rx="3" fill="var(--luca-navy-600)"><title>' + d.m + ' gastos ' + LUCA.fmt(d.expense) + '</title></rect>';
    var cx = x0 + barW, cy = y(d.income - d.expense);
    pts.push([cx, cy]);
    bars += '<text x="' + (x0 + barW) + '" y="' + (h - 8) + '" text-anchor="middle" font-size="11" fill="var(--luca-text-2)" font-family="var(--luca-font)">' + d.m + '</text>';
  });
  line = '<polyline points="' + pts.map(function (p) { return p[0] + ',' + p[1]; }).join(' ') + '" fill="none" stroke="var(--luca-gold)" stroke-width="2"/>' +
    pts.map(function (p) { return '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="3" fill="var(--luca-gold)"/>'; }).join('');
  return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="100%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Ingresos vs gastos de los últimos meses con línea de flujo neto">' + bars + line + '</svg>';
};

/* Línea acumulada + proyección punteada + límite. */
LUCA.projection = function (real, projected, limit, opts) {
  opts = opts || {};
  var w = opts.width || 560, h = opts.height || 200, pad = 30;
  var all = real.concat(projected), maxV = Math.max(limit, Math.max.apply(null, all)) * 1.1 || 1;
  var n = all.length;
  function x(i) { return pad + (i / (n - 1)) * (w - pad * 2); }
  function y(v) { return h - pad - (v / maxV) * (h - pad * 1.5); }
  var realPts = real.map(function (v, i) { return x(i) + ',' + y(v); }).join(' ');
  var projStart = real.length - 1;
  var projPts = [[projStart, real[real.length - 1]]].concat(projected.map(function (v, i) { return [real.length + i, v]; }))
    .map(function (p) { return x(p[0]) + ',' + y(p[1]); }).join(' ');
  var limitY = y(limit);
  return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="100%" role="img" aria-label="Gasto acumulado del mes con proyección a fin de mes y línea de límite ' + LUCA.fmt(limit) + '">' +
    '<line x1="' + pad + '" y1="' + limitY + '" x2="' + (w - pad) + '" y2="' + limitY + '" stroke="var(--luca-alert)" stroke-width="1.5" stroke-dasharray="2 3"/>' +
    '<text x="' + (w - pad) + '" y="' + (limitY - 5) + '" text-anchor="end" font-size="10" fill="var(--luca-alert-text)" font-family="var(--luca-font)">Límite ' + LUCA.fmt(limit) + '</text>' +
    '<polyline points="' + realPts + '" fill="none" stroke="var(--luca-navy-600)" stroke-width="2.5"/>' +
    '<polyline points="' + projPts + '" fill="none" stroke="var(--luca-navy-300)" stroke-width="2" stroke-dasharray="4 4"/>' +
    '</svg>';
};

/* Mini barras historial. data=[{label,value}], highlightLast */
LUCA.miniBars = function (data, opts) {
  opts = opts || {};
  var w = opts.width || 260, h = opts.height || 90, pad = 16, gap = 8;
  var max = Math.max.apply(null, data.map(function (d) { return d.value; })) || 1;
  var bw = (w - pad * 2) / data.length - gap;
  var bars = data.map(function (d, i) {
    var bh = (d.value / max) * (h - pad * 1.6);
    var x0 = pad + i * ((w - pad * 2) / data.length);
    var last = (i === data.length - 1);
    return '<rect x="' + x0 + '" y="' + (h - pad - bh) + '" width="' + bw + '" height="' + bh + '" rx="3" fill="' + (last ? 'var(--luca-gold)' : 'var(--luca-navy-300)') + '"><title>' + d.label + ': ' + LUCA.fmt(d.value) + '</title></rect>' +
      '<text x="' + (x0 + bw / 2) + '" y="' + (h - 4) + '" text-anchor="middle" font-size="10" fill="var(--luca-text-2)" font-family="var(--luca-font)">' + d.label + '</text>';
  }).join('');
  return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="100%" role="img" aria-label="Historial de gasto por mes">' + bars + '</svg>';
};

/* Sparkline. values=[..] */
LUCA.sparkline = function (values, opts) {
  opts = opts || {};
  var w = opts.width || 120, h = opts.height || 40, pad = 3;
  var max = Math.max.apply(null, values), min = Math.min.apply(null, values), rng = (max - min) || 1;
  var pts = values.map(function (v, i) {
    return (pad + (i / (values.length - 1)) * (w - pad * 2)) + ',' + (h - pad - ((v - min) / rng) * (h - pad * 2));
  }).join(' ');
  return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h + '" aria-hidden="true"><polyline points="' + pts + '" fill="none" stroke="var(--luca-navy-300)" stroke-width="2"/></svg>';
};
