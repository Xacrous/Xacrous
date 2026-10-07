const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => v == null ? '—' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const day = (ms) => new Date(ms).toISOString().slice(0, 10);
const when = (ms) => ms ? new Date(ms).toLocaleString() : '—';
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { 'X-Requested-With': 'btcbot', ...(opts.headers || {}) } });
  if (r.status === 401) { location.href = '/login'; throw new Error('login required'); }
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
const post = (path) => api(path, { method: 'POST' });
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let status = null;
let priceChart, candleSeries, smaSeries, entrySeries, exitSeries, eqChart, eqSeries, bhSeries;

function chartOptions(el) {
  return {
    width: el.clientWidth, height: el.clientHeight,
    layout: { background: { color: 'transparent' }, textColor: css('--muted') },
    grid: { vertLines: { color: css('--border') }, horzLines: { color: css('--border') } },
    rightPriceScale: { borderColor: css('--border') }, timeScale: { borderColor: css('--border'), minBarSpacing: 0.05 },
    localization: { priceFormatter: (v) => Math.round(v).toLocaleString() },
  };
}

function initCharts() {
  priceChart = LightweightCharts.createChart($('chart'), chartOptions($('chart')));
  candleSeries = priceChart.addCandlestickSeries({
    upColor: css('--up'), downColor: css('--down'), borderVisible: false,
    wickUpColor: css('--up'), wickDownColor: css('--down'),
  });
  smaSeries = priceChart.addLineSeries({ color: css('--sma'), lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  entrySeries = priceChart.addLineSeries({ color: css('--up'), lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  exitSeries = priceChart.addLineSeries({ color: css('--down'), lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
  eqChart = LightweightCharts.createChart($('equity-chart'), { ...chartOptions($('equity-chart')), rightPriceScale: { mode: 1, borderColor: css('--border') } });
  eqSeries = eqChart.addLineSeries({ color: css('--accent'), lineWidth: 2, title: 'Strategy' });
  bhSeries = eqChart.addLineSeries({ color: css('--muted'), lineWidth: 1, title: 'Buy & hold' });
  const fit = () => {
    priceChart.resize($('chart').clientWidth, $('chart').clientHeight);
    eqChart.resize($('equity-chart').clientWidth, $('equity-chart').clientHeight);
  };
  window.addEventListener('resize', fit);
}

async function loadStatus() {
  status = await api('/api/status');
  const s = status;
  const modeName = s.testnet ? 'testnet' : s.mode;
  $('mode').textContent = modeName.toUpperCase();
  $('mode').className = 'pill ' + modeName;
  renderAccount(s);
  $('paused').classList.toggle('hidden', !s.paused);
  $('btn-pause').textContent = s.paused ? 'Resume trading' : 'Pause trading';
  $('price').textContent = fmt(s.price);
  $('symbol').textContent = `${s.symbol} · ${s.exchange}`;
  $('position').textContent = s.in_position ? `LONG ${fmt(s.position_qty, 5)} ${s.base}` : 'FLAT';
  $('position').className = 'value ' + (s.in_position ? 'buy' : '');
  $('position-sub').textContent = s.in_position ? `worth ${fmt(s.position_qty * s.price)} ${s.quote}` : `waiting for a buy signal`;
  $('equity').textContent = s.equity == null ? '—' : `${fmt(s.equity)} ${s.quote}`;
  $('equity-sub').textContent = s.paper_start
    ? `paper account · ${fmt((s.equity / s.paper_start - 1) * 100, 1)}% since start`
    : `${fmt(s.balance_quote)} ${s.quote} + ${fmt(s.balance_base, 5)} ${s.base}`;
  const sig = s.last_signal;
  if (sig) {
    $('signal').textContent = sig.action;
    $('signal').className = 'value ' + (sig.action === 'BUY' ? 'buy' : sig.action === 'SELL' ? 'sell' : '');
    $('signal-sub').textContent = `${day(sig.candle_ts)} close · ${sig.reason}`;
    $('lv-sma').textContent = fmt(sig.sma);
    $('lv-entry').textContent = fmt(sig.entry_level);
    $('lv-exit').textContent = fmt(sig.exit_level);
  }
  $('checks').textContent = `last check ${when(s.last_check)} · next ${when(s.next_check)}`;
}

function renderAccount(s) {
  const c = s.connection, el = $('account');
  if (s.mode === 'paper' || !c) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  if (!c.ok) {
    el.className = 'banner bad';
    el.textContent = `Exchange account check failed, so the bot will not trade: ${c.error}`;
  } else if (c.ip_restricted === false) {
    el.className = 'banner warn';
    el.textContent = 'Connected to Binance, but this API key is not restricted to your server\'s IP address. Add the IP restriction in Binance API Management.';
  } else {
    el.className = 'banner good';
    el.textContent = `Connected to ${s.testnet ? 'Binance Spot Testnet' : 'Binance'} · spot trading on · withdrawals off` +
      (c.ip_restricted ? ' · IP restricted' : '') + ` · checked ${when(c.checked)}`;
  }
}

async function loadChart(btTrades) {
  const rows = await api('/api/candles?limit=1500');
  candleSeries.setData(rows.map(r => ({ time: r.t, open: r.o, high: r.h, low: r.l, close: r.c })));
  const line = (k) => rows.filter(r => r[k] != null).map(r => ({ time: r.t, value: r[k] }));
  smaSeries.setData(line('sma')); entrySeries.setData(line('entry')); exitSeries.setData(line('exit'));
  const first = rows.length ? rows[0].t : 0;
  const snap = (ms) => { // markers must sit on an existing bar
    const t = Math.floor(ms / 1000); let best = rows[0]?.t;
    for (const r of rows) { if (r.t <= t) best = r.t; else break; } return best;
  };
  const markers = [];
  for (const t of btTrades) {
    if (t.entry_ts / 1000 >= first) markers.push({ time: snap(t.entry_ts), position: 'belowBar', color: css('--up'), shape: 'arrowUp', text: 'B' });
    if (!t.open && t.exit_ts / 1000 >= first) markers.push({ time: snap(t.exit_ts), position: 'aboveBar', color: css('--down'), shape: 'arrowDown', text: 'S' });
  }
  const orders = await api('/api/orders');
  for (const o of orders) {
    if (o.candle_ts / 1000 >= first) markers.push({ time: snap(o.candle_ts), position: o.side === 'BUY' ? 'belowBar' : 'aboveBar',
      color: css('--accent'), shape: 'circle', text: `bot ${o.side}` });
  }
  markers.sort((a, b) => a.time - b.time);
  candleSeries.setMarkers(markers);
  renderOrders(orders);
}

function stat(label, value) { return `<div class="stat"><b>${value}</b><span>${label}</span></div>`; }

async function loadBacktest(query = '') {
  const bt = await api('/api/backtest' + query);
  const s = bt.stats, p = bt.params;
  const f = $('bt-form');
  f.sma_length.value = p.sma_length; f.entry_band.value = +(p.entry_band * 100).toFixed(2); f.exit_band.value = +(p.exit_band * 100).toFixed(2);
  $('bt-stats').innerHTML = [
    stat('Closed trades', s.trades + (s.open_trade ? ' + 1 open' : '')),
    stat('Win rate', s.win_rate_pct == null ? '—' : s.win_rate_pct + '%'),
    stat('Yearly return (strategy)', fmt(s.cagr_pct, 1) + '%'),
    stat('Yearly return (buy & hold)', fmt(s.buy_hold_cagr_pct, 1) + '%'),
    stat('Worst drop (strategy)', fmt(s.max_drawdown_pct, 1) + '%'),
    stat('Worst drop (buy & hold)', fmt(s.buy_hold_max_drawdown_pct, 1) + '%'),
    stat('Profit factor', s.profit_factor ?? '—'),
    stat('Time in market', fmt(s.time_in_market_pct, 0) + '%'),
  ].join('') + `<div class="stat"><b>${day(bt.start)} → ${day(bt.end)}</b><span>Period · fee ${bt.fee * 100}% per side</span></div>`;
  eqSeries.setData(bt.equity_curve.map(e => ({ time: Math.floor(e.ts / 1000), value: e.equity })));
  const rows = await api('/api/candles?limit=5000');
  const startIdx = rows.findIndex(r => r.t >= Math.floor(bt.start / 1000));
  const base = rows[startIdx]?.o;
  bhSeries.setData(rows.slice(startIdx).map(r => ({ time: r.t, value: 10000 * r.c / base })));
  eqChart.timeScale().fitContent();
  $('bt-trades').innerHTML = '<tr><th>Bought</th><th>Price</th><th>Sold</th><th>Price</th><th>Result</th></tr>' +
    bt.trades.slice().reverse().map(t => `<tr><td>${day(t.entry_ts)}</td><td>${fmt(t.entry_price)}</td>
      <td>${t.open ? 'open' : day(t.exit_ts)}</td><td>${fmt(t.exit_price)}</td>
      <td class="${t.return_pct > 0 ? 'buy' : 'sell'}">${t.return_pct > 0 ? '+' : ''}${fmt(t.return_pct, 1)}%</td></tr>`).join('');
  return bt;
}

function renderOrders(orders) {
  $('orders').innerHTML = '<tr><th>Time</th><th>Side</th><th>Qty</th><th>Price</th><th>Value</th><th>Mode</th></tr>' +
    (orders.length ? orders.map(o => `<tr><td>${when(o.ts)}</td><td class="${o.side === 'BUY' ? 'buy' : 'sell'}">${o.side}</td>
      <td>${fmt(o.qty, 6)}</td><td>${fmt(o.price)}</td><td>${fmt(o.cost)}</td><td>${esc(o.mode)}</td></tr>`).join('')
      : '<tr><td colspan="6" class="muted">No orders yet</td></tr>');
}

async function loadEvents() {
  const ev = await api('/api/events');
  $('events').innerHTML = '<tr><th>Time</th><th>Message</th></tr>' +
    ev.map(e => `<tr class="${esc(e.level)}"><td>${when(e.ts)}</td><td>${esc(e.message)}</td></tr>`).join('');
}

async function refresh(withBacktest = false) {
  try {
    await loadStatus();
    const bt = withBacktest ? await loadBacktest() : null;
    await loadChart(bt ? bt.trades : (window.lastBt?.trades || []));
    if (bt) { window.lastBt = bt; priceChart.timeScale().fitContent(); }
    await loadEvents();
  } catch (e) { console.error(e); }
}

$('btn-run').onclick = async () => { $('btn-run').disabled = true; try { await post('/api/run'); } finally { $('btn-run').disabled = false; refresh(); } };
$('btn-logout').onclick = async () => { await post('/api/logout').catch(() => {}); location.href = '/login'; };
$('btn-pause').onclick = async () => {
  const pausing = !status?.paused;
  if (pausing || confirm('Resume automatic trading?')) { await post(pausing ? '/api/pause' : '/api/resume'); refresh(); }
};
$('bt-form').onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  const q = `?sma_length=${f.sma_length.value}&entry_band=${f.entry_band.value / 100}&exit_band=${f.exit_band.value / 100}`;
  const bt = await loadBacktest(q); window.lastBt = bt; loadChart(bt.trades);
};

initCharts();
refresh(true);
setInterval(refresh, 60_000);
