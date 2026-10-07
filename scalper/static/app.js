const $ = (id) => document.getElementById(id);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d = 2) => v == null || isNaN(v) ? '—' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const sig = (v, d = 8) => v == null ? '—' : Number(Number(v).toPrecision(d)).toString();
const signed = (v, d = 2) => v == null ? '—' : (v > 0 ? '+' : '') + num(v, d);
const time = (s) => new Date(s * 1000).toLocaleTimeString();
const dt = (s) => new Date(s * 1000).toLocaleString();

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST', headers: { 'X-Requested-With': 'scalper', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `${path}: ${r.status}`);
  return data;
}

let pd = 2;  // price decimals from the symbol's tick size
const px = (v) => v == null ? '—' : Number(v).toFixed(pd);
let S = null, chart, candleSeries, lines = {}, settingsLoaded = false, lastSymbol = null;

/* ---------- chart ---------- */
function initChart() {
  const el = $('chart');
  chart = LightweightCharts.createChart(el, {
    width: el.clientWidth, height: el.clientHeight,
    layout: { background: { color: 'transparent' }, textColor: css('--muted') },
    grid: { vertLines: { color: css('--border') }, horzLines: { color: css('--border') } },
    rightPriceScale: { borderColor: css('--border') },
    timeScale: { borderColor: css('--border'), timeVisible: true, secondsVisible: false },
  });
  candleSeries = chart.addCandlestickSeries({ upColor: css('--up'), downColor: css('--down'), borderVisible: false,
    wickUpColor: css('--up'), wickDownColor: css('--down') });
  window.addEventListener('resize', () => chart.resize(el.clientWidth, el.clientHeight));
}
function setLine(key, price, color, title) {
  if (lines[key]) { candleSeries.removePriceLine(lines[key]); delete lines[key]; }
  if (price) lines[key] = candleSeries.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
}

/* ---------- order book ladder ---------- */
function ladder(el, levels, side, maxQ, mine) {
  el.replaceChildren(...levels.map(([p, q]) => {
    const row = document.createElement('div');
    row.className = `row ${side}` + (mine.has(p) ? ' mine' : '');
    const bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.width = `${Math.min(100, (q / maxQ) * 100)}%`;
    const a = document.createElement('span'); a.textContent = px(p);
    const b = document.createElement('span'); b.textContent = sig(q, 6);
    row.append(bar, a, b);
    return row;
  }));
}

function meter(id, v, valId) {
  const f = $(id), x = Math.max(-1, Math.min(1, v || 0));
  f.className = 'fill ' + (x >= 0 ? 'pos' : 'neg');
  f.style.left = x >= 0 ? '50%' : `${50 + x * 50}%`;
  f.style.width = `${Math.abs(x) * 50}%`;
  $(valId).textContent = signed(v, 2);
}

