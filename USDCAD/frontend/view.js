export const num = (x, digits = 4) => Number.isFinite(x) ? x.toFixed(digits) : '—';
export const signed = (x, digits = 2) => Number.isFinite(x) ? `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(x).toFixed(digits)}` : '—';
export const pct = x => Number.isFinite(x) ? `${(x * 100).toFixed(1).replace(/\.0$/, '')}%` : '—';
export const escape = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const dateLabel = d => new Date(`${d}T12:00:00Z`).toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
export function atOrBefore(rows, date, field = 'date') {
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i][field] <= date) return rows[i];
  return null;
}
export function rangeRows(rows, end, range) {
  const start = range === 'all' ? rows[0].date : `${Number(end.slice(0, 4)) - Number(range)}${end.slice(4)}`;
  return rows.filter(r => r.date >= start && r.date <= end);
}
export function csv(rows, keys) {
  const cell = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  return [keys.map(cell).join(','), ...rows.map(r => keys.map(k => cell(r[k])).join(','))].join('\r\n');
}
