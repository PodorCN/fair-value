import { num, signed, pct, escape as esc, dateLabel, atOrBefore, rangeRows, csv } from './view.js';

const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value; };
let data, config, range = '5', pinned = false, loading = false;
const names = { usdcad: 'USD/CAD', wti: 'WTI crude', us2y: 'US 2Y yield', ca2y: 'Canada 2Y yield', vix: 'VIX', usd_broad: 'Broad USD', audusd: 'AUD/USD', nzdusd: 'NZD/USD', usdnok: 'USD/NOK', us_cpi: 'US CPI', ca_cpi: 'Canada CPI' };

async function fetchJSON(url) {
  // Static hosts use a CDN: a fresh URL also avoids a cached prior daily file.
  const separator = url.includes('?') ? '&' : '?';
  const res = await fetch(`${url}${separator}_=${Date.now()}`, { cache: 'no-store', signal: AbortSignal.timeout(20000) });
  if (!res.ok) throw new Error(`Snapshot request failed (HTTP ${res.status}).`);
  return res.json();
}

async function load() {
  if (loading) return;
  loading = true;
  $('refresh').disabled = true;
  try {
    config = await fetchJSON('./config.json');
    const next = await fetchJSON(config.dashboardUrl);
    if (next.schema_version !== 1 || !next.history?.length || !next.monthly?.length) throw new Error('Unsupported or incomplete snapshot.');
    data = next;
    $('network-error').hidden = true;
    $('load-state').hidden = true;
    $('dashboard').hidden = false;
    $('asof').min = data.history[0].date;
    $('asof').max = data.valuation_date;
    if (!pinned || !$('asof').value || $('asof').value > data.valuation_date) $('asof').value = data.valuation_date;
    render();
    try {
      const status = await fetchJSON(config.statusUrl);
      if (status.result === 'failed') showError(`Last backend update failed at ${status.finished_at}. The last published snapshot is retained. ${status.error || ''}`);
    } catch { /* Snapshot is usable; source and run metadata remain visible. */ }
  } catch (err) {
    $('load-state').hidden = true;
    showError(`${err.message} ${data ? 'Showing the last successfully loaded snapshot.' : 'Start the backend update, build, and serve the dashboard over HTTP.'}`);
  } finally {
    loading = false;
    $('refresh').disabled = false;
  }
}
function showError(message) { text('network-error', message); $('network-error').hidden = false; }
function selectDate(date) {
  $('asof').value = date;
  pinned = date !== data.valuation_date;
  render();
}

