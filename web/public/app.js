// Dashboard data comes from /api/summary (the finnamon CLI's JSON); the charts from /api/chart (a list of Vega-Lite specs,
// one panel each), refreshed when the server says the board changed; the intercom is an xterm.js terminal over a WebSocket
// to the household's Claude session.
// Edits (budgets, properties) go to the server, which runs the same finnamon commands a person would.
import { initTalk, talkMessage, talkConnected } from './talk.js';

const $ = (id) => document.getElementById(id);
const money = (x) => (x == null ? '' : (x < 0 ? '-' : '') + '$' + Math.abs(x).toLocaleString(undefined, { maximumFractionDigits: 0 }));
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const icon = (name, weight = 'regular') => `<svg class="ic" aria-hidden="true"><use href="#i-${weight}-${name}"/></svg>`;
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
const amount = (v) => Number(String(v ?? '').replace(/[$,\s]/g, ''));

// ---- theme ----------------------------------------------------------------------------------------------------------

// The stylesheet's tokens are the one palette; the terminal's ANSI colours are the only thing spelled out here.
const ANSI = {
  light: { black: '#1B1B1B', red: '#DC2626', green: '#4A8A6F', yellow: '#9C7E1A', blue: '#2563EB', magenta: '#A04F2F', cyan: '#0E7490', white: '#818181',
           brightBlack: '#515151', brightRed: '#DC2626', brightGreen: '#3A6B56', brightYellow: '#806815', brightBlue: '#1D4ED8', brightMagenta: '#7A3D24', brightCyan: '#155E75', brightWhite: '#1B1B1B' },
  dark:  { black: '#383838', red: '#F87171', green: '#84C9A1', yellow: '#F3DF7D', blue: '#93C5FD', magenta: '#EFB5A5', cyan: '#67E8F9', white: '#E8E8D0',
           brightBlack: '#818181', brightRed: '#FCA5A5', brightGreen: '#A8D4C0', brightYellow: '#FBF1C3', brightBlue: '#BFDBFE', brightMagenta: '#F5D0C3', brightCyan: '#A5F3FC', brightWhite: '#FFFFFF' },
};
const currentTheme = () => document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const termTheme = () => ({ background: token('--term-bg'), foreground: token('--ink'), cursor: token('--cinnamon'), selectionBackground: token('--cinnamon-100'), ...ANSI[currentTheme()] });
function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('theme', t); } catch {}
  for (const x of [term, iterm]) if (x) x.options.theme = termTheme();
  drawChart();
}
$('theme').addEventListener('click', () => applyTheme(currentTheme() === 'dark' ? 'light' : 'dark'));

// ---- dashboard --------------------------------------------------------------------------------------------------------

let summary = null;

let locked = false;   // the page's key was rotated (or its cookie is gone): every request answers 401 until `finnamon open` runs again
function lock() {   // once: the toast stays until dismissed, the pill says so after, and every control that needs the key is off
  if (locked) return;
  locked = true;
  $('banks-btn').hidden = true; $('add-menu').hidden = true; $('settings-btn').hidden = true; $('status').textContent = 'locked';
  const why = 'Locked: run finnamon open on the Finnamon box';
  $('health').className = 'pill down'; $('health').innerHTML = icon('warning-circle') + '<span class="pill-text">Locked</span>'; $('health').title = why; $('health').setAttribute('aria-label', why);   // kept, not hidden: on phones it is what pushes the header's buttons right
  for (const d of document.querySelectorAll('dialog[open]')) d.close();   // a modal would hide the toast, and its buttons cannot work now
  $('intercom-btn').disabled = true; $('intercom-btn').title = why; $('intercom-btn').setAttribute('aria-label', why);
  openIntercom(false); $('intercom-state').textContent = 'locked'; $('intercom-btn').dataset.state = 'DOWN';   // the dot: setState() is a no-op from here on
  requestAnimationFrame(() => $('toast-x').focus());   // the dialogs' close handlers return focus to controls just hidden
  try { ws?.close(); } catch {}   // whatever the socket still had, it goes nowhere now (send() refuses too)
  toast('Finnamon has locked this page. On the Finnamon box run finnamon open and open the address it prints; for a phone, finnamon open --host <its tailnet name>.', { kind: 'warn' });
  // from here on toast() and setState() are no-ops: nothing may replace this message, and the closing socket must not relabel 'locked' as 'offline'
}
async function loadSummary() {
  if (locked) return;
  const r = await fetch('/api/summary');
  if (r.status === 401) return lock();
  if (!r.ok) { $('banks-btn').hidden = true; $('status').textContent = 'finnamon CLI error'; return; }   // the count carries the ' · ' before it: left up, the error reads as one word with a stale number
  summary = await r.json();
  renderHeader(summary); renderNetWorth(summary); renderBudgets(summary); renderAlerts(summary);
  // an open edit panel keeps what the person typed; it is rebuilt when they save, remove, or reopen it
}

function renderHeader(s) {
  const n = (s.accounts || []).filter(a => !a.mirror_of).length, b = (s.status.items || []).length;
  const banks = `${plural(b, 'bank')}, ${plural(n, 'account')}`;   // no bank, no count: a clickable zero opens an empty list
  $('banks-btn').hidden = !b; $('banks-btn').textContent = banks; $('banks-btn').setAttribute('aria-label', `${banks}: show the list`);
  $('status').textContent = (b ? ' · ' : '') + (s.status.last_run ? `synced ${when(s.status.last_run)}` : 'not synced yet');
  if ($('banks-pop').open) renderBanks(s);
  const h = s.health || { level: 'ok', label: 'All good' };
  $('health').className = 'pill ' + (h.level === 'ok' ? '' : h.level);
  $('health').innerHTML = (h.level === 'ok' ? icon('check-circle', 'fill') : icon('warning-circle')) + `<span class="pill-text">${esc(h.label)}</span>`;
  $('health').title = h.label; $('health').setAttribute('aria-label', h.label);   // on phones only the icon shows
}

// ---- Settings, and Update Finnamon --------------------------------------------------------------------------------
// The cog opens Settings: the installed version, "Check for updates" (always a fresh `finnamon update --check`), Update, and
// a few read-only status rows. A dot on the cog (aria-label "Settings, update available")
// says an update is waiting, so it never hides the health pill. Update runs `finnamon update` on the box (web/server.js updater()). That restarts this
// server, so the page polls through the gap ("Restarting…") and reloads onto the new version, or shows why not.
let upd = null, updPhase = null, updUntil = 0, updChecking = false, setx = null;   // the last GET /api/update and /api/settings; updPhase: null | 'updating' | 'restarting' | 'failed'
let updLoaded = false;
const UPD_LABEL = { updating: 'Updating…', restarting: 'Restarting…', failed: 'Update failed' };
async function loadUpdate({ fresh = false } = {}) {
  if (locked || (updPhase && updPhase !== 'failed')) return;   // a failure stays until a look finds nothing left to pull (a terminal update)
  const q = fresh ? '?fresh=1' : updLoaded ? '' : '?load=1';   // the page's first look re-checks a result older than 10 min; the server caches for 30
  let r;
  try { r = await fetch('/api/update' + q); } catch { return; }
  if (r.status === 401) return lock();
  if (!r.ok) return;
  updLoaded = true;
  upd = await r.json();
  if (updPhase === 'failed' && !upd.running && !(upd.ahead > 0)) updPhase = null;
  if (upd.running) { updPhase = 'updating'; pollUpdate(); }   // started from another device, or before a reload
  renderUpdate();
}
const isDemo = () => !!summary?.status?.demo;
const sdot = (k) => `<i class="sdot ${k}" aria-hidden="true"></i>`;   // ok (no class) | warn | err | new | busy | off
function settingsRows() {   // label, a dot and the value; a detail under it in muted type. '…' until /api/settings answers
  const st = summary?.status || {}, inb = summary?.inbound;
  const row = (k, dot, v, sub = '') => `<li><span class="k">${k}</span><span class="v">${v == null ? '<span class="none">…</span>' : sdot(dot) + v}</span>${sub ? `<span class="sub">${sub}</span>` : ''}</li>`;
  const mode = { session: 'Dashboard’s assistant session', channel: 'Claude Code channel', daemon: 'The daemon' }[inb];
  const daemon = st.daemon_alive === false ? 'Daemon stopped' : st.daemon_alive ? 'Daemon running' : '';
  const tg = row('Telegram', st.daemon_alive === false ? 'err' : mode ? '' : 'off', mode || '<span class="none">Unknown</span>', daemon);
  const remote = !setx ? row('Remote access', '', null) : setx.remote.on ? row('Remote access', '', 'On', setx.remote.hosts.map(esc).join(', ')) : row('Remote access', 'off', 'Off', 'This computer only');
  const voice = !setx ? row('Voice', '', null) : !setx.voice ? row('Voice', 'off', 'Not available here')
    : setx.voice.stt === 'whisper' ? row('Voice', '', 'Whisper ready') : row('Voice', 'warn', 'Browser speech', 'Whisper is not set up');
  return `<h3 class="sep">Status</h3><ul class="rows">${tg}${remote}${voice}</ul>`;
}
function renderUpdate() {
  const b = $('settings-btn'), avail = upd && !upd.error && upd.ahead > 0;
  const badge = updPhase === 'failed' ? 'failed' : updPhase ? 'busy' : avail ? 'update' : '';   // the cog's dot replaces the header pill
  const label = { failed: 'Settings, update failed', busy: `Settings, ${updPhase === 'restarting' ? 'restarting' : 'updating'}`, update: 'Settings, update available' }[badge] || 'Settings';
  if (badge) b.dataset.badge = badge; else delete b.dataset.badge;
  b.setAttribute('aria-label', label); b.title = label;
  renderSettingsPop();
}
function renderSettingsPop() {
  const pop = $('settings-pop');
  const demo = isDemo(), avail = upd && !upd.error && upd.ahead > 0;
  const ver = upd?.current || setx?.version;
  const what = { updating: 'Updating… the dashboard restarts on its own when it is done.', restarting: 'Restarting… this page reconnects by itself.' }[updPhase];
  const line = (dot, text, role = true) => `<p class="state"${role ? ' role="status"' : ''}>${sdot(dot)}<span>${text}</span></p>`;
  let state = '';   // the update state: one line with its dot, and what goes with it underneath
  if (demo) state = line('off', 'Demo household: made-up data, nothing to update.', false);
  else if (what) state = line('busy', what);
  else if (updChecking) state = line('busy', 'Checking for updates…');
  else if (updPhase === 'failed') state = line('err', 'The update did not finish.', false) + (upd?.last?.tail ? `<pre>${esc(upd.last.tail)}</pre>` : '')
    + '<p class="more">Run <code>finnamon update</code> in a terminal on the Finnamon box to see why.</p>';
  else if (upd?.error) state = line('err', 'Could not check for updates') + `<p class="more">${esc(upd.error)}</p>`;
  else if (avail) state = line('new', upd.latest && upd.latest !== upd.current ? `Version ${esc(upd.latest)} available` : `${esc(plural(upd.ahead, 'new change'))} available`)
    + (upd.notes ? `<p class="more">New: ${esc(upd.notes)}</p>` : '');
  else if (upd) state = line('', 'Up to date');
  const busy = !!what, offer = !busy && (avail || updPhase === 'failed');   // Update is only there when there is something to install
  const html = `<div class="phead"><h2>Settings</h2><button class="x" id="settings-x" type="button" aria-label="Close">${icon('x')}</button></div>`
    + `<p class="ver num"><span class="nm">Finnamon</span> ${ver ? esc(ver) : (setx || upd) ? '<span class="nm">version unknown</span>' : ''}</p>` + state
    + (demo ? '' : `<div class="acts"><button type="button" class="quiet" id="update-check"${busy || updChecking ? ' disabled' : ''}>Check for updates</button>`
        + (offer ? `<button type="button" class="primary" id="update-go">${updPhase === 'failed' ? 'Try again' : 'Update'}</button>` : '') + '</div>')
    + settingsRows();
  if (html === pop.dataset.html) return;   // a poll that changed nothing leaves the dialog, and its focus, alone
  const f = pop.contains(document.activeElement) ? document.activeElement.id : null;
  pop.innerHTML = pop.dataset.html = html;
  $('settings-x').addEventListener('click', () => pop.close());
  $('update-go')?.addEventListener('click', startUpdate);
  $('update-check')?.addEventListener('click', checkUpdates);
  if (f) ($(f) && !$(f).disabled ? $(f) : $('settings-x')).focus();   // Update goes once it starts: focus stays in the dialog
}
async function checkUpdates() {
  updChecking = true; renderSettingsPop();
  try { await loadUpdate({ fresh: true }); } finally { updChecking = false; renderUpdate(); }
}
async function openSettings() {
  renderUpdate(); $('settings-pop').showModal();
  if (!isDemo() && !upd) loadUpdate();   // the first look may have been refused or not made yet
  try { const r = await fetch('/api/settings'); if (r.status === 401) return lock(); if (r.ok) { setx = await r.json(); renderSettingsPop(); } } catch {}
}
async function startUpdate() {
  let r;
  try { r = await fetch('/api/update', { method: 'POST' }); } catch { return toast('Could not reach the Finnamon box.', { kind: 'warn' }); }
  if (r.status === 401) return lock();
  const j = await r.json().catch(() => ({}));
  if (!r.ok && r.status !== 409) return toast(j.error || 'Could not start the update.', { kind: 'warn' });   // 409: one is running already, so follow it
  updPhase = 'updating'; updUntil = Date.now() + 10 * 60_000; renderUpdate(); pollUpdate();
}
let updTimer = null;
function pollUpdate() {
  clearTimeout(updTimer);
  updTimer = setTimeout(async () => {
    let j = null;
    try { const r = await fetch('/api/update'); if (r.ok) j = await r.json(); } catch {}
    if (!j) updPhase = 'restarting';   // the server is down mid-restart (or the proxy answers 502 for it)
    else if (j.running) { upd = j; updPhase = 'updating'; }
    else {
      // no exit line (systemd killed the run with the web unit): judge by what is left to pull, never by a version that may be missing
      const last = j.last, ok = !!last && (last.exit === 0 || (last.exit == null && !j.error && j.ahead === 0));
      if (ok) { try { if (j.current) sessionStorage.setItem('finnamon-updated', j.current); } catch {} return location.reload(); }   // new page files too
      upd = j; updPhase = 'failed'; return renderUpdate();
    }
    if (Date.now() > (updUntil ||= Date.now() + 10 * 60_000)) { updPhase = 'failed'; return renderUpdate(); }
    renderUpdate(); pollUpdate();
  }, 2000);
}
$('settings-btn').addEventListener('click', openSettings);
$('settings-pop').addEventListener('click', (e) => {   // the backdrop closes it, as banks-pop's does
  if (e.target !== e.currentTarget) return;
  const b = e.currentTarget.getBoundingClientRect();
  if (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom) e.currentTarget.close();
});
$('settings-pop').addEventListener('close', () => $('settings-btn').focus());
try { const v = sessionStorage.getItem('finnamon-updated'); if (v) { sessionStorage.removeItem('finnamon-updated'); setTimeout(() => toast(`Finnamon updated: now ${v}.`), 0); } } catch {}