/* ---------- render ---------- */
function render(s) {
  S = s;
  const t = s.trader, m = s.market, sg = t?.signal, st = s.settings;
  const mode = s.demo ? 'demo' : s.account;
  if (m) {
    const d = Math.max(0, Math.min(10, Math.round(-Math.log10(m.tick))));
    if (d !== pd || !candleSeries.__fmt) {  // chart axis and price lines use the symbol's tick size
      pd = d;
      candleSeries.applyOptions({ priceFormat: { type: 'price', precision: pd, minMove: m.tick } });
      candleSeries.__fmt = true;
    }
  }
  $('mode').textContent = (s.demo ? 'DEMO · ' : '') + s.account.toUpperCase();
  $('mode').className = 'pill ' + mode;
  if (lastSymbol !== st.symbol) { lastSymbol = st.symbol; $('symbol').value = st.symbol; candleSeries.setData([]); }
  document.querySelectorAll('[data-unit=quote]').forEach((e) => { e.textContent = m ? `(${m.quote})` : ''; });

  // banner
  const problems = [s.error, s.feed.error && !s.feed.connected ? `Market data: ${s.feed.error}` : null,
    t?.halt_reason ? `Trading ${t.halt_reason}` : null, s.feed.stale && s.feed.connected ? 'Order book data is stale' : null].filter(Boolean);
  $('banner').classList.toggle('hidden', !problems.length);
  $('banner').textContent = problems.join(' · ');

  // buttons
  $('btn-start').disabled = !t || t.enabled;
  $('btn-pause').disabled = !t || !t.enabled;
  $('btn-close').disabled = !t || (t.state === 'IDLE');

  const b = s.book;
  $('price').textContent = b ? px(b.mid) : '—';
  $('price-sub').textContent = b ? `${m.base}/${m.quote} · spread ${num(b.spread_bps, 2)} bps` : (s.feed.connected ? 'waiting for data' : 'connecting…');
  $('state').textContent = t ? (t.enabled ? t.state : (t.state === 'IDLE' ? 'PAUSED' : t.state + ' (paused)')) : 'OFF';
  $('state').className = 'value ' + (t?.state === 'HOLDING' ? 'up' : '');
  $('state-sub').textContent = t ? (t.state === 'BUYING' ? `buy ${sig(t.buy_order.amount)} @ ${px(t.buy_order.price)}` :
    t.state === 'HOLDING' ? 'waiting for take-profit' : (t.blocker || (sg?.buy ? 'signal ready' : 'scanning'))) : (s.error || '');

  const p = t?.position;
  $('pos').textContent = p ? `${signed(p.unrealized_pct, 3)}%` : 'none';
  $('pos').className = 'value ' + (p ? (p.unrealized >= 0 ? 'up' : 'down') : '');
  $('pos-sub').textContent = p ? `${sig(p.qty, 6)} ${m.base} @ ${px(p.avg)} · TP ${px(p.tp_price)} · stop ${px(p.stop_price)} · ${Math.round(p.age_s)}s` : '';

  const bal = s.balances;
  $('bal').textContent = bal.quote == null ? '—' : `${sig(bal.quote, 8)} ${m.quote}`;
  $('bal-sub').textContent = bal.base == null ? '' : `${sig(bal.base, 8)} ${m.base} free` +
    (s.account === 'paper' ? ' · paper balance resets on restart' : '');
  const ss = s.stats;
  $('pnl').textContent = m ? `${signed(ss.pnl, 4)} ${m.quote}` : '—';
  $('pnl').className = 'value ' + (ss.pnl > 0 ? 'up' : ss.pnl < 0 ? 'down' : '');
  $('pnl-sub').textContent = t ? `today ${signed(t.daily_pnl, 4)} · ${ss.trades} trades` : '';

  // book
  if (b) {
    const mine = new Set([t?.buy_order?.price, p?.tp_price].filter(Boolean));
    const maxQ = Math.max(...b.bids.map((x) => x[1]), ...b.asks.map((x) => x[1]));
    ladder($('asks'), b.asks.slice(0, 10).reverse(), 'ask', maxQ, mine);
    ladder($('bids'), b.bids.slice(0, 10), 'bid', maxQ, mine);
    $('ladder-mid').textContent = `${px(b.mid)}  ·  micro ${px(b.microprice)}`;
    $('book-meta').textContent = `last trade ${px(b.last_trade)}`;
    candleSeries.setData(b.candles.map((c) => ({ time: c.t, open: c.o, high: c.h, low: c.l, close: c.c })));
  }
  setLine('entry', p?.avg || t?.buy_order?.price, css('--blue'), p ? 'entry' : 'buy');
  setLine('tp', p?.tp_price, css('--up'), 'TP');
  setLine('stop', p?.stop_price, css('--down'), 'stop');

  // signal
  if (sg) {
    meter('score-fill', sg.score, 'score-val');
    meter('imb-fill', sg.imbalance, 'imb-val');
    meter('mic-fill', sg.micro, 'mic-val');
    meter('flow-fill', sg.flow, 'flow-val');
    $('mark-entry').style.left = `${50 + st.signal.entry_score * 50}%`;
    $('mark-exit').style.left = `${50 + st.signal.exit_score * 50}%`;
    const need = t.target_gross_pct * st.signal.min_vol_mult;
    const checks = [
      [sg.spread_ok, `Spread ≤ ${st.signal.max_spread_bps} bps`],
      [sg.flow_ok, `${sg.trades} trades / ${st.signal.flow_window_s}s`],
      [sg.vol_ok, `1m range ${sg.avg_range_pct == null ? '—' : num(sg.avg_range_pct, 3) + '%'} ≥ ${num(need, 3)}%`],
      [sg.trend_ok, st.signal.trend_filter ? 'Above 1m EMA' : 'Trend filter off'],
      [sg.score_ok && sg.confirmed_s >= st.signal.confirm_s, `Score held ${num(sg.confirmed_s, 1)}s`],
      [t.enabled, t.enabled ? 'Trading on' : 'Paused'],
    ];
    $('checks').innerHTML = checks.map(([ok, txt]) => `<li class="${ok ? 'ok' : ''}">${esc(txt)}</li>`).join('');
    $('blockers').textContent = sg.buy ? 'All conditions met.' : (sg.blockers || []).join(' · ');
    $('sig-meta').textContent = `target +${num(t.target_gross_pct, 3)}% gross (fees ${num(t.fees.maker_pct, 3)}% / ${num(t.fees.taker_pct, 3)}%)`;
  }

  // stats
  const stat = (l, v) => `<div class="stat"><b>${esc(v)}</b><span>${esc(l)}</span></div>`;
  $('stats').innerHTML = [
    stat('Trades', ss.trades), stat('Win rate', ss.win_rate == null ? '—' : num(ss.win_rate, 1) + '%'),
    stat(`Net P&L (${m?.quote || ''})`, signed(ss.pnl, 6)), stat('Avg per trade', ss.avg_pnl_pct == null ? '—' : signed(ss.avg_pnl_pct, 3) + '%'),
    stat('Profit factor', ss.profit_factor == null ? '—' : num(ss.profit_factor, 2)), stat(`Fees paid (${m?.quote || ''})`, num(ss.fees, 6)),
    stat('Avg hold', ss.avg_hold_s == null ? '—' : Math.round(ss.avg_hold_s) + 's'), stat('Losses in a row', t?.consecutive_losses ?? '—'),
  ].join('');
  $('perf-meta').textContent = `${st.symbol} · ${s.account} account`;

  if (!settingsLoaded) { fillSettings(st); settingsLoaded = true; }
}