function render() {
  const r = atOrBefore(data.history, $('asof').value) || data.history[0];
  $('asof').value = r.date;
  const historical = r.date !== data.valuation_date;
  const snapshotAge = Math.floor((Date.now() - Date.parse(data.generated_at)) / 86400000);
  const health = snapshotAge > 3 ? 'Snapshot overdue' : ({ ok: 'Sources current', stale: 'Inputs delayed', degraded: 'Partial update', cached: 'Research cache' }[data.health] || data.health);
  text('health', health);
  $('health').className = `badge ${data.health === 'ok' && snapshotAge <= 3 ? 'healthy' : 'warning'}`;
  const notices = [...data.notices];
  if (snapshotAge > 3) notices.unshift({ severity: 'warning', title: 'Snapshot overdue', detail: `Published ${snapshotAge} days ago. Refreshing the browser only reloads published data; the backend worker must run to recalculate.` });
  if (historical) notices.unshift({ severity: 'info', title: 'Historical view', detail: `Valuation and portfolio readings are pinned to ${dateLabel(r.date)}. Source health and driver attribution describe the latest published run.` });
  $('notices').innerHTML = notices.map(n => `<div class="notice ${esc(n.severity)}"><span class="notice-icon">${n.severity === 'warning' ? '!' : 'i'}</span><div><strong>${esc(n.title)}</strong><span>${esc(n.detail)}</span></div></div>`).join('');
  text('spot', num(r.spot));
  text('spot-detail', `${dateLabel(r.date)} · aligned daily fixing`);
  text('fair', num(r.beer_fv));
  text('fair-detail', `Raw OLS ${num(r.beer_raw_fv)} · annual refit`);
  text('gap', `${signed(r.beer_gap_pct)}%`);
  text('gap-detail', `${num(r.beer_z, 2)}σ · USD ${r.beer_gap_pct >= 0 ? 'rich' : 'cheap'} vs BEER`);
  text('ppp', num(r.ppp_fv));
  text('ppp-detail', `PPP ${r.ppp_state.toLowerCase()} with BEER direction`);
  $('gap-position').style.left = `${Math.max(3, Math.min(97, 50 + r.beer_z * 15))}%`;
  const trail = rangeRows(data.history, r.date, '1');
  spark('spot-spark', trail, 'spot', '#8894a7');
  spark('fair-spark', trail, 'beer_fv', '#3966bd');

  // A historical date is a UI lookup over backend-computed decisions, not a new signal.
  const effectiveDate = historical ? r.date : data.run_date;
  const held = atOrBefore(data.monthly, effectiveDate, 'execution_date');
  text('held-weight', held ? pct(held.usd_weight) : '—');
  text('held-cad', held ? `${pct(1 - held.usd_weight)} CAD` : 'No executed decision');
  $('held-bar').style.width = held ? pct(held.usd_weight) : '50%';
  text('held-meta', held ? `Decision ${dateLabel(held.date)} · effective ${dateLabel(held.execution_date)}` : 'The first model decision has not executed yet.');
  text('ppp-state', held?.ppp_state || '—');
  text('layer1-weight', held ? `${pct(held.layer1_usd)} USD` : '—');
  text('layer2-weight', held ? `${pct(held.layer2_usd)} USD` : '—');
  text('daily-weight', `${pct(r.usd_weight)} USD`);
  text('daily-description', `If evaluated on ${dateLabel(r.date)}. Indicative only; the model rebalances at month-end.`);
  const pending = data.monthly.find(m => m.date <= effectiveDate && m.execution_date > effectiveDate);
  const next = historical ? 'Monthly schedule · month-end + 5 business days' : `Next decision ${dateLabel(data.allocation.next_decision)}`;
  text('schedule-detail', pending ? `${pct(pending.usd_weight)} USD pending · execution ${dateLabel(pending.execution_date)}` : next);

  const points = rangeRows(data.history, r.date, range);
  const series = [{ key: 'spot', label: 'USD/CAD', color: '#263449' }, { key: 'beer_fv', label: 'BEER fair value', color: '#3966bd' }];
  if ($('show-ppp').checked) series.push({ key: 'ppp_fv', label: 'PPP', color: '#b18a45', dashed: true });
  if ($('show-raw').checked) series.push({ key: 'beer_raw_fv', label: 'Raw OLS', color: '#8297c0', dashed: true });
  chart('valuation-chart', points, series, { band: $('show-bands').checked, pin: true });
  text('chart-date-range', `${dateLabel(points[0].date)} — ${dateLabel(points.at(-1).date)}`);
  text('beer-score', signed(-r.beer_z));
  text('peer-score', signed(r.peer_z));
  text('signal-date', dateLabel(r.date));
  chart('signal-chart', points, [{ key: 'beer_z', label: 'BEER score', color: '#3966bd', multiplier: -1 }, { key: 'peer_z', label: 'Peer score', color: '#368b83' }], { signal: true });
  $('peer-readings').innerHTML = ['aud', 'nzd', 'nok'].map(c => `<div><span>CAD vs ${c.toUpperCase()}</span><strong>${signed(r[`peer_${c}_z`])}<small> z</small></strong></div>`).join('');
  renderDrivers();
  renderHistory(r.date, historical);
  renderMethod();
  text('published', `Calculated ${new Date(data.generated_at).toLocaleString('en-CA', { timeZone: 'America/Toronto', hour12: false })} ET · ${data.mode === 'live' ? 'Public sources' : 'Cached research inputs'}`);
}