function when(ts) {   // "2026-09-20 06:02:11" → "today 6:02 am" / "Sep 19, 6:01 am"
  const d = new Date(String(ts).replace(' ', 'T'));
  if (isNaN(d)) return ts;
  const t = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }).toLowerCase();
  return d.toDateString() === new Date().toDateString() ? `today ${t}` : `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${t}`;
}

function renderNetWorth(s) {
  const nw = s.networth, linked = (s.status.items || []).length > 0;
  countTo($('nw-total'), linked || nw.property ? nw.net_worth : null);
  const d = s.delta, el = $('nw-delta');   // {amount, pct} from the server, null until there is a previous month
  if (d && d.amount != null) {
    el.className = 'delta num ' + (d.amount < 0 ? 'down' : 'up');
    el.innerHTML = icon(d.amount < 0 ? 'arrow-down' : 'arrow-up') + ` ${money(Math.abs(d.amount))}${d.pct != null ? ` (${d.amount < 0 ? '' : '+'}${d.pct.toFixed(1)}%)` : ''} this month`;
  } else { el.className = 'delta'; el.textContent = linked ? '' : 'Link an account to see your numbers'; }
  const assets = (nw.cash || 0) + (nw.investments || 0) + (nw.property || 0);
  const share = (x) => assets > 0 && x ? `${Math.round(x / assets * 100)}%` : '';
  $('v-cash').textContent = linked ? money(nw.cash) : '—'; $('p-cash').textContent = linked ? share(nw.cash) : '';
  $('v-invest').textContent = linked ? money(nw.investments) : '—'; $('p-invest').textContent = linked ? share(nw.investments) : '';
  $('v-prop').textContent = nw.property || linked ? money(nw.property || 0) : '—'; $('p-prop').textContent = share(nw.property);
  $('v-debt').textContent = linked ? money(-(nw.liabilities || 0)) : '—'; $('p-debt').textContent = linked && nw.liabilities ? share(nw.liabilities) : '';   // of assets
  nwHistory = (s.history || []).map(r => ({ date: String(r.date), v: Number(r.net_worth), a: Number(r.assets),   // the CLI's history already counts property (at its current value)
    l: Number(r.liabilities) }))
    .filter(r => Number.isFinite(r.v) && Number.isFinite(Date.parse(r.date))).sort((a, b) => a.date < b.date ? -1 : a.date > b.date ? 1 : 0);
  drawNetWorth();
}

// the figure counts to its value: from zero on the first load, from the previous value on a refresh (a small move on a
// small delta). Ease-out over 900ms; tabular figures keep the width steady; reduced motion sets it at once.
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)');
function countTo(el, target) {
  cancelAnimationFrame(el._raf); el.removeAttribute('aria-busy');   // every exit below leaves the figure clean
  if (target == null) { delete el.dataset.shown; delete el.dataset.value; el.textContent = '—'; return; }   // nothing to show yet
  const from = Number(el.dataset.shown ?? el.dataset.value) || 0; el.dataset.value = target;   // from what is on screen right now
  if (REDUCED.matches || Math.round(from) === Math.round(target) || !Number.isFinite(target)) { el.dataset.shown = target; el.textContent = money(target); return; }
  const t0 = performance.now(), ms = 900;
  el.setAttribute('aria-busy', 'true');   // a status region: the settled figure is announced, not the frames
  const step = (now) => {
    const p = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - p, 3);
    el.dataset.shown = Math.round(from + (target - from) * e); el.textContent = money(Number(el.dataset.shown));
    if (p < 1) el._raf = requestAnimationFrame(step); else el.removeAttribute('aria-busy');
  };
  el._raf = requestAnimationFrame(step);
}