async function loadTables() {
  const [trades, events] = await Promise.all([api('/api/trades'), api('/api/events')]);
  $('trades').innerHTML = '<tr><th>Closed</th><th>Symbol</th><th>Entry</th><th>Exit</th><th>P&L</th><th>%</th><th>Why</th></tr>' +
    (trades.length ? trades.map((x) => `<tr><td>${esc(dt(x.exit_ts))}</td><td>${esc(x.symbol)}</td><td>${esc(sig(x.entry_price))}</td>
      <td>${esc(sig(x.exit_price))}</td><td class="${x.pnl >= 0 ? 'up' : 'down'}">${esc(signed(x.pnl, 6))}</td>
      <td class="${x.pnl >= 0 ? 'up' : 'down'}">${esc(signed(x.pnl_pct, 3))}</td><td>${esc(x.reason)}</td></tr>`).join('')
      : '<tr><td colspan="7" class="muted">No trades yet</td></tr>');
  $('events').innerHTML = '<tr><th>Time</th><th>Message</th></tr>' +
    events.map((e) => `<tr class="${esc(e.level)}"><td>${esc(time(e.ts))}</td><td class="msg">${esc(e.message)}</td></tr>`).join('');
}

/* ---------- settings form ---------- */
function fillSettings(st) {
  for (const el of $('settings').elements) {
    if (!el.name) continue;
    const [a, b] = el.name.split('.');
    const v = b ? st[a][b] : st[a];
    if (el.type === 'checkbox') el.checked = !!v; else el.value = v;
  }
}
function readSettings() {
  const out = JSON.parse(JSON.stringify(S.settings));
  for (const el of $('settings').elements) {
    if (!el.name) continue;
    const [a, b] = el.name.split('.');
    const v = el.type === 'checkbox' ? el.checked : Number(el.value);
    if (b) out[a][b] = v; else out[a] = v;
  }
  return out;
}

$('settings').onsubmit = async (e) => {
  e.preventDefault();
  try { await api('/api/settings', readSettings()); $('settings-msg').textContent = 'Saved.'; }
  catch (err) { $('settings-msg').textContent = err.message; }
  settingsLoaded = false;
};
$('symbol-form').onsubmit = async (e) => {
  e.preventDefault();
  const symbol = $('symbol').value.trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (!symbol || !S) return;
  try { await api('/api/settings', { ...S.settings, symbol }); lastSymbol = null; settingsLoaded = false; }
  catch (err) { alert(err.message); }
};
$('btn-start').onclick = async () => {
  const live = S?.account === 'live' && !S?.demo;
  if (live && !confirm(`Start trading ${S.settings.symbol} with REAL money?`)) return;
  try { await api('/api/start', {}); } catch (err) { alert(err.message); }
};
$('btn-pause').onclick = () => api('/api/pause', {});
$('btn-close').onclick = () => { if (confirm('Exit the open trade at market price now?')) api('/api/close', {}); };

async function tick() {
  try { render(await api('/api/status')); } catch (e) { $('banner').classList.remove('hidden'); $('banner').textContent = 'Lost connection to the bot. Is run.py still running?'; }
}
initChart();
tick(); loadTables();
setInterval(tick, 700);
setInterval(() => loadTables().catch(() => {}), 3000);