function renderDrivers() {
  const max = Math.max(...data.drivers.map(d => Math.abs(d.contribution_log_bp)), 1);
  $('driver-rows').innerHTML = data.drivers.map(d => `<div class="driver-row"><div><span>${esc(d.label)}</span><strong>${signed(d.contribution_log_bp, 1)}</strong></div><div class="driver-track"><span class="${d.contribution_log_bp < 0 ? 'negative' : ''}"></span></div></div>`).join('');
  $('driver-rows').querySelectorAll('.driver-track span').forEach((bar, i) => {
    const value = data.drivers[i].contribution_log_bp;
    bar.style.width = `${Math.abs(value) / max * 47}%`;
    bar.style[value < 0 ? 'right' : 'left'] = '50%';
  });
  text('driver-total', `${signed(data.raw_fv_change_log_bp, 1)} bp`);
  text('refit-detail', Math.abs(data.refit_change_log_bp) > 0.01 ? `includes ${signed(data.refit_change_log_bp, 1)} bp from refit` : 'same coefficients over the period');
  text('driver-date', `Latest valuation ${dateLabel(data.valuation_date)} · + contribution raises USD/CAD fair value.`);
}
function visibleMonthly(date, historical) { return data.monthly.filter(m => !historical || m.date <= date); }
function renderHistory(date, historical) {
  let rows = visibleMonthly(date, historical).slice().reverse();
  if ($('history-count').value !== 'all') rows = rows.slice(0, Number($('history-count').value));
  $('history-body').innerHTML = rows.map(m => `<tr><td class="decision-date">${esc(m.date)}</td><td>${esc(m.data_through)}</td><td>${esc(m.execution_date)}</td><td>${num(m.spot)}</td><td>${num(m.beer_fv)}</td><td>${signed(m.beer_z)}</td><td>${signed(m.peer_z)}</td><td><span class="tier">${signed(m.layer1_tier, 0)}</span></td><td><span class="tier">${signed(m.layer2_tier, 0)}</span></td><td><strong class="weight-cell">${pct(m.usd_weight)}</strong></td></tr>`).join('');
}
function renderMethod() {
  text('model-version', data.model_version);
  const fit = data.coefficients.at(-1);
  $('method-facts').innerHTML = `<span>Last refit <strong>${esc(fit.date)}</strong></span><span>Estimation rows <strong>${fit.n}</strong></span><span>Execution lag <strong>5 business days</strong></span>`;
  const issues = data.sources.filter(s => s.status !== 'ok').length;
  text('source-summary', issues ? `${issues} inputs need attention` : 'All inputs current');
  $('sources-body').innerHTML = data.sources.map(s => `<tr><td>${esc(names[s.name])}</td><td><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.provider)} · ${esc(s.series_id)} ↗</a></td><td>${esc(s.observation_date)}</td><td>${esc(s.available_date)}</td><td>${esc(s.valuation_observation_date)}</td><td>${num(s.valuation_value, 3)}</td><td><span class="source-status ${s.status === 'ok' ? 'good' : 'warn'}">${esc(s.status === 'ok' ? 'Current' : s.status)}</span>${s.error ? `<small class="source-error">${esc(s.error)}</small>` : ''}</td></tr>`).join('');
  text('data-policy', `${data.methodology.cpi_policy} ${data.provenance.history_caveat} Daily source status is delayed after 7 business days; CPI after 65 business days. Latest observation describes the newest fetched print; used observation and value describe the aligned valuation. CPI released after the common market date enters a later valuation.`);
  $('coefs-body').innerHTML = data.coefficients.slice().reverse().map(f => `<tr><td>${esc(f.date)}</td><td>${f.n}</td>${['intercept', 'rate_diff_2y', 'ln_wti', 'vix', 'ln_usd_broad'].map(k => `<td>${num(f[k], 6)}</td>`).join('')}</tr>`).join('');
  const p = data.ppp_calibration;
  text('calibration', `Frozen PPP: log FV = log(CPI US / CPI CA) + ${num(p.constant, 6)}; sigma ${num(p.sigma, 6)}. ${p.months} monthly observations, ${p.train_start} to ${p.train_end}. Spread input is in percentage points; VIX is in index points.`);
  $('provenance').innerHTML = `<p>Source: <code>${esc(data.provenance.paper)}</code>; implementation traced to <code>TDAM/usdcad_fv/src/allocation.py</code>, revision 18. Research commit <code>${esc(data.provenance.research_commit)}</code>.</p><p>The standalone engine is checked against all 189 original monthly tier/weight records. It does not import code from the research repository at runtime.</p><ul>${data.methodology.limitations.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
}

function spark(id, rows, key, color) {
  const vals = rows.map(r => r[key]).filter(Number.isFinite);
  const min = Math.min(...vals), spread = Math.max(...vals) - min || 1;
  const path = vals.map((v, i) => `${i ? 'L' : 'M'}${(i / Math.max(1, vals.length - 1) * 220).toFixed(1)},${(30 - (v - min) / spread * 26).toFixed(1)}`).join(' ');
  $(id).innerHTML = `<svg viewBox="0 0 220 34" preserveAspectRatio="none"><path d="${path}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

function chart(id, points, series, opts = {}) {
  const host = $(id), W = 1000, H = opts.signal ? 220 : 340;
  const m = { l: 18, r: 65, t: 18, b: 35 }, w = W - m.l - m.r, h = H - m.t - m.b;
  const values = points.flatMap(p => series.map(s => p[s.key] * (s.multiplier || 1)));
  if (opts.band) values.push(...points.flatMap(p => [p.band_low, p.band_high]));
  const finite = values.filter(Number.isFinite);
  if (opts.signal) finite.push(-1.5, 1.5);
  let lo = Math.min(...finite), hi = Math.max(...finite), pad = (hi - lo) * 0.12 || 0.01;
  lo -= pad; hi += pad;
  const x = i => m.l + i / Math.max(1, points.length - 1) * w;
  const y = v => m.t + (hi - v) / (hi - lo) * h;
  const pathFor = (rows, fn) => rows.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(2)},${y(fn(p)).toFixed(2)}`).join(' ');
  let svg = '';
  if (opts.signal) {
    for (const z of [-1.5, -1, -0.5, 0.5, 1, 1.5]) svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(z)}" y2="${y(z)}" stroke="#e2e6ec" stroke-dasharray="3 5"/>`;
  }
  for (let i = 0; i < 5; i++) {
    const v = lo + (hi - lo) * i / 4, py = y(v);
    svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${py}" y2="${py}" stroke="#edf0f3"/><text x="${W - m.r + 12}" y="${py + 4}" class="chart-axis">${num(v, opts.signal ? 1 : 3)}</text>`;
    const idx = Math.round(i / 4 * (points.length - 1));
    svg += `<text x="${x(idx)}" y="${H - 9}" text-anchor="${i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}" class="chart-axis">${new Date(`${points[idx].date}T12:00Z`).toLocaleDateString('en-CA', { month: 'short', year: 'numeric', timeZone: 'UTC' })}</text>`;
  }
  if (opts.band) {
    const upper = pathFor(points, p => p.band_high);
    const lower = points.map((p, i) => `L${x(i).toFixed(2)},${y(p.band_low).toFixed(2)}`).reverse().join(' ');
    svg += `<path d="${upper} ${lower} Z" fill="#3966bd" opacity=".075"/>`;
  }
  for (const s of series) svg += `<path d="${pathFor(points, p => p[s.key] * (s.multiplier || 1))}" fill="none" stroke="${s.color}" stroke-width="${opts.signal ? 1.8 : 2.2}" ${s.dashed ? 'stroke-dasharray="5 5"' : ''} stroke-linejoin="round"/>`;
  svg += `<g class="crosshair" visibility="hidden"><line class="cursor-line" y1="${m.t}" y2="${H - m.b}" stroke="#9faabb" stroke-dasharray="3 3"/>${series.map((s, i) => `<circle class="cursor-dot-${i}" r="4" fill="${s.color}" stroke="white" stroke-width="2"/>`).join('')}</g><rect class="hit-area" x="${m.l}" y="${m.t}" width="${w}" height="${h}" fill="transparent"/>`;
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${opts.signal ? 'Allocation scores' : 'Spot and fair value'} from ${points[0].date} to ${points.at(-1).date}"><title>${opts.signal ? 'Positive scores favor USD' : 'USD/CAD spot and model fair value'}</title>${svg}</svg><div class="chart-tooltip" hidden></div>`;
  const el = host.querySelector('svg'), area = host.querySelector('.hit-area'), cross = host.querySelector('.crosshair'), tooltip = host.querySelector('.chart-tooltip');
  const indexFrom = event => {
    const box = el.getBoundingClientRect();
    return Math.max(0, Math.min(points.length - 1, Math.round((((event.clientX - box.left) / box.width * W) - m.l) / w * (points.length - 1))));
  };
  area.addEventListener('pointermove', event => {
    const i = indexFrom(event), p = points[i];
    cross.setAttribute('visibility', 'visible');
    const line = cross.querySelector('line'); line.setAttribute('x1', x(i)); line.setAttribute('x2', x(i));
    series.forEach((s, j) => { const dot = cross.querySelector(`.cursor-dot-${j}`); dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(p[s.key] * (s.multiplier || 1))); });
    tooltip.hidden = false;
    tooltip.innerHTML = `<strong>${esc(dateLabel(p.date))}</strong>${series.map(s => `<div><span><i></i>${esc(s.label)}</span><b>${num(p[s.key] * (s.multiplier || 1), opts.signal ? 2 : 4)}</b></div>`).join('')}`;
    tooltip.querySelectorAll('i').forEach((dot, j) => { dot.style.background = series[j].color; });
    tooltip.style.left = `${Math.max(4, Math.min(host.clientWidth - 220, event.clientX - host.getBoundingClientRect().left + 16))}px`;
  });
  area.addEventListener('pointerleave', () => { tooltip.hidden = true; cross.setAttribute('visibility', 'hidden'); });
  if (opts.pin) area.addEventListener('click', event => selectDate(points[indexFrom(event)].date));
}

$('refresh').addEventListener('click', load);
$('asof').addEventListener('change', () => { if ($('asof').value && data) selectDate($('asof').value); });
$('latest').addEventListener('click', () => { if (data) selectDate(data.valuation_date); });
document.querySelectorAll('[data-range]').forEach(button => button.addEventListener('click', () => {
  range = button.dataset.range;
  document.querySelectorAll('[data-range]').forEach(b => { b.classList.toggle('selected', b === button); b.setAttribute('aria-pressed', String(b === button)); });
  if (data) render();
}));
['show-bands', 'show-ppp', 'show-raw', 'history-count'].forEach(id => $(id).addEventListener('change', () => { if (data) render(); }));
$('export').addEventListener('click', () => {
  if (!data) return;
  const rows = visibleMonthly($('asof').value, $('asof').value !== data.valuation_date);
  const blob = new Blob([csv(rows, ['date', 'data_through', 'execution_date', 'spot', 'beer_fv', 'ppp_fv', 'beer_z', 'peer_z', 'layer1_tier', 'layer2_tier', 'usd_weight'])], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `USDCAD-decisions-${$('asof').value}.csv`; a.click(); URL.revokeObjectURL(a.href);
});
load();
setInterval(load, 300000);