// the trend: an inline SVG (no library), daily net worth from the balances the daemon snapshots
let nwHistory = [], nwMonths = 6;
const nwShow = { a: false, l: false };   // assets and liabilities are one click away: on one scale a large property flattens the net-worth line
try { Object.assign(nwShow, JSON.parse(localStorage.getItem('nw-series') || '{}')); } catch {}
for (const b of document.querySelectorAll('.chead .legend[data-series]')) {
  b.setAttribute('aria-pressed', String(!!nwShow[b.dataset.series]));
  b.addEventListener('click', () => {
    nwShow[b.dataset.series] = !nwShow[b.dataset.series]; b.setAttribute('aria-pressed', String(nwShow[b.dataset.series]));
    try { localStorage.setItem('nw-series', JSON.stringify(nwShow)); } catch {}
    drawNetWorth();
  });
}
function drawNetWorth() {
  const svg = $('nw-svg'), empty = $('nw-chart-empty');
  $('nw-tip').hidden = true;   // a redraw drops the hover; the next move brings it back
  const since = new Date(); since.setMonth(since.getMonth() - nwMonths);
  const cut = since.toISOString().slice(0, 10);
  const pts = nwHistory.filter(r => r.date >= cut);
  $('nw-range').hidden = nwHistory.length < 2;   // no series at all: nothing to range over, nothing to toggle
  for (const b of document.querySelectorAll('.chead .legend[data-series]')) b.hidden = nwHistory.length < 2;
  if (pts.length < 2) { svg.hidden = true; empty.hidden = false; $('nw-alt').textContent = ''; return; }
  svg.hidden = false; empty.hidden = true;
  const shown = [nwShow.a && 'assets', nwShow.l && 'liabilities'].filter(Boolean);
  $('nw-alt').textContent = `Net worth over ${nwMonths} months, from ${money(pts[0].v)} to ${money(pts[pts.length - 1].v)}.${shown.length ? ` Also shown: ${shown.join(' and ')}, ending at ${shown.map(s => money(pts[pts.length - 1][s[0]])).join(' and ')}.` : ''}`;
  const W = Math.max(240, svg.clientWidth), H = Math.max(120, svg.clientHeight), L = 8, R = 14, T = 12, B = 22;   // drawn at its real size: text keeps its shape
  const LABEL_PX = 90, LABEL_MIN_GAP = 36, EDGE = 20, Y_PAD = 0.15, ROW = (H - T - B) / 2;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const all = pts.flatMap(r => [r.v, nwShow.a ? r.a : NaN, nwShow.l ? r.l : NaN].filter(Number.isFinite));   // one scale for whatever is shown
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * Y_PAD || Math.abs(hi) * 0.02 || 1; lo -= pad; hi += pad;
  const t0 = Date.parse(pts[0].date), t1 = Date.parse(pts[pts.length - 1].date) || t0 + 1;
  const x = (r) => L + (W - L - R) * ((Date.parse(r.date) - t0) / Math.max(1, t1 - t0));
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const path = (key) => pts.filter(r => Number.isFinite(r[key])).map(r => `${x(r).toFixed(1)},${y(r[key]).toFixed(1)}`).join(' ');
  const line = path('v');
  const area = `M${x(pts[0]).toFixed(1)},${H - B} L${line.replace(/ /g, ' L')} L${x(pts[pts.length - 1]).toFixed(1)},${H - B} Z`;
  const months = []; let seen = '';
  for (const r of pts) { const m = r.date.slice(0, 7); if (m !== seen) { seen = m; months.push(r); } }
  const every = Math.ceil(months.length / Math.max(3, Math.floor(W / LABEL_PX)));
  if (months.length > 1 && x(months[1]) - x(months[0]) < LABEL_MIN_GAP) months.shift();   // a partial first month would sit on top of the next label
  // the first label and every January carry the year: a 1Y range would otherwise show two Octobers with nothing between them
  const labels = months.filter((_, i) => i % every === 0).map((r, i) => `<text class="ax" x="${x(r).toFixed(1)}" y="${H - 4}" text-anchor="${x(r) < EDGE ? 'start' : 'middle'}">${day(r.date).toLocaleDateString(undefined, i === 0 || r.date.slice(5, 7) === '01' ? { month: 'short', year: 'numeric' } : { month: 'short' })}</text>`).join('');
  const last = pts[pts.length - 1], k = (v) => { const a = Math.abs(v), s = v < 0 ? '-' : ''; return s + (a >= 1e9 ? `$${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `$${(a / 1e6).toFixed(2)}M` : `$${Math.round(a / 1e3)}k`); };
  svg.innerHTML = `<defs><linearGradient id="nw-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--mint-700)" stop-opacity=".22"/><stop offset="1" style="stop-color:var(--mint-700)" stop-opacity="0"/></linearGradient></defs>
    ${[0, 1, 2].map(i => `<line class="grid" x1="${L}" x2="${W - R}" y1="${(T + i * ROW).toFixed(1)}" y2="${(T + i * ROW).toFixed(1)}"/>`).join('')}
    <path class="fill" d="${area}"/>${nwShow.a ? `<polyline class="line as" points="${path('a')}"/>` : ''}${nwShow.l ? `<polyline class="line li" points="${path('l')}"/>` : ''}<polyline class="line" points="${line}"/>
    <circle class="dot" cx="${x(last).toFixed(1)}" cy="${y(last.v).toFixed(1)}" r="3"/>
    <text class="ax" x="${(W - R).toFixed(1)}" y="${Math.max(10, y(last.v) - 8).toFixed(1)}" text-anchor="end">${k(last.v)}</text>${labels}
    <g class="hover" visibility="hidden"><line x1="0" x2="0" y1="${T}" y2="${H - B}"/>${NW_SERIES.filter(([key]) => key === 'v' || nwShow[key]).map(([key]) => `<circle class="${key}" r="3.5"/>`).join('')}</g>`;
  nwHover = { pts, x, y, W };
}
// hover: the nearest day to the pointer (the cursor need not sit on the line), its date and every curve shown at it
const NW_SERIES = [['v', 'net worth', 'k-nw'], ['a', 'assets', 'k-as'], ['l', 'liabilities', 'k-li']];
const day = (d) => new Date(d + 'T12:00');
let nwHover = null;
function nwPoint(e) {
  const svg = $('nw-svg'), tip = $('nw-tip'), g = svg.querySelector('.hover');
  if (!nwHover || !g) return;
  const { pts, x, y, W } = nwHover, box = svg.getBoundingClientRect(), px = (e.clientX - box.left) * W / box.width;
  const r = pts.reduce((a, b) => Math.abs(x(b) - px) < Math.abs(x(a) - px) ? b : a);
  const keys = NW_SERIES.filter(([key]) => (key === 'v' || nwShow[key]) && Number.isFinite(r[key]));
  g.setAttribute('visibility', 'visible'); g.setAttribute('transform', `translate(${x(r).toFixed(1)},0)`);
  for (const c of g.querySelectorAll('circle')) {   // a curve with no figure that day drops its dot
    const v = r[c.getAttribute('class')]; c.setAttribute('visibility', Number.isFinite(v) ? 'inherit' : 'hidden'); if (Number.isFinite(v)) c.setAttribute('cy', y(v).toFixed(1));
  }
  tip.innerHTML = `<b>${day(r.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</b>` +
    keys.map(([key, name, cls]) => `<div>${keys.length > 1 ? `<i class="${cls}"></i>${name} ` : ''}<span class="num">${money(r[key])}</span></div>`).join('');
  tip.hidden = false;
  const at = tip.offsetParent.getBoundingClientRect(), x0 = box.left - at.left, left = x0 + x(r) * box.width / W;   // an svg has no offsetLeft
  tip.style.left = `${Math.max(x0, left + 12 + tip.offsetWidth > x0 + box.width ? left - tip.offsetWidth - 12 : left + 12)}px`;   // flips left of the rule near the right edge
  tip.style.top = `${box.top - at.top + 4}px`;
}
$('nw-svg').addEventListener('pointermove', nwPoint);
$('nw-svg').addEventListener('pointerdown', nwPoint);   // a tap on a phone
const nwUnhover = () => { $('nw-tip').hidden = true; $('nw-svg').querySelector('.hover')?.setAttribute('visibility', 'hidden'); };
$('nw-svg').addEventListener('pointerleave', (e) => { if (e.pointerType !== 'touch') nwUnhover(); });   // a lifted finger leaves too: a tap's tip stays
document.addEventListener('pointerdown', (e) => { if (!$('nw-svg').contains(e.target)) nwUnhover(); });   // until a tap anywhere else
$('nw-range').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-m]'); if (!b) return;
  nwMonths = Number(b.dataset.m);
  for (const o of $('nw-range').querySelectorAll('button')) o.setAttribute('aria-pressed', o === b);
  drawNetWorth();
});

// budgets: overall bar, then the four that matter most; Manage shows them all with editable limits
const CAT_ICONS = [[/grocer/, 'shopping-cart'], [/restaurant|dining|coffee|food/, 'fork-knife'], [/gas|fuel|transport|transit|parking/, 'gas-pump'],
  [/auto|car|vehicle/, 'car'], [/subscri|stream|entertain|tv|music/, 'television'], [/travel|flight|hotel|airline/, 'airplane'],
  [/rent|mortgage|home|util|electric|water|internet/, 'house'], [/medic|health|pharm|dental|doctor/, 'first-aid'], [/pet/, 'dog'],
  [/gift|charit|donat/, 'heart'], [/shop|merch|general|clothing|amazon/, 'storefront']];
const catIcon = (b) => (CAT_ICONS.find(([re]) => re.test(`${b.name} ${(b.covers || []).join(' ')} ${b.category || ''}`.toLowerCase())) || [null, 'tag'])[1];
// what a budget counts, when its name doesn't say it all: several categories, a merchant, a fixed bill
const covers = (b) => (b.covers || []).length > 1 || (b.merchants || []).length || b.fixed ? [...(b.covers || []), ...(b.fixed ? ['fixed'] : [])].join(' · ') : '';
const barState = (b) => b.spent > b.monthly_limit ? 'over' : (b.pace > b.monthly_limit || b.spent / b.monthly_limit > 0.8) ? 'warn' : '';
const pctOf = (b) => Math.min(100, Math.round(100 * b.spent / b.monthly_limit));

function renderBudgets(s) {
  const bs = s.budgets, body = $('budgets-body');
  $('budgets-empty').style.display = bs.length || managing ? 'none' : '';
  $('budgets-toggle').textContent = managing ? 'Done' : bs.length ? 'Manage' : 'Add';
  if (!bs.length) { body.innerHTML = ''; return; }
  const spent = bs.reduce((a, b) => a + b.spent, 0), limit = bs.reduce((a, b) => a + b.monthly_limit, 0);
  const pct = limit ? Math.min(100, Math.round(100 * spent / limit)) : 0, left = limit - spent;
  const now = new Date(), day = bs[0].day || now.getDate(), togo = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate() - day;
  const note = left < 0 ? `${money(-left)} over with ${togo} days to go` : `${pct}% used, ${money(left)} left with ${togo} days to go`;
  const top = [...bs].sort((a, b) => (b.spent / b.monthly_limit) - (a.spent / a.monthly_limit)).slice(0, 4);
  body.innerHTML = `<div class="overall"><div class="row"><span>Overall</span><span class="amt num">${money(spent)} <small>/ ${money(limit)}</small></span></div>` +
    `<div class="bar"><div class="fill ${left < 0 ? 'over' : pct > 80 ? 'warn' : ''}" style="width:${pct}%"></div></div><div class="note ${left < 0 ? 'over' : ''}">${note}</div></div>` +
    `<h3>${bs.length > 4 ? 'Top categories' : 'Categories'}</h3><div class="cats">` + top.map(b =>
      `<div class="cat"><span class="ic">${icon(catIcon(b))}</span><span class="name">${esc(b.name)}</span><span class="amt num">${money(b.spent)} / ${money(b.monthly_limit)}</span>` +
      `<div class="bar"><div class="fill ${barState(b)}" style="width:${pctOf(b)}%"></div></div>${covers(b) ? `<span class="covers" title="${esc(covers(b))}">${esc(covers(b))}</span>` : ''}</div>`).join('') + '</div>';
}

let managing = false;
function renderManage(s) {
  const m = $('budgets-manage');
  m.innerHTML = s.budgets.map(b =>
    `<div class="erow" data-name="${esc(b.name)}"><span class="name"><span class="ic">${icon(catIcon(b))}</span><span class="lbl">${esc(b.name)} <span class="pace num">${money(b.spent)} so far</span>` +
    `${covers(b) ? `<span class="covers" title="${esc(covers(b))}">${esc(covers(b))}</span>` : ''}</span></span>` +
    `<input class="num" value="${b.monthly_limit}" inputmode="decimal" aria-label="Monthly limit for ${esc(b.name)}"><button class="del" aria-label="Remove ${esc(b.name)}" data-del="${esc(b.name)}">${icon('trash')}</button></div>`).join('') +
    `<div class="erow add"><input id="b-new-name" placeholder="New category, e.g. travel"><input id="b-new-amt" class="num" placeholder="$ per month" inputmode="decimal"><span></span></div>` +
    `<div class="eactions"><span class="err" id="b-err"></span><button class="quiet" id="b-cancel">Cancel</button><button class="primary" id="b-save">Save</button></div>`;
  wireEditor(m, {
    remove: (n) => ({ ask: `Remove the ${n} budget?`, url: `/api/budget/${encodeURIComponent(n)}` }),
    changes: () => [...m.querySelectorAll('.erow[data-name]')].map(row => {
      const b = summary.budgets.find(x => x.name === row.dataset.name), v = amount(row.querySelector('input').value);
      return v !== b.monthly_limit ? ['/api/budget', { name: b.name, amount: v }] : null;
    }).filter(Boolean),
    added: () => $('b-new-name').value.trim() ? ['/api/budget', { name: $('b-new-name').value.trim(), amount: amount($('b-new-amt').value) }] : null,
    err: 'b-err', cancel: 'b-cancel', save: 'b-save', rerender: () => renderManage(summary), close: () => toggleManage(false),
  });
}

// One wiring for both edit panels: delete with a confirm, then Save posts every changed row and the new row. If a
// write fails, what did land is refreshed and shown with the message, so a half-applied save is never invisible.
function wireEditor(el, o) {
  el.querySelectorAll('[data-del]').forEach(btn => btn.addEventListener('click', async () => {
    const { ask, url } = o.remove(btn.dataset.del);
    if (!confirm(ask)) return;
    const r = await api('DELETE', url);
    await loadSummary(); o.rerender();
    if (!r.ok) $(o.err).textContent = r.error;
  }));
  $(o.cancel).addEventListener('click', o.close);
  $(o.save).addEventListener('click', async () => {
    $(o.err).textContent = '';
    const writes = o.changes(); const add = o.added(); if (add) writes.push(add);
    for (const [url, body] of writes) {
      const r = await api('POST', url, body);
      if (!r.ok) { await loadSummary(); o.rerender(); $(o.err).textContent = r.error; return; }
    }
    await loadSummary(); o.close();
  });
}
function toggleManage(on) {
  managing = on;
  $('budgets-manage').style.display = on ? 'block' : 'none';
  $('budgets-body').style.display = on ? 'none' : '';
  renderBudgets(summary);
  if (on) { renderManage(summary); $('b-new-name').focus(); }
}
$('budgets-toggle').addEventListener('click', () => { if (summary) toggleManage(!managing); });

// properties: the ledger's Property row opens a panel under the summary
let propsOpen = false;
function renderProps(s) {
  const p = $('prop-pop');
  p.innerHTML = `<h3>Properties<span class="hint">what you'd sell them for; counted into net worth until you change it</span></h3>` +
    s.properties.map(pr => `<div class="erow" data-name="${esc(pr.name)}"><span class="name"><span class="ic">${icon('house')}</span>${esc(pr.name)}</span>` +
      `<input class="num" value="${pr.value}" inputmode="decimal" aria-label="Value of ${esc(pr.name)}"><button class="del" aria-label="Remove ${esc(pr.name)}" data-del="${esc(pr.name)}">${icon('trash')}</button></div>`).join('') +
    `<div class="erow add"><input id="p-new-name" placeholder="Name, e.g. House on Elm St"><input id="p-new-amt" class="num" placeholder="$ value" inputmode="decimal"><span></span></div>` +
    `<div class="eactions"><span class="err" id="p-err"></span><button class="quiet" id="p-cancel">Cancel</button><button class="primary" id="p-save">Save</button></div>`;
  wireEditor(p, {
    remove: (n) => ({ ask: `Remove ${n}?`, url: `/api/property/${encodeURIComponent(n)}` }),
    changes: () => [...p.querySelectorAll('.erow[data-name]')].map(row => {
      const pr = summary.properties.find(x => x.name === row.dataset.name), v = row.querySelector('input').value;
      return amount(v) !== pr.value ? ['/api/property', { name: pr.name, value: v }] : null;
    }).filter(Boolean),
    added: () => $('p-new-name').value.trim() ? ['/api/property', { name: $('p-new-name').value.trim(), value: $('p-new-amt').value }] : null,
    err: 'p-err', cancel: 'p-cancel', save: 'p-save', rerender: () => renderProps(summary), close: () => toggleProps(false),
  });
}
function toggleProps(on) { propsOpen = on; $('prop-pop').style.display = on ? '' : 'none'; if (on) { renderProps(summary); $('p-new-name').focus(); } }
$('row-prop').addEventListener('click', () => { if (summary) toggleProps(!propsOpen); });
$('row-prop').addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && summary) { e.preventDefault(); toggleProps(!propsOpen); } });

// import: a bank's CSV export into a manual account (one Plaid can't reach); the server hands the file to `finnamon import`
const ON_BOX = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);   // the browser window opens where the server runs
const KINDS = { checking: 'Checking', savings: 'Savings', credit: 'Credit card', loan: 'Loan', investment: 'Investment' };   // labels for finnamon.imports.KINDS; a parity test holds web/server.js KINDS to it
const MAX_NAME = 80;   // web/server.js MAX_NAME, which /api/account enforces
function renderImport(s) {
  const p = $('import-pop'), manual = (s.accounts || []).filter(a => a.source === 'manual' && !a.mirror_of);
  const last = (a) => a.last_synced_at ? ` (last import ${when(a.last_synced_at)})` : ' (nothing imported yet)';
  p.innerHTML = `<h3>Import a CSV<span class="hint">a bank's transaction export, for an account Plaid can't reach</span></h3>` + (manual.length
    ? `<div class="erow add imp"><select id="i-acct" aria-label="Account">${manual.map(a => `<option value="${esc(a.name)}" data-bank="${esc(a.institution)}">${esc(a.institution)} · ${esc(a.name)}${esc(last(a))}</option>`).join('')}</select>` +
      `<input type="file" id="i-file" accept=".csv,text/csv" aria-label="CSV file">` +
      `<input id="i-bal" class="num" placeholder="Balance now (optional)" inputmode="decimal" aria-label="Balance now" title="Some exports carry no running balance; the figure here becomes the account's balance in net worth">` +
      `<button class="del" id="i-del" aria-label="Remove the chosen account" title="Remove the chosen account and its transactions">${icon('trash')}</button></div>` +
      `<div id="i-prev" class="prev" hidden aria-live="polite"></div>` +
      `<div class="eactions"><span class="err" id="i-err"></span><button class="quiet" id="i-fetch"${ON_BOX ? '' : ' disabled'} title="${ON_BOX ? 'Opens a browser window on this machine for you to log in; the file is downloaded and imported for you' : 'The browser window would open on the Finnamon box, not here: upload the CSV, or on this computer run finnamon import --browser <bank> --to ' + esc(location.origin)}">Fetch by AI</button><button class="primary" id="i-go" disabled>Import</button></div>` +
      (ON_BOX ? '' : `<p class="muted">Fetch by AI works on the Finnamon box itself; from here, upload the CSV, or run <code>finnamon import --browser &lt;bank&gt; --to ${esc(location.origin)}</code> on this computer, with <code>FINNAMON_WEB_TOKEN</code> set to what <code>finnamon web token</code> prints on the box.</p>`)
    : `<p class="muted">No manual account yet — add one below.</p>`) +
    `<h3 class="sep">Add a manual account<span class="hint">one per account; add as many as the bank has (HSBC Checking, HSBC Savings, HSBC Credit Card)</span></h3>` +
    `<div class="erow add acct"><input id="a-name" placeholder="Account name, e.g. HSBC Checking" aria-label="Account name" maxlength="${MAX_NAME}">` +
    `<input id="a-inst" placeholder="Bank, e.g. HSBC" aria-label="Bank" maxlength="${MAX_NAME}">` +
    `<select id="a-type" aria-label="Type">${Object.entries(KINDS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>` +
    `<div class="eactions"><span class="err" id="a-err"></span><button class="quiet" id="i-cancel">${manual.length ? 'Cancel' : 'Close'}</button><button class="${manual.length ? 'quiet' : 'primary'}" id="a-add">Add account</button></div>`;   // with no account yet, adding one is the only thing to do here
  $('i-cancel').addEventListener('click', () => toggleImport(false));
  $('a-add').addEventListener('click', async (e) => {
    const btn = e.currentTarget;   // held: currentTarget is null once the dispatch that raised it has finished, and this handler awaits
    const n = $('a-name').value.trim(), inst = $('a-inst').value.trim() || n.split(/\s+/)[0];   // \s+, as the CLI's own split() fallback does
    if (!n) { $('a-err').textContent = 'The account needs a name.'; return; }
    $('a-err').textContent = ''; btn.disabled = true;   // a second click while the first is in flight comes back as "already exists", which reads like a failure
    const r = await api('POST', '/api/account', { name: n, institution: inst, type: $('a-type').value });
    if (!r.ok) { $('a-err').textContent = r.error; btn.disabled = false; return; }
    const added = r.name || n;   // account add collapses inner whitespace, and keeps the bank an existing item already had
    toast(`Added ${added} at ${r.institution || inst}`);
    try { await loadSummary(); } catch {}   // the row is written; a refresh that throws must not leave the button dead
    renderImport(summary);
    const sel = $('i-acct'), there = sel && [...sel.options].some(o => o.value === added);
    if (there) { sel.value = added; sel.focus(); }
    else $('a-err').textContent = `${added} was added, but the page could not refresh. Reload to import into it.`;   // an unmatched value selects nothing, and the next import would post no account at all
  });
  $('i-del')?.addEventListener('click', async () => {   // a typo'd account is otherwise stuck in net worth until its whole bank goes
    const a = manual.find(x => x.name === $('i-acct').value);
    if (!a || !confirm(`Remove ${a.institution} · ${a.name} and its ${plural(a.transactions ?? 0, 'transaction')}? This can't be undone.`)) return;
    const r = await api('DELETE', `/api/account/${encodeURIComponent(a.account_id)}`);   // the id: a Plaid account may share the name
    if (!r.ok) { $('i-err').textContent = r.error; return; }
    toast(`Removed ${a.name}`);
    try { await loadSummary(); } catch { $('i-err').textContent = `${a.name} was removed, but the page could not refresh. Reload to see the list.`; return; }
    renderImport(summary);
  });
  $('i-fetch')?.addEventListener('click', async () => {   // its own Claude session in the panel's Import tab, never the household's
    const bank = $('i-acct').selectedOptions[0]?.dataset.bank;
    const r = await api('POST', '/api/import/browser', { bank });
    if (!r.ok) { $('i-err').textContent = r.error; return; }
    toggleImport(false); showView('import'); openIntercom(true);
  });
  // Before anything is saved: a dry run of the chosen file (the CLI's --dry-run, with --flip when the toggle is on) shows the first rows
  // and whether each reads as spending or money in. A file that parses to no rows is an error here, never a success.
  const post = (extra, text) => fetch(`/api/import?account=${encodeURIComponent($('i-acct').value)}${extra}`, { method: 'POST', headers: { 'content-type': 'text/csv' }, body: text });
  let flip = false, ticket = 0;
  const preview = async () => {
    const f = $('i-file').files[0], box = $('i-prev'), go = $('i-go'), n = ++ticket;   // the newest request wins
    go.disabled = true; $('i-err').textContent = ''; box.hidden = !f;
    if (!f) return;
    box.textContent = 'Reading the file…';
    let r = null, out = {};
    try { r = await post(`&dry_run=1${flip ? '&flip=1' : ''}`, await f.text()); out = await r.json(); } catch {}
    if (n !== ticket) return;
    if (r?.status === 401) return lock();
    if (!r || !r.ok) { box.hidden = true; $('i-err').textContent = out.error || 'The page lost the server; reload to see where things stand.'; return; }
    if (!out.rows) { box.hidden = true; $('i-err').textContent = `No rows could be read${out.skipped ? ` (${plural(out.skipped, 'row')} skipped)` : ''}. Dates need to look like 2026-09-30 or 09/30/2026, and amounts like 1234.56 (not 1.234,56).`; return; }
    box.innerHTML = `<p class="muted">${plural(out.rows, 'row')} read${out.skipped ? `, ${plural(out.skipped, 'row')} unreadable` : ''}; the first ${out.sample.length}:</p>` +
      `<table class="prev-t"><tbody>${out.sample.map(x => `<tr><td>${esc(x.date)}</td><td class="pn">${esc(x.name)}</td><td class="num">${money(Math.abs(x.amount))}</td><td class="${x.reads_as === 'spending' ? 'out' : 'in'}">${x.reads_as}</td></tr>`).join('')}</tbody></table>` +
      `<label class="flip"><input type="checkbox" id="i-flip"${flip ? ' checked' : ''}> Flip signs <span class="hint">if purchases show as money in (some card exports list spending as positive)</span></label>`;
    $('i-flip').addEventListener('change', (e) => { flip = e.target.checked; preview(); });
    go.disabled = false;
  };
  $('i-file').addEventListener('change', () => { flip = false; preview(); });
  $('i-acct').addEventListener('change', preview);
  $('i-go')?.addEventListener('click', async () => {
    const f = $('i-file').files[0];
    if (!f) { $('i-err').textContent = 'Pick the CSV file first.'; return; }
    $('i-err').textContent = ''; toast('Importing', { kind: 'busy' });
    let r = null, out = {};
    if ($('i-bal').value.trim() && !Number.isFinite(amount($('i-bal').value))) { toast(''); $('i-err').textContent = 'The balance has to be a number, like 1200 or 1,234.56.'; return; }
    const bal = $('i-bal').value.trim() ? `&balance=${encodeURIComponent(amount($('i-bal').value))}` : '';
    try { r = await post(`${bal}${flip ? '&flip=1' : ''}`, await f.text()); out = await r.json(); } catch {}
    if (r?.status === 401) return lock();   // a rotated key is not an outage
    if (!r || !r.ok) { toast(''); $('i-err').textContent = out.error || 'The page lost the server; reload to see where things stand.'; return; }
    if (!out.rows) { toast(''); $('i-err').textContent = 'No rows could be read, so nothing was imported.'; return; }
    toast(`${plural(out.added ?? 0, 'new transaction')}${out.already ? `, ${out.already} already there` : ''}${out.skipped ? `, ${plural(out.skipped, 'row')} unreadable` : ''}${out.balance != null ? `, balance ${money(out.balance)}` : ''}`);
    toggleImport(false); loadSummary();
  });
}
function toggleImport(on) {
  const p = $('import-pop');
  if (!on) return p.close();
  renderImport(summary); p.showModal(); ($('i-acct') || $('a-name')).focus();
}
// A click on the backdrop closes it, as Escape does. The target test alone is not enough: .card gives the dialog its own
// padding, and a click on that ring reports the dialog as the target too, so the panel would close on its own margin and
// throw away the chosen file and the half-typed form. Compare against the box instead.
$('import-pop').addEventListener('click', (e) => {
  if (e.target !== e.currentTarget) return;
  const b = e.currentTarget.getBoundingClientRect();
  if (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom) toggleImport(false);
});
// Escape, the backdrop and Cancel all end here. showModal() would restore focus to import-btn, but that button sits in a
// details the menu closed on the way in, so the keyboard would land on the body: send it to the control, as the menu does.
$('import-pop').addEventListener('close', () => $('add-menu').querySelector('summary').focus());
$('import-btn').addEventListener('click', () => { if (summary) toggleImport(!$('import-pop').open); });

