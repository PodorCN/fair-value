import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parseHTML } from 'linkedom';

test('an open static dashboard follows new daily JSON and preserves a pinned date', async () => {
  const initial = JSON.parse(await readFile(new URL('../data/dashboard.json', import.meta.url), 'utf8'));
  const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
  const { document, window } = parseHTML(html);
  const previous = { document: globalThis.document, fetch: globalThis.fetch, setInterval: globalThis.setInterval };
  let published = structuredClone(initial), tick;
  const requests = [];
  globalThis.document = document;
  globalThis.setInterval = (callback, milliseconds) => {
    assert.equal(milliseconds, 300000);
    tick = callback;
    return 0;
  };
  globalThis.fetch = async value => {
    const url = new URL(value, 'https://podorcn.github.io/fair-value/');
    requests.push(url);
    assert(url.searchParams.has('_'), 'config and data requests must bypass old CDN entries');
    let body;
    if (url.pathname.endsWith('/config.json')) body = { dashboardUrl: './data/dashboard.json', statusUrl: './data/update-status.json' };
    else if (url.pathname.endsWith('/update-status.json')) body = { result: 'success' };
    else if (url.pathname.endsWith('/dashboard.json')) body = structuredClone(published);
    else throw new Error(`Unexpected request ${url}`);
    return { ok: true, json: async () => body };
  };
  for (const input of document.querySelectorAll('input[type=checkbox]')) input.checked = input.hasAttribute('checked');
  document.querySelector('#history-count option').setAttribute('selected', '');
  const waitFor = async predicate => {
    for (let i = 0; i < 100; i++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert.fail(document.getElementById('network-error').textContent || 'Dashboard did not finish rendering');
  };
  const publishNext = () => {
    const last = published.history.at(-1);
    const date = new Date(`${last.date}T12:00:00Z`);
    do { date.setUTCDate(date.getUTCDate() + 1); } while ([0, 6].includes(date.getUTCDay()));
    const next = { ...last, date: date.toISOString().slice(0, 10), spot: last.spot + .001 };
    published.history.push(next);
    published.latest = { ...published.latest, ...next };
    published.valuation_date = next.date;
    return next;
  };
  try {
    await import('./app.js');
    await waitFor(() => document.getElementById('spot').textContent === initial.latest.spot.toFixed(4));
    assert(document.getElementById('network-error').hidden);
    const first = publishNext();
    await tick();
    assert.equal(document.getElementById('spot').textContent, first.spot.toFixed(4));
    assert.equal(document.getElementById('asof').max, first.date);
    assert.equal(document.getElementById('asof').value, first.date);
    assert(document.querySelector('#valuation-chart svg').getAttribute('aria-label').endsWith(first.date));

    const picker = document.getElementById('asof');
    picker.value = '2026-09-26';
    picker.dispatchEvent(new window.Event('change'));
    assert.equal(picker.value, '2026-09-25');
    const pinnedSpot = document.getElementById('spot').textContent;
    const second = publishNext();
    await tick();
    assert.equal(picker.max, second.date);
    assert.equal(picker.value, '2026-09-25');
    assert.equal(document.getElementById('spot').textContent, pinnedSpot);
    document.getElementById('latest').click();
    assert.equal(picker.value, second.date);
    assert.equal(document.getElementById('spot').textContent, second.spot.toFixed(4));
    assert.equal(requests.filter(r => r.pathname.endsWith('/config.json')).length, 3);
    assert(requests.every(r => r.pathname.startsWith('/fair-value/')));
  } finally {
    Object.assign(globalThis, previous);
  }
});