// Which banks are connected and what sits under each. The rows use the header's own filter, so the list the person opens
// has as many rows as the count they clicked; a mirror is another account seen twice, not an account of its own.
// Everything below is display only: names, kinds and masks are tidied for reading, never written back.
const KIND = { checking: 'Checking', savings: 'Savings', 'cash management': 'Cash management', 'money market': 'Money market', cd: 'CD', hsa: 'HSA',
  prepaid: 'Prepaid', paypal: 'PayPal', 'credit card': 'Credit card', ira: 'IRA', roth: 'Roth IRA', 'roth 401k': 'Roth 401(k)', '401k': '401(k)',
  '403b': '403(b)', '457b': '457(b)', '529': '529 plan', brokerage: 'Brokerage', 'non-taxable brokerage account': 'Brokerage', pension: 'Pension',
  mortgage: 'Mortgage', 'home equity': 'Home equity', 'line of credit': 'Line of credit', student: 'Student loan', auto: 'Auto loan', loan: 'Loan' };
const TYPE = { depository: 'Bank account', credit: 'Credit', loan: 'Loan', investment: 'Investment', brokerage: 'Investment' };
const cap = (x) => x ? x[0].toUpperCase() + x.slice(1) : '';
const own = (o, k) => Object.hasOwn(o, k) ? o[k] : '';   // a subtype named 'constructor' is a word, not Object's member
const kindOf = (a) => { const sub = String(a.subtype || '').toLowerCase(); return own(KIND, sub) || (sub !== 'other' && cap(sub)) || own(TYPE, a.type) || cap(a.type || ''); };
// ponytail: ALL-CAPS words of 4+ letters become Title case, shorter ones (CMA, IRA, HSA) and the acronyms below stay; add to ACRO as banks show up
const ACRO = new Set(['USAA', 'AMEX', 'HSBC', 'BBVA', 'NFCU', 'HELOC', 'FDIC', 'SIPC', 'UTMA', 'UGMA']);
const tidy = (n) => /\p{Ll}/u.test(n) ? n : n.replace(/\p{Lu}{4,}/gu, w => ACRO.has(w) ? w : w[0] + w.slice(1).toLowerCase());
function acctName(a) {   // "IRA portfolio - 5321" with mask 5321 reads the number twice: drop it from the name
  const n = tidy(String(a.name || '').trim()), m = /^\d+$/.test(a.mask || '') ? a.mask : '';   // digits only: a lettered mask is never cut out of a name
  const cut = m ? n.replace(new RegExp(`[\\s(#*.·–/:-]+x?${m}\\)?$`, 'i'), '').trim() : n;
  return cut || n;
}
const DEBT = new Set(['credit', 'loan']);   // finnamon/investments.py LIABILITY_TYPES: shown owed, as the Debt figure is
// A bank's heading total: assets minus debts, whole dollars per account as the rows show them; no balance yet counts nothing.
const bankTotal = (accts) => accts.reduce((t, a) => { const b = Math.round(Number(a.balance)) || 0; return t + (a.balance == null ? 0 : DEBT.has(a.type) ? -b : b); }, 0);
// Which banks the person left open, per browser. Everything starts closed; storage that throws or holds junk reads as all closed.
const OPEN_KEY = 'finnamon.banksOpen';
const loadOpen = (st = globalThis.localStorage) => { try { const v = JSON.parse(st.getItem(OPEN_KEY)); return new Set(Array.isArray(v) ? v.filter(x => typeof x === 'string') : []); } catch { return new Set(); } };
const saveOpen = (set, st = globalThis.localStorage) => { try { st.setItem(OPEN_KEY, JSON.stringify([...set])); } catch { /* private mode: the state lasts until the dialog closes */ } };
// Whose is it: only with 2+ household members. One owner across a bank's accounts is named on the heading; mixed owners, on each row.
const ownerOf = (a) => a.owner || '';
function ownerPlan(accts, members) {
  const names = new Map((members || []).map(m => [m.owner, m.display_name || m.owner]));
  if (names.size < 2) return { head: '', rows: false, name: () => '' };
  const name = (a) => names.get(ownerOf(a)) || ownerOf(a), set = new Set(accts.map(ownerOf).filter(Boolean));
  return set.size === 1 ? { head: name(accts.find(ownerOf)), rows: false, name } : { head: '', rows: set.size > 1, name };
}
// Group by: Bank (the default), Type or Owner. Type needs no members; Owner needs 2+, else it is not offered.
const VIEW_KEY = 'finnamon.banksView';
const viewsFor = (members) => (members || []).length > 1 ? ['bank', 'type', 'owner'] : ['bank', 'type'];
const loadView = (members, st = globalThis.localStorage) => { try { const v = st.getItem(VIEW_KEY); return viewsFor(members).includes(v) ? v : 'bank'; } catch { return 'bank'; } };
const saveView = (v, st = globalThis.localStorage) => { try { st.setItem(VIEW_KEY, v); } catch { /* private mode: the choice lasts until the dialog closes */ } };
// Type groups: assets first, then debts; within each, the order below, kinds it does not know after, then by name.
const KIND_ORDER = ['Checking', 'Savings', 'Cash management', 'Money market', 'CD', 'HSA', 'Prepaid', 'PayPal', 'Bank account', 'Brokerage', 'IRA', 'Roth IRA',
  '401(k)', 'Roth 401(k)', '403(b)', '457(b)', '529 plan', 'Pension', 'Investment', 'Credit card', 'Credit', 'Mortgage', 'Home equity', 'Line of credit', 'Student loan', 'Auto loan', 'Loan'];
function groupAccounts(accts, view, members) {   // [{ key, name, rows }] for the Type and Owner views; empty groups are left out
  const by = new Map(), push = (k, n, a, extra = {}) => { if (!by.has(k)) by.set(k, { key: k, name: n, rows: [], ...extra }); by.get(k).rows.push(a); };
  if (view === 'owner') {
    const names = new Map((members || []).map(m => [m.owner, m.display_name || m.owner]));
    for (const a of accts) names.has(ownerOf(a)) ? push(ownerOf(a), names.get(ownerOf(a)), a) : push('', 'Joint/unassigned', a);
    const order = [...names.keys(), ''];
    return order.filter(k => by.has(k)).map(k => by.get(k));
  }
  for (const a of accts) { const k = kindOf(a) || 'Other'; push(k, k, a, { debt: DEBT.has(a.type) }); }
  const rank = (g) => { const i = KIND_ORDER.indexOf(g.name); return i < 0 ? KIND_ORDER.length : i; };
  return [...by.values()].sort((x, y) => (x.debt - y.debt) || (rank(x) - rank(y)) || x.name.localeCompare(y.name));
}
const STALE_MS = 24 * 3600e3;   // web/server.js health(): a bank not synced in a day is overdue
function renderBanks(s) {
  const members = s.owners || [], view = loadView(members);
  const accts = (s.accounts || []).filter(a => !a.mirror_of), banks = new Map(), warn = new Map();   // warn: bank -> what is wrong with its sync, for rows outside the Bank view
  for (const it of s.status.items || []) banks.set(it.institution, [...(banks.get(it.institution) || []), it]);   // two logins at one bank share a heading
  const everyone = ownerPlan(accts, members);
  const row = (a, plan) => {
    const kind = kindOf(a), debt = DEBT.has(a.type), b = a.balance == null ? NaN : Math.round(Number(a.balance)) || 0;   // whole dollars, as money() shows them, so $0.40 owed is not a red -$0
    const bal = !Number.isFinite(b) ? `<span class="bal none" title="No balance yet">—</span>`
      : `<span class="bal num${debt && b > 0 ? ' owed' : ''}"${debt && b > 0 ? ' title="Owed"' : ''}>${esc(money(debt ? -b || 0 : b))}</span>`;
    const chip = plan.rows && plan.name(a) ? ` <span class="own">${esc(plan.name(a))}</span>` : '';
    const w = view !== 'bank' && warn.get(a.institution), mask = a.mask ? `<span class="num">···${esc(a.mask)}</span>` : '';
    const sub = view === 'type' ? esc(a.institution || '') + chip : view === 'owner' ? esc([a.institution, kind].filter(Boolean).join(' · ')) : esc(kind);
    return `<li><span class="nm">${esc(acctName(a))}${view === 'bank' ? chip : ''}${w ? ` <span class="rwarn" title="${esc(w)}" role="img" aria-label="${esc(`${a.institution} ${w}`)}">${icon('warning-circle')}</span>` : ''}</span>${bal}<span class="kd">${sub}${mask ? `${sub ? ' · ' : ''}${mask}` : ''}</span></li>`;
  };
  const list = (rows, plan) => rows.length ? `<ul class="accts">${rows.map(a => row(a, plan)).join('')}</ul>` : `<p class="muted">No accounts yet.</p>`;
  const open = loadOpen(), k = (key) => view === 'bank' ? key : `${view}:${key}`;   // Bank keeps its old keys, so what people left open survives
  const sec = (key, name, note, rows) => {   // a native <details>: keyboard, screen reader and aria-expanded come with it
    const tot = bankTotal(rows), n = rows.length, plan = view === 'bank' ? ownerPlan(rows, members) : view === 'type' ? { rows: everyone.rows || members.length > 1, name: everyone.name } : { head: '', rows: false, name: () => '' };
    return `<details class="bank" data-key="${esc(k(key))}"${open.has(k(key)) ? ' open' : ''}><summary><svg class="ic chev" aria-hidden="true" viewBox="0 0 16 16"><path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>` +
      `<span class="bn">${esc(name)}${plan.head ? ` <span class="own">${esc(plan.head)}</span>` : ''}</span><span class="cnt">${esc(plural(n, 'account'))}</span><span class="bal num${tot < 0 ? ' owed' : ''}">${esc(money(tot))}</span>${note ? `<span class="note">${note}</span>` : ''}</summary>${list(rows, plan)}</details>`;
  };
  let html = '';
  for (const [inst, its] of banks) {
    const bad = its.find(i => i.status && i.status !== 'good');   // as health() reads it, so this heading and the pill agree
    const linked = its.filter(i => !String(i.item_id).startsWith('manual:')), times = (xs) => xs.map(i => i.last_synced_at).filter(Boolean).sort();
    const synced = times(its).pop(), oldest = times(linked)[0];   // a bank's staleness is its stalest login's: a fresh one must not hide it
    const stale = oldest && Date.now() - new Date(String(oldest).replace(' ', 'T')).getTime() > STALE_MS;
    const text = bad ? (/LOGIN/.test(bad.status) ? 'needs a new login' : `not syncing (${bad.status})`) : stale ? `synced ${when(oldest)}` : '';
    if (text) warn.set(inst, text);
    const note = bad ? `<span class="tag warn">${icon('warning-circle')} ${esc(text)}</span>${RELOGIN.test(bad.status) && !String(bad.item_id).startsWith('manual:') ? reconnectBtn(bad.item_id) : ''}`
      : !linked.length ? `<span class="hint">added by hand${synced ? ` · imported ${esc(when(synced))}` : ''}</span>`
      : times(linked).length < linked.length ? `<span class="hint">not synced yet</span>`   // just linked: the first sync is on its way
      : stale ? `<span class="tag warn" title="Finnamon syncs every bank at least daily">${icon('warning-circle')} ${esc(text)}</span>`
      : `<span class="hint">synced ${esc(when(synced))}</span>`;
    if (view === 'bank') html += sec(its[0].institution_id || inst, inst, note, accts.filter(a => a.institution === inst));
  }
  if (view !== 'bank') for (const g of groupAccounts(accts, view, members)) html += sec(g.key || 'none', g.name, '', g.rows);
  else {
    const loose = accts.filter(a => !banks.has(a.institution));   // an account whose bank has no item row: still counted, so still listed
    if (loose.length) html += sec('none', 'No bank connection', '', loose);
  }
  const label = { bank: 'Bank', type: 'Type', owner: 'Owner' };
  const seg = `<div class="ctl"><div class="seg" role="radiogroup" aria-label="Group by"><span class="segl" aria-hidden="true">Group by</span>` +
    viewsFor(members).map(v => `<label><input type="radio" name="gb" value="${v}"${v === view ? ' checked' : ''}><span>${label[v]}</span></label>`).join('') + `</div>` +
    (html.includes('<details') ? `<button class="quiet" id="banks-all" type="button"></button>` : '') + `</div>`;
  $('banks-pop').innerHTML = `<div class="phead"><h2>Accounts</h2><button class="x" id="banks-x" type="button" aria-label="Close">${icon('x')}</button></div>` + seg + html;
  $('banks-pop').querySelectorAll('input[name=gb]').forEach(r => r.addEventListener('change', () => { saveView(r.value); renderBanks(s); $('banks-pop').querySelector('input[name=gb]:checked').focus(); }));
  const all = () => [...$('banks-pop').querySelectorAll('details.bank')], sync = () => { const b = $('banks-all'); if (b) b.textContent = all().every(d => d.open) ? 'Collapse all' : 'Expand all'; };
  const store = () => { const mine = new Set(all().map(d => d.dataset.key)); saveOpen(new Set([...loadOpen()].filter(x => !mine.has(x)).concat(all().filter(d => d.open).map(d => d.dataset.key)))); };   // other views' groups stay as they were
  all().forEach(d => d.addEventListener('toggle', () => { store(); sync(); }));
  $('banks-all')?.addEventListener('click', () => { const to = !all().every(d => d.open); all().forEach(d => { d.open = to; }); });
  $('banks-x').addEventListener('click', () => $('banks-pop').close());
  sync();
}
$('banks-btn').addEventListener('click', () => { if (summary) { renderBanks(summary); $('banks-pop').showModal(); } });
$('banks-pop').addEventListener('click', (e) => {   // the backdrop closes it; the box test is import-pop's, for the same padding ring
  if (e.target !== e.currentTarget) return;
  const b = e.currentTarget.getBoundingClientRect();
  if (e.clientX < b.left || e.clientX > b.right || e.clientY < b.top || e.clientY > b.bottom) e.currentTarget.close();
});
$('banks-pop').addEventListener('close', () => $('banks-btn').focus());

async function api(method, url, body) {
  let r;
  try { r = await fetch(url, { method, headers: body ? { 'content-type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined }); }
  catch { return { ok: false, error: 'The page lost the server; reload to see where things stand.' }; }   // the server restarted or the link dropped
  if (r.status === 401) { lock(); return { ok: false, error: '' }; }   // one explanation, from lock(); an empty error hides nothing over it
  let out = {}; try { out = await r.json(); } catch {}
  return { ok: r.ok, error: out.error || (r.ok ? '' : `error ${r.status}`), ...out };
}

// alerts: a.text is notify.render() output: our own HTML, bank strings already escaped
const KIND_ICONS = [[/sync|login/, 'plug', 'hot'], [/duplicate/, 'copy', ''], [/recurring/, 'arrows-clockwise', ''], [/balance/, 'drop', 'warn'],
  [/budget|pace/, 'target', 'warn'], [/merchant/, 'storefront', ''], [/large|unusual|spike|anomal/, 'warning-circle', 'hot']];
const EMOJI_LEAD = /^\s*(?:[☀-➿]️?|[\uD83C-\uDBFF][\uDC00-\uDFFF]️?)+\s*/;
const reconnectBtn = (item) => `<button class="link" type="button" data-reconnect="${esc(item)}">Reconnect</button>`;
function renderAlerts(s) {
  // The server sends only what Finnamon told the household or is about to (`finnamon alerts --sent`); triage's own notes
  // on the rest stay with the assistant. Open ones come in s.alerts (`--open`), the latest resolved in s.resolved.
  const rows = (s.alerts || []).slice(0, 8), done = s.resolved || [];
  const linked = (s.status.items || []).length > 0;   // "Nothing to flag" with no bank would say all is well while nothing is watched
  $('alerts-empty').style.display = rows.length || !linked ? 'none' : '';
  $('alerts-nolink').style.display = rows.length || linked ? 'none' : '';
  const item = (a, tail) => {
    const [, ic, tone] = KIND_ICONS.find(([re]) => re.test(a.kind || '')) || [null, 'bell', ''];
    const text = String(a.page_text || a.text || esc(a.kind)).replace(EMOJI_LEAD, '');   // the icon replaces the emoji; page_text: the button, not "reply fix X"
    return `<li><span class="ic ${tone}">${icon(ic)}</span><span><div>${text}</div>${tail}`;   // tail closes the span
  };
  const act = (a, what, label) => `<button class="link" data-alert="${Number(a.id)}" data-act="${what}">${label}</button>`;
  $('alerts-body').innerHTML = rows.map(a => {
    const tag = a.sent_at ? ['new', 'open'] : ['warn', 'unsent'];
    const first = a.reconnect ? reconnectBtn(a.reconnect) : act(a, 'normal', 'It’s normal');   // a re-login is fixed, not normal
    return item(a, `<div class="when">${esc(when(a.sent_at || a.created_at))}</div><div class="acts">${first}${act(a, 'dismiss', 'Dismiss')}</div></span>`) +
      `<span class="tag ${tag[0]}">${tag[1]}</span></li>`;
  }).join('');
  const how = (a) => a.resolution === 'normal' ? `It’s normal${a.suppression_id ? ` (rule ${Number(a.suppression_id)})` : ''}` : a.resolution === 'dismissed' ? 'Dismissed' : a.resolution === 'reconnected' ? 'Reconnected' : 'Resolved';
  $('alerts-resolved').hidden = !done.length;
  $('alerts-resolved-n').textContent = done.length;
  $('alerts-resolved-body').innerHTML = done.map(a => item(a, `<div class="when">${esc(how(a))} · ${esc(when(a.resolved_at))}</div></span>`) +
    `<span>${a.resolution === 'normal' || a.resolution === 'dismissed' ? act(a, 'undo', 'Undo') : ''}</span></li>`).join('');   // the rest Finnamon resolved (a bank reconnected), or came before the page could undo
}
const ALERT_DONE = { normal: 'Marked normal: alerts like it stay quiet.', dismiss: 'Dismissed: the next one still alerts.', undo: 'Undone: the alert is open again.' };
// Reconnect: Plaid's re-login page for one bank, in a new tab. The tab opens on the click itself (one opened after an await
// is a blocked popup to Safari) and goes to Plaid once the server has the link; the daemon syncs the bank within a minute of the login.
const RELOGIN = /^(ITEM_LOGIN_REQUIRED|PENDING_EXPIRATION|PENDING_DISCONNECT)$/;   // notify.RELOGIN exactly (tests/test_relogin.py holds the two together)
async function reconnect(b) {
  const w = window.open('', '_blank');
  if (w) w.opener = null;
  b.disabled = true;
  const r = await api('POST', `/api/item/${encodeURIComponent(b.dataset.reconnect)}/reconnect`);
  b.disabled = false;
  if (r.ok && /^https:\/\//.test(r.url || '')) {   // Plaid's address, nothing else
    if (w) w.location.href = r.url; else location.assign(r.url);   // a blocked popup: this tab goes, and the page is one Back away
    toast('Log in on Plaid’s page. Finnamon picks the bank back up within a minute of the login.');
  } else {
    w?.close();
    if (r.error || r.ok) toast(r.error || 'Plaid sent no login page; try again in a minute.', { kind: 'warn' });
  }
}
document.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-reconnect]'); if (!b) return;
  e.preventDefault();   // in Accounts it sits in a <summary>: the click is the button's, not the section's toggle
  reconnect(b);
});
$('alerts').addEventListener('click', async (e) => {
  const b = e.target.closest('button[data-alert]'); if (!b) return;
  for (const x of $('alerts').querySelectorAll('button[data-alert]')) x.disabled = true;   // one at a time: the list is redrawn after
  const r = await api('POST', `/api/alert/${b.dataset.alert}/${b.dataset.act}`);
  if (r.ok) toast((r.next && b.dataset.act === 'normal' ? r.next : ALERT_DONE[b.dataset.act]) + (r.removed_rule ? ` Rule ${r.removed_rule.id} removed.` : ''));
  else if (r.error) toast(r.error, { kind: 'warn' });
  await loadSummary().catch(() => {});
  for (const x of $('alerts').querySelectorAll('button[data-alert]')) x.disabled = false;   // a failed refresh must not leave them dead
});

// ---- charts ---------------------------------------------------------------------------------------------------------

let board = [];   // charts/current.json: the specs on the board, one panel each, in order
const chartId = (s) => s.usermeta?.finnamon?.id || s.usermeta?.finnamon?.chart || 'chart';   // a pre-list file's entry has no id (finnamon.charts.board)
const EMPTY_BOARD = $('chart-panel').innerHTML;
function chartOpts() {
  const muted = token('--muted'), grid = token('--line'), mint = token('--mint'), cinnamon = token('--cinnamon'), gold = token('--gold'), track = token('--track');
  // mode: never infer full Vega from $schema; the loader refuses every fetch and link: the spec is all there is (the CLI checks both too)
  const nothing = () => Promise.reject(new Error('charts load nothing from outside the spec'));
  // ast + expressionInterpreter: expressions (Vega-Lite's own and any in a spec) are walked as data, never compiled with new Function;
  // the page's CSP has no 'unsafe-eval', so without these no chart would draw at all
  return { actions: false, renderer: 'svg', mode: 'vega-lite', ast: true, expr: vega.expressionInterpreter, loader: Object.assign(vega.loader(), { load: nothing, sanitize: nothing }),
    config: { background: 'transparent', font: 'Geist, -apple-system, system-ui, sans-serif',
    title: { anchor: 'start', fontSize: 13, fontWeight: 600, color: muted, offset: 12 },
    axis: { labelColor: muted, titleColor: muted, gridColor: grid, domainColor: grid, tickColor: grid, tickCount: 6 }, view: { stroke: null },
    // a legend never takes plot width (#97): a row above the plot; a spec's own legend.orient still wins over this config
    legend: { orient: 'top', direction: 'horizontal', labelColor: muted, titleColor: muted, labelLimit: 120 },
    // budgets: limit (track) then spent (mint); in/out: in (mint) then out (cinnamon); balances: one per account
    range: { category: [track, mint, cinnamon, gold, token('--chart-4'), token('--cinnamon-dark'), token('--chart-6'), token('--chart-7')] }, mark: { color: mint } } };
}
// when the chart's values were computed (#76); a spec with only inline values has no query to rerun
function asOf(s) {
  const fm = s.usermeta?.finnamon || {};
  if (fm.static || !(fm.source || fm.chart)) return fm.as_of ? `static, drawn ${esc(when(fm.as_of))}` : 'static';
  return fm.as_of ? `as of ${esc(when(fm.as_of))}` : '';
}
// ---- table panels: a board entry with `table` is rows, not Vega-Lite (finnamon.charts.custom_table). Pure, so
// web/test/tables.test.js lifts this block; every cell goes through esc(), since merchants and memos are bank strings.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function shortDate(v, year = new Date().getFullYear()) {   // "2026-09-28" → "Sep 28"; another year keeps it: "Sep 28, 2025"
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v ?? ''));
  if (!m || !MONTHS[m[2] - 1]) return String(v ?? '');
  return `${MONTHS[m[2] - 1]} ${+m[3]}` + (+m[1] === year ? '' : `, ${m[1]}`);
}
// money is tx_now's sign: positive is money out, shown as -$52.10 in the debt colour; money in is $6,800.00
function tableCell(v, format, year) {
  if (v == null || v === '') return { text: '' };
  const n = typeof v === 'number' ? v : Number(v);
  if (format === 'money' && Number.isFinite(n)) return { text: (n > 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }), out: n > 0 };
  if (format === 'number' && Number.isFinite(n)) return { text: n.toLocaleString('en-US') };
  if (format === 'date') return { text: shortDate(v, year) };
  return { text: String(v) };
}
function tableNote(t, shown) {
  if (!shown) return 'no rows match';
  const of = (t.more ? 'over ' : '') + Number(t.total || 0).toLocaleString('en-US');
  return shown < t.total || t.more ? `showing ${shown} of ${of}` : `${shown} row${shown === 1 ? '' : 's'}`;
}
const isTable = (s) => Array.isArray(s.table?.columns);
function tableHtml(s, year) {   // a hand-edited entry of the wrong shape draws nothing rather than throwing out every panel
  const obj = (x) => x && typeof x === 'object';
  const cols = s.table.columns.filter(obj), rows = (Array.isArray(s.data?.values) ? s.data.values : []).filter(obj);
  const head = cols.map(c => `<th scope="col"${c.align === 'right' ? ' class="r"' : ''}>${esc(c.label)}</th>`).join('');
  const body = rows.map(r => '<tr>' + cols.map(c => {
    const x = tableCell(r[c.field], c.format, year), cls = [c.align === 'right' && 'r', x.out && 'out'].filter(Boolean).join(' ');
    return `<td${cls ? ` class="${cls}"` : ''}${c.format === 'text' ? ` title="${esc(x.text)}"` : ''}>${esc(x.text)}</td>`;
  }).join('') + '</tr>').join('');
  return `<div class="tbl-title">${esc(s.title)}</div><div class="tbl-wrap" tabindex="0" role="region" aria-label="${esc(s.title)}">`
    + `<table class="tbl num"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
// ---- end table panels

let views = [], drawn = 0;
async function drawChart() {
  const gen = ++drawn;
  views.splice(0).forEach(v => v.finalize());   // each embed makes a View with its own listeners; a redraw must end every one
  const panel = $('chart-panel');
  panel.classList.toggle('board', board.length > 0);
  if (!board.length) { panel.innerHTML = EMPTY_BOARD; return; }
  panel.innerHTML = board.map(s => `<figure class="panel"><button class="x" data-remove="${esc(chartId(s))}" aria-label="Remove this ${isTable(s) ? 'table' : 'chart'}" title="Remove">${icon('x')}</button>`
    + (isTable(s) ? `<div class="vis">${tableHtml(s)}</div><figcaption class="asof">${tableNote(s.table, (Array.isArray(s.data?.values) ? s.data.values : []).length)} · ${asOf(s)}</figcaption>`
      : `<div class="vis"></div><figcaption class="asof">${asOf(s)}</figcaption>`) + '</figure>').join('');
  const cells = panel.querySelectorAll('.vis');
  // usermeta.embedOptions would win over chartOpts() (actions, sourceHeader, patch: script on this origin): the CLI drops it, and so does the page
  const made = await Promise.all(board.map((s, i) => isTable(s) ? null : vegaEmbed(cells[i], { ...s, usermeta: { finnamon: s.usermeta?.finnamon } }, chartOpts()).then(r => r.view,
    e => { cells[i].innerHTML = `<div class="muted">This chart did not draw: ${esc(e.message)}</div>`; return null; })));
  if (gen === drawn) views = made.filter(Boolean);
  else made.forEach(v => v?.finalize());   // a newer draw replaced these cells while they embedded
}
async function loadChart() {
  const r = await fetch('/api/chart').catch(() => null);
  if (!r?.ok) return;
  board = await r.json();
  // a lit chip means its preset is on the board; a merchant_history for one merchant is not a chip's
  document.querySelectorAll('[data-chart]').forEach(b => {
    const on = board.some(s => s.usermeta?.finnamon?.chart === b.dataset.chart);
    b.classList.toggle('active', on); b.setAttribute('aria-pressed', on);
  });
  drawChart();
}
async function chartRequest(method, url, body) { const r = await api(method, url, body); if (!r.ok) toast(r.error, { kind: 'warn' }); }
let chartResizeTimer = null, mainWidth = 0;   // "width: container" is measured once at embed time
// Watch <main>, not the window: the docked intercom narrows it without any window resize. Height changes (a redraw) are ignored.
new ResizeObserver(() => { const w = document.querySelector('main').clientWidth; if (w === mainWidth) return; mainWidth = w;
  clearTimeout(chartResizeTimer); chartResizeTimer = setTimeout(() => { drawNetWorth(); drawChart(); }, 150); }).observe(document.querySelector('main'));
document.querySelectorAll('[data-chart]').forEach(b => b.addEventListener('click', () => {
  const on = board.find(s => s.usermeta?.finnamon?.chart === b.dataset.chart);
  chartRequest(...(on ? ['DELETE', `/api/chart/${encodeURIComponent(chartId(on))}`] : ['POST', '/api/chart', { name: b.dataset.chart }]));
}));
$('chart-panel').addEventListener('click', (e) => {
  const x = e.target.closest('[data-remove]');
  if (x) chartRequest('DELETE', `/api/chart/${encodeURIComponent(x.dataset.remove)}`);
});

// ---- toast: link progress and errors, out of the layout's way -------------------------------------------------------

let toastTimer;
const TOAST_ICONS = { ok: icon('check-circle', 'fill'), warn: icon('warning-circle'), busy: icon('arrows-clockwise') };
function toast(text, { kind = 'ok', sticky = kind !== 'ok', force = false } = {}) {   // good news fades; warnings and work in progress stay until replaced or dismissed
  if (locked && !force && !text.startsWith('Finnamon has locked')) return;   // the lock message stays; a caller's empty or generic error must not wipe it (the x still may)
  const t = $('toast'), row = $('toast-row'); clearTimeout(toastTimer);
  if (!text) { row.hidden = true; return; }
  t.className = 'toast ' + kind; t.setAttribute('role', kind === 'warn' ? 'alert' : 'status');
  $('toast-ic').innerHTML = TOAST_ICONS[kind]; $('toast-text').textContent = '';
  row.hidden = true; void t.offsetWidth; row.hidden = false;   // restart the slide-in when one toast replaces another
  requestAnimationFrame(() => { $('toast-text').textContent = text; });   // written into a rendered live region, so it is announced
  if (!sticky) toastTimer = setTimeout(() => { row.hidden = true; }, 6000);
}
$('toast-x').addEventListener('click', () => toast('', { force: true }));

// ---- Plaid Link in the page ----------------------------------------------------------------------------------------

// With several household members the bank must be filed under the right one: ask first. Resolves to the owner name,
// null for a one-member household (no question, the CLI's default applies) and undefined when the person backs out.
function askOwner() {
  const members = Array.isArray(summary?.owners) ? summary.owners : [];
  if (members.length < 2) return Promise.resolve(null);
  const p = $('owner-pop');
  p.innerHTML = `<h3>Whose bank is this?</h3><form method="dialog">` + members.map((m, i) =>
    `<label class="owner-opt"><input type="radio" name="owner" value="${esc(m.owner)}"${i ? '' : ' checked'}> ${esc(m.display_name || m.owner)}</label>`).join('') +
    `<div class="row"><button class="primary" value="ok">Continue</button> <button value="" formnovalidate>Cancel</button></div></form>`;
  return new Promise(resolve => {
    p.addEventListener('close', () => resolve(p.returnValue === 'ok' ? p.querySelector('input:checked').value : undefined), { once: true });
    p.returnValue = ''; p.showModal();
  });
}
const ownerName = (o) => { const m = (summary?.owners || []).find?.(x => x.owner === o); return m ? (m.display_name || m.owner) : o; };

async function linkBank({ receivedRedirectUri } = {}) {
  let token = sessionStorage.getItem('plaid_link_token');
  if (receivedRedirectUri && !token) token = (await api('GET', '/api/link/token')).link_token;   // the server keeps the owner with it   // the bank sent us back in a new tab (no sessionStorage)
  if (receivedRedirectUri && !token) { toast('That link session has expired; start again from Link account.', { kind: 'warn' }); history.replaceState(null, '', location.pathname); return; }
  if (typeof Plaid === 'undefined') { toast('Plaid did not load; reload the page and try again.', { kind: 'warn' }); return; }
  if (!receivedRedirectUri) {
    const owner = await askOwner();
    if (owner === undefined) return;
    const r = await api('POST', '/api/link/token', owner ? { owner } : undefined);
    if (!r.ok) { toast(r.error, { kind: 'warn' }); return; }
    token = r.link_token;
    sessionStorage.setItem('plaid_link_token', token);   // an OAuth bank leaves and comes back to this page
  }
  toast('');
  const handler = Plaid.create({
    token, receivedRedirectUri,
    onSuccess: async (public_token) => {
      sessionStorage.removeItem('plaid_link_token');
      toast('Linking, the first sync can take a minute', { kind: 'busy' });
      const out = await api('POST', '/api/link/finish', { public_token });
      const n = out.sync?.accounts, tx = out.sync?.transactions;
      if (out.ok) toast(`Linked ${out.institution}${out.owner ? ` for ${ownerName(out.owner)}` : ''}: ${plural(n, 'account')}, ${plural(tx, 'transaction')}` +
        (out.mirror_candidates?.length ? '. Some look like accounts already linked; see the chat.' : ''), { sticky: !!out.mirror_candidates?.length });
      else toast(out.error, { kind: 'warn' });
      loadSummary();   // whatever the reply, the bank may have landed
    },
    onExit: (err) => { if (err) toast(err.display_message || err.error_message || 'Link closed.', { kind: 'warn' }); sessionStorage.removeItem('plaid_link_token'); },
  });
  handler.open();
  if (receivedRedirectUri) history.replaceState(null, '', location.pathname);   // a reload must not hand Plaid the same return twice
}
$('link-btn').addEventListener('click', () => linkBank());
$('nolink-btn').addEventListener('click', () => linkBank());
// the Add account menu closes when a way is picked, on Escape, or on a click anywhere else
$('add-menu').querySelectorAll('button').forEach(b => b.addEventListener('click', () => $('add-menu').removeAttribute('open')));
document.addEventListener('click', (e) => { if (!$('add-menu').contains(e.target)) $('add-menu').removeAttribute('open'); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('add-menu').open) { $('add-menu').removeAttribute('open'); $('add-menu').querySelector('summary').focus(); } });   // focus returns to the control, not to the page body
if (location.search.includes('oauth_state_id=')) linkBank({ receivedRedirectUri: location.href });

// ---- intercom -----------------------------------------------------------------------------------------------------

// The numbers come from this machine; the terminal comes from a CDN. If the CDN is unreachable the page still works.
// Two sessions share the panel: the household's (Finnamon tab), and a browser import's while one runs (Import tab).
function makeTerm(id) {
  const t = new Terminal({ fontFamily: "'Geist Mono', 'SF Mono', Menlo, monospace", fontSize: 13, cursorBlink: true, minimumContrastRatio: 4.5, allowProposedApi: true, theme: termTheme() });
  const f = new FitAddon.FitAddon();
  t.loadAddon(f); t.loadAddon(new WebLinksAddon.WebLinksAddon());
  t.open($(id));
  f.fit();   // the closed panel has layout (opacity 0, not display none), so the replay lands at the right width
  return { term: t, fit: f };
}
let term = null, fit = null, iterm = null, ifit = null;
try {
  ({ term, fit } = makeTerm('terminal'));
  ({ term: iterm, fit: ifit } = makeTerm('terminal-import'));
} catch (e) {
  console.warn(`intercom unavailable: ${e.message}`);
  $('intercom-btn').disabled = true; $('intercom-btn').title = 'The terminal library did not load';
}

let ws, wsReady = false, opened = false;
function connect() {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { wsReady = true; talkConnected(true); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'hello') {
      term?.reset(); term?.write(msg.replay); setState(msg.state);
      if (msg.import) { importStopping = importTyped = false; iterm?.reset(); iterm?.write(msg.import.replay); setImportState(msg.import.state); }
      else if (importState && importState !== 'DOWN') setImportState('DOWN');   // it ended while the socket was down
      sendSize();
    }
    else if (msg.type === 'output') (msg.session === 'import' ? iterm : term)?.write(msg.data);
    else if (msg.type === 'state') msg.session === 'import' ? setImportState(msg.state) : setState(msg.state);
    else if (msg.type === 'chart') loadChart();
    else if (msg.type === 'talk') talkMessage(msg);
  };
  // Before retrying, ask a cheap route whether the page is still let in: a key rotated while the socket was up would otherwise
  // be refused every two seconds, and logged each time, until the next summary poll noticed.
  ws.onclose = (ev) => { wsReady = false; setState('DOWN'); talkConnected(false); if (ev.code === 4001) return lock();   // the server closed it: the key changed
    fetch('/api/chart').then(r => { if (r.status === 401) lock(); }).catch(() => {}).finally(() => { if (!locked) setTimeout(connect, 2000); }); };
}
const send = (o) => { if (wsReady && !locked) ws.send(JSON.stringify(o)); };
term?.onData((d) => { if (d.includes('\r')) awaitingReply = true; send({ type: 'input', data: d }); });
iterm?.onData((d) => { if (importState === 'DOWN' && !importTyped) { importTyped = true; showView('import'); } send({ type: 'input', session: 'import', data: d }); });   // keys into an ended import go nowhere: say so
function sendSize() {   // the visible terminal is measured; the hidden one has no size to fit
  if (!term) return;
  // Twice: xterm measures its cell before Geist Mono lands and only re-measures inside a resize, so the first fit can
  // propose rows of the fallback font's height and push the input line out of sight; the second uses the real cell.
  if (view === 'import') { ifit.fit(); ifit.fit(); send({ type: 'resize', session: 'import', cols: iterm.cols, rows: iterm.rows }); }
  else { fit.fit(); fit.fit(); send({ type: 'resize', cols: term.cols, rows: term.rows }); }
}

let view = 'chat', importState = null, importStopping = false, importTyped = false;
const STATE_TEXT = { WORKING: 'thinking…', WAITING: 'ready', QUESTION: 'asking you something', DOWN: 'offline', STARTING: 'starting…' };
function showView(v) {
  view = v;
  $('terminal').hidden = v !== 'chat'; $('terminal-import').hidden = v !== 'import';
  document.querySelectorAll('.intercom-head .tab').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === v)));
  $('intercom-state').textContent = v === 'import' ? (importState === 'DOWN' ? (importTyped ? 'ended; Import CSV starts another' : importStopping ? 'import stopped' : 'import ended') : importStopping ? 'stopping…' : STATE_TEXT[importState] || importState || '') : STATE_TEXT[lastState] || '';
  $('import-stop').hidden = v !== 'import' || !importState || importState === 'DOWN';
  $('import-stop').disabled = importStopping;
  if (opened) requestAnimationFrame(() => { sendSize(); (v === 'import' ? iterm : term)?.focus(); });
}
function setImportState(s) {
  const was = importState; importState = s;
  if (s !== 'DOWN' && was === 'DOWN') importStopping = importTyped = false;   // a new Fetch by AI
  document.querySelector('.tab[data-view="import"]').hidden = false;
  if (view === 'import') showView('import');
  if (s === 'DOWN' && was && was !== 'DOWN') { loadSummary(); iterm?.write('\r\n[the numbers above are refreshed; this transcript goes when the page reloads]\r\n'); }   // the import wrote rows; the numbers follow
}
document.querySelectorAll('.intercom-head .tab').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
// The only other way out is typing /exit into the terminal; the server kills the process after a short grace if claude lingers.
$('import-stop').addEventListener('click', () => { if (!wsReady) return; importStopping = true; send({ type: 'stop', session: 'import' }); showView('import'); });

// The dot lights when a reply to something you typed lands while the panel is closed. The TUI redraws on its own
// (footer, spinner), so "output arrived" or "went quiet" alone would cry wolf.
let awaitingReply = false, lastState = null;
function setState(s) {
  if (locked) return;   // the socket lock() closed must not relabel the page 'offline'
  if (view === 'chat') $('intercom-state').textContent = STATE_TEXT[s] || s;
  $('intercom-btn').dataset.state = s;
  if ((s === 'WAITING' || s === 'QUESTION') && lastState === 'WORKING' && awaitingReply) { if (!opened) markUnread(); awaitingReply = false; }
  lastState = s;
}
function markUnread() { $('intercom-btn').classList.add('unread'); }
// Wide windows dock the panel and push the page left (body.docked, style.css); narrow ones keep the overlay. Open/closed is remembered per browser.
const wide = matchMedia('(min-width: 901px)');
// The docked panel starts under the header's visible bottom edge: measured, so it follows a wrapped header and the page scrolling the header away.
const setHeadH = () => document.documentElement.style.setProperty('--head-h', Math.max(0, document.querySelector('header').getBoundingClientRect().bottom) + 'px');
setHeadH(); addEventListener('scroll', setHeadH, { passive: true }); new ResizeObserver(setHeadH).observe(document.querySelector('header'));
const remember = (open) => { try { localStorage.setItem('intercom-open', open ? '1' : '0'); } catch {} };
function openIntercom(open, focus = true) {
  const was = opened; opened = open; $('intercom').classList.toggle('closed', !open); document.body.classList.toggle('docked', open);
  if (open) { $('intercom-btn').classList.remove('unread'); requestAnimationFrame(() => { sendSize(); if (focus) (view === 'import' ? iterm : term)?.focus(); }); }
  else if (was && $('intercom').contains(document.activeElement)) $('intercom-btn').focus();   // the terminal's textarea is about to be hidden
}
const toggleIntercom = (open) => { openIntercom(open); remember(open); };
$('intercom-btn').addEventListener('click', () => toggleIntercom(!opened));
$('intercom-close').addEventListener('click', () => toggleIntercom(false));
wide.addEventListener('change', () => { if (opened) sendSize(); });
window.addEventListener('resize', () => { if (opened) sendSize(); });
// The call bar and its notes come and go inside the panel without any window resize: the terminal refits to what is left.
// Growing, the panel's scroll area is clamped first and xterm takes that for the owner scrolling up: the input line sat
// out of sight until a key was pressed. A layout change brings it back to the bottom.
const refit = new ResizeObserver(() => { if (opened) { sendSize(); (view === 'import' ? iterm : term).scrollToBottom(); } });
refit.observe($('terminal')); refit.observe($('terminal-import'));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && opened) toggleIntercom(false); });

loadSummary(); loadChart(); if (term) connect(); else setState('DOWN');
initTalk();
try { if (!locked && term && wide.matches && localStorage.getItem('intercom-open') === '1') openIntercom(true, false); } catch {}
loadUpdate();
setInterval(() => { loadSummary(); loadUpdate(); }, 5 * 60 * 1000);   // the server checks for an update at most every 30 min
