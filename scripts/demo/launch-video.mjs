// Records the launch video (finnamon-demo.mp4, 1080x1920) and the README's teaser (docs/finnamon-teaser.gif) from
// `finnamon demo`: a made-up household, never ~/.finnamon. The approach is agent-007's scripts/demo/phone-call.mjs.
//
// The dashboard is the real one (web/server.js over the demo home), on a phone, with its clock pinned to Sep 20 2026
// by libfaketime so the data says what the recorded lines say ($248 on dining, a $52.18 Shell charge on the 17th and
// the 18th; tests/test_demo.py holds finnamon/demo.py to them). The intercom's `claude` is scripts/demo/bin/claude, a
// stand-in that answers the questions below (the chart one by really running `finnamon chart`) through the same transcript a real session writes, so Talk's whole
// path runs: the question typed into the session, the progress line, the answer read off the transcript and played.
// The voices are generated (DEMO_MEDIA: ask1.wav, ask2.wav, ask3.wav for the owner, answer1.wav, answer2.wav for Finnamon): the
// owner's lines are fed to the page as its speech recognition's result, and Finnamon's are what the page fetches when
// it speaks each answer, so each plays where the app would say it. The progress line is shown, not spoken: there is
// no voice for it. Every frame says the data is made up, the video sped up and the voices AI-generated.
//
// Needs macOS, Google Chrome, ffmpeg, libfaketime (brew install libfaketime), web/node_modules (cd web && npm install)
// and playwright:
//   npm i --no-save --prefix scripts/demo playwright
//   node scripts/demo/launch-video.mjs [out-dir]     (default: a temp folder)
// Then copy out-dir/finnamon-teaser.gif to docs/ and put out-dir/finnamon-demo.mp4 where the README links it.
import { execFileSync, spawnSync } from 'child_process';
import { mkdirSync, mkdtempSync, writeFileSync, statSync, existsSync } from 'fs';
import { tmpdir, homedir } from 'os';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { chromium } from 'playwright';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const MEDIA = process.env.DEMO_MEDIA || join(homedir(), '.agent-007', 'demo-media', 'finnamon');
const REPO_URL = process.env.REPO_URL || '';   // the end card's link line; a placeholder until the public repo exists (#113)
const TODAY = '2026-09-20 09:41:00';
const NOTE = 'Demo household, made-up data · sped up · voices are AI-generated';
const ALERT = '⚠️ <b>Possible duplicate:</b> Shell $52.18 on Chase Checking …4821, charged Sep 17 and again Sep 18. Same merchant, same amount. Reply "it\'s normal" if you filled up twice.';
const DINING = `SELECT strftime('%Y-%m',date) ym, round(sum(amount)) spent FROM tx_now WHERE flow IN ('expense','refund') AND pending=0 AND category_primary='FOOD_AND_DRINK' AND date>=date('now','-5 months') GROUP BY ym ORDER BY ym`;
const DINING_SPEC = { title: 'Dining by month', mark: { type: 'bar', tooltip: true }, encoding: { x: { field: 'ym', type: 'ordinal', title: null }, y: { field: 'spent', type: 'quantitative', title: '$' } } };
const TURNS = [
  { ask: 'ask1.wav', answer: 'answer1.wav', said: 'How much did we spend on dining this month?',
    match: 'how much', step: 'Checking dining against its budget', command: 'finnamon budget',
    reply: 'You\'ve spent $248 on dining so far, on pace for about $370 against your $350 budget.' },
  // The chart beat: no spoken answer (the owner watches it appear); the stand-in really runs `finnamon chart`, so the board is the real one.
  { ask: 'ask3.wav', answer: null, said: 'Show dining by month', match: 'show dining', step: 'Drawing dining by month', chart: true,
    command: 'finnamon chart --id dining-monthly --sql "SELECT … FROM tx_now …" --spec-json \'{"title": "Dining by month", …}\'',
    exec: ['chart', '--id', 'dining-monthly', '--sql', DINING, '--spec-json', JSON.stringify(DINING_SPEC)], today: TODAY, reply: 'Done. Dining by month is on your board.' },
  { ask: 'ask2.wav', answer: 'answer2.wav', said: 'Anything I should worry about this week?',
    match: 'worry', step: 'Looking at this week\'s alerts', command: 'finnamon alerts --days 7',
    reply: 'Just one thing. Shell charged $52.18 twice, on the 17th and the 18th. Want me to flag it?' },
];

const out = process.argv[2] || mkdtempSync(join(tmpdir(), 'finnamon-video-'));
mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const ff = (...a) => execFileSync('ffmpeg', ['-y', '-loglevel', 'error', ...a], { stdio: 'inherit' });
const duration = (f) => Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f], { encoding: 'utf8' }));
for (const t of TURNS) for (const k of ['ask', 'answer']) {
  if (!t[k]) continue;
  t[k] = join(MEDIA, t[k]);
  if (!existsSync(t[k])) throw new Error(`missing ${t[k]} (DEMO_MEDIA)`);
}

// --- The demo household and its dashboard, on Sep 20 ---

const home = join(out, 'home');
const env = { ...process.env, FINNAMON_DEMO_HOME: home, CLAUDE_BIN: join(REPO, 'scripts', 'demo', 'bin', 'claude'),
              CLAUDE_CONFIG_DIR: join(out, 'claude-config'), DEMO_TURNS: JSON.stringify(TURNS.map(({ match, step, command, reply, exec, today }) => ({ match, step, command, reply, exec, today }))) };
mkdirSync(env.CLAUDE_CONFIG_DIR, { recursive: true });   // where `finnamon demo` marks the assistant directory trusted
for (const k of ['FINNAMON_HOME', 'FINNAMON_ASSISTANT', 'CLAUDECODE', 'FINNAMON_FROM_CLAUDE']) delete env[k];
const finnamon = (...a) => spawnSync('faketime', ['-f', `@${TODAY}`, 'python3', '-m', 'finnamon.cli', 'demo', ...a], { cwd: REPO, env, encoding: 'utf8' });
const started = finnamon('--reset', '--print');
const url = started.stdout.match(/the demo dashboard: (\S+)/)?.[1];
if (!url) throw new Error(`finnamon demo did not start:\n${started.stdout}\n${started.stderr}`);
process.on('exit', () => finnamon('--stop'));
console.log(`[demo] ${url.replace(/token=.*/, 'token=…')} in ${home}`);

// --- The phone ---

const phone = { width: 390, height: 844 };
const browser = await chromium.launch({ channel: 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
const ctx = await browser.newContext({ viewport: phone, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: 'light' });
await ctx.addInitScript(({ offset }) => {
  // The page's clock agrees with the server's: Sep 20.
  const D = Date;
  window.Date = class extends D {
    constructor(...a) { super(...(a.length ? a : [D.now() + offset])); }
    static now() { return D.now() + offset; }
  };
  // Talk without whisper.cpp uses the browser's recognition: here, the script. Its one-time notice is taken as read.
  try { localStorage.setItem('finnamon-talk-browser-stt', '1'); } catch {}
  class Recognition { start() { window.__demoRec = this; } abort() { if (window.__demoRec === this) window.__demoRec = null; } stop() { this.abort(); } }
  window.SpeechRecognition = window.webkitSpeechRecognition = Recognition;
  window.__demoHear = (text) => {
    const r = window.__demoRec;
    if (!r) return false;
    const result = [{ transcript: text }];
    result.isFinal = true;
    r.onresult({ resultIndex: 0, results: [result] });
    return true;
  };
  // The progress line the server sends while the session works.
  const WS = window.WebSocket;
  window.WebSocket = class extends WS {
    constructor(...a) {
      super(...a);
      this.addEventListener('message', (e) => {
        try { const m = JSON.parse(e.data); if (m.type === 'talk' && m.status) window.__demoEvent({ status: m.status }); } catch {}
      });
    }
  };
  // When each answer plays: the blobs fetched from /api/talk/audio (the silent unlock clip is a few hundred bytes).
  const answers = new Set();
  const create = URL.createObjectURL;
  URL.createObjectURL = (b) => { const u = create.call(URL, b); if (b?.size > 10000) answers.add(u); return u; };
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    if (answers.has(this.src)) {
      this.addEventListener('playing', () => {
        window.__demoEvent({ playing: true });
        for (const e of ['ended', 'pause', 'error']) this.addEventListener(e, () => window.__demoEvent({ ended: true }), { once: true });
      }, { once: true });
    }
    return play.call(this);
  };
}, { offset: new Date(TODAY.replace(' ', 'T')).getTime() - Date.now() });

const events = [];   // { at, status } | { at, playing } | { at, ended }, at on this process's clock
await ctx.exposeFunction('__demoEvent', (e) => { events.push({ at: Date.now(), ...e }); });
const page = await ctx.newPage();
// Each answer is the generated take, in the order the page asks for them; the progress line has no voice.
const served = [];
let silent = false;
await page.route('**/api/talk/audio/**', async (route) => {
  const [id, index] = new URL(route.request().url()).pathname.split('/').slice(-2);
  if (index === 'status') return route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"no voice for progress in the demo"}' });
  if (silent) return route.fulfill({ status: 404, body: '' });   // the chart turn's reply is not voiced
  if (!served.includes(id)) served.push(id);
  const take = TURNS.filter(t => t.answer)[served.indexOf(id)]?.answer;
  if (!take) return route.fulfill({ status: 404, body: '' });
  await route.fulfill({ status: 200, contentType: 'audio/wav', headers: { 'X-Pieces': '1' }, path: take });
});

// A touch, since video has no cursor: a ring where the finger lands. The call bar's note would name the browser's
// speech recognition, which the scratch box falls back to for want of whisper.cpp and a real setup doesn't use.
const STYLE = `#talk-notice, #talk-note-x { display: none !important }
  .demo-tap { position: fixed; z-index: 99999; pointer-events: none; width: 44px; height: 44px; margin: -22px 0 0 -22px;
    border-radius: 50%; background: #b5651d33; border: 2px solid #b5651dcc; animation: demo-tap .6s ease-out forwards }
  @keyframes demo-tap { from { transform: scale(.4); opacity: 1 } to { transform: scale(1.3); opacity: 0 } }`;
await page.goto(url);
await page.addStyleTag({ content: STYLE });
await page.waitForLoadState('networkidle');
await sleep(1500);

// Filmed as fast as it can be screenshot, at 2x; the frames' times make the video (ffmpeg's concat with durations).
const frames = [];
let rolling = true;
const filming = (async () => {
  const cdp = await ctx.newCDPSession(page);
  mkdirSync(join(out, 'frames'), { recursive: true });
  while (rolling) {
    const at = Date.now();
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'jpeg', quality: 88 });
    const file = join(out, 'frames', `${String(frames.length).padStart(5, '0')}.jpg`);
    writeFileSync(file, Buffer.from(data, 'base64'));
    frames.push({ at, file });
  }
})();
while (!frames.length) await sleep(20);

async function tap(locator, hold = 400) {
  await locator.waitFor();
  const box = await locator.boundingBox();
  await page.evaluate(({ x, y }) => {
    const ring = Object.assign(document.createElement('div'), { className: 'demo-tap' });
    Object.assign(ring.style, { left: `${x}px`, top: `${y}px` });
    document.body.appendChild(ring);
    setTimeout(() => ring.remove(), 700);
  }, { x: box.x + box.width / 2, y: box.y + box.height / 2 });
  await sleep(150);
  await locator.click();
  await sleep(hold);
}
const scrollTo = (y, ms) => page.evaluate(({ y, ms }) => new Promise(done => {
  const from = scrollY, to = Math.min(y, document.documentElement.scrollHeight - innerHeight), t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / ms), e = k < .5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
    scrollTo(0, from + (to - from) * e);
    k < 1 ? requestAnimationFrame(step) : done();
  };
  requestAnimationFrame(step);
}), { y, ms });
const callState = (s) => page.waitForFunction((s) => document.getElementById('talk-bar')?.dataset.state === s, s, { timeout: 60_000 });
async function next(kind, after) {
  for (let i = 0; i < 1200; i++, await sleep(50)) {
    const e = events.find(e => e[kind] && e.at >= after);
    if (e) return e.at;
  }
  throw new Error(`no ${kind} event; ${JSON.stringify(events.map(e => ({ ...e, at: e.at - events[0].at })))} served=${served}`);
}

const captions = [];   // { at, until, who?, text, kind }
const sounds = [];     // { at, file, ms }
const t0 = frames[0].at;

// 1. The alert, as it lands on the owner's phone.
captions.push({ at: t0, until: t0 + 5500, text: 'It warns you when something\'s off.', kind: 'scene' });
await sleep(5500);
// 2. The dashboard: net worth, budgets, alerts, charts.
captions.push({ at: Date.now(), until: Date.now() + 6500, text: 'Net worth, budgets, alerts and charts, from your own banks.', kind: 'scene' });
await scrollTo(2600, 4200);
await sleep(600);
await scrollTo(0, 1400);
await sleep(300);
// 3. The assistant, and the phone button.
await tap(page.locator('#intercom-btn'), 1200);
await tap(page.locator('#talk-btn'), 0);
await callState('listening');
await sleep(1200);
for (const t of TURNS) {
  await callState('listening');
  const at = Date.now(), ms = duration(t.ask) * 1000;
  captions.push({ at, until: at + ms + 400, who: 'You', text: t.said, kind: 'line' });
  sounds.push({ at, file: t.ask, ms });
  await sleep(ms + 250);
  if (!await page.evaluate(text => window.__demoHear(text), t.said)) throw new Error('the call was not listening');
  const status = await next('status', at);
  if (t.chart) {   silent = true;   // the assistant draws it: the owner watches the board take the chart in
    captions.push({ at: status, until: status + 5200, text: 'Ask for any chart. It draws it.', kind: 'scene' });
    await sleep(2800);
    await tap(page.locator('#talk-end'), 400);
    await tap(page.locator('#intercom-close'), 600);
    await page.waitForFunction(() => /Dining by month/.test(document.body.innerText), null, { timeout: 15_000 });
    const panel = page.getByText('Dining by month').first();
    await scrollTo(await panel.evaluate(el => el.getBoundingClientRect().top + scrollY - innerHeight / 4), 900);
    await sleep(1500);
    await scrollTo(0, 800);
    await tap(page.locator('#intercom-btn'), 800);
    await tap(page.locator('#talk-btn'), 0);
    await callState('listening');
    silent = false;
    continue;
  }
  const playing = await next('playing', at);
  const ended = await next('ended', playing);
  captions.push({ at: status, until: playing, text: `${t.step}…`, kind: 'status' });
  captions.push({ at: playing, until: ended + 500, who: 'Finnamon', text: t.reply, kind: 'line' });
  sounds.push({ at: playing, file: t.answer, ms: ended - playing });
  await sleep(1200);
}
// 4. Hang up; the same alert, in the dashboard.
await tap(page.locator('#talk-end'), 500);
await tap(page.locator('#intercom-close'), 600);
const alertRow = page.getByText(/Shell/).first();
const y = await alertRow.evaluate(el => el.getBoundingClientRect().top + scrollY - innerHeight / 3);
captions.push({ at: Date.now(), until: Date.now() + 5200, text: 'And the same alert waits in the dashboard.', kind: 'scene' });
await scrollTo(y, 1500);
await alertRow.evaluate(el => { (el.closest('li, tr, .row, .alert') || el).style.cssText += ';outline:3px solid #b5651d;outline-offset:2px;border-radius:6px'; });
await sleep(3600);
const end = Date.now();
rolling = false;
await filming;
await ctx.close();

// --- Stills: the frame around the phone, the alert card, the captions, the end card ---

const W = 1080, H = 1920;
const screen = { height: 1632 };   // 85% of the frame
screen.width = Math.round(screen.height * phone.width / phone.height / 2) * 2;
screen.x = (W - screen.width) / 2; screen.y = 104;
const FONT = "font-family: -apple-system, 'Helvetica Neue', sans-serif;";
const still = await browser.newPage({ viewport: { width: W, height: H } });
async function render(file, html, selector) {
  await still.setContent(`<body style="margin:0;${FONT}">${html}</body>`);
  await (selector ? still.locator(selector) : still).screenshot({ path: file, omitBackground: true });
  return file;
}
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const frame = await render(join(out, 'frame.png'), `
  <div style="position:absolute;left:${screen.x}px;top:${screen.y}px;width:${screen.width}px;height:${screen.height}px;border-radius:52px;
    box-shadow:0 0 0 4px #2a2f37, 0 0 0 3000px #101214"></div>
  <div style="position:absolute;top:36px;width:100%;text-align:center;color:#a3abb8;font-size:28px">${NOTE}</div>`);
const card = await render(join(out, 'card.png'), `<div id="c" style="width:${screen.width - 48}px;box-sizing:border-box;padding:26px 30px;border-radius:30px;
  background:rgba(250,250,252,.97);box-shadow:0 12px 40px rgba(0,0,0,.35);color:#111;font-size:30px;line-height:1.35">
  <div style="display:flex;align-items:center;gap:14px;margin-bottom:10px;font-size:24px;color:#555">
    <span style="width:44px;height:44px;border-radius:50%;background:#2aabee;display:inline-grid;place-items:center;color:#fff;font-size:24px">✈</span>
    <b style="color:#111">Finnamon</b><span>· Telegram · now</span></div>${ALERT}</div>`, '#c');
for (const [i, c] of captions.entries()) {
  const color = c.who === 'You' ? '#7cc4ff' : '#f0b45a';
  c.png = await render(join(out, `caption-${i}.png`), `<div id="c" style="width:${W}px;padding:18px 44px 30px;box-sizing:border-box;text-align:center;
    font-size:${c.kind === 'status' ? 40 : 46}px;line-height:1.22;color:${c.kind === 'status' ? '#c9ced6' : '#f4f6f8'};${c.kind === 'status' ? 'font-style:italic;' : ''}
    background:linear-gradient(to bottom, rgba(16,18,20,0), rgba(16,18,20,.92) 22px)">
    ${c.who ? `<span style="color:${color};font-weight:600">${c.who}:</span> ` : ''}${esc(c.text)}</div>`, '#c');
  c.h = Number(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'stream=height', '-of', 'csv=p=0', c.png], { encoding: 'utf8' }));
}
const endCard = await render(join(out, 'end.png'), `<div style="width:${W}px;height:${H}px;background:#101214;color:#f4f6f8;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:30px;text-align:center;padding:0 80px;box-sizing:border-box">
  <div style="font-size:92px;font-weight:700;color:#e0974a">Finnamon</div>
  <div style="font-size:50px;line-height:1.25">Your money. Your financial AI.</div>
  <div style="font-size:40px;line-height:1.35;color:#c9ced6">Turn Claude into your personal finance assistant.</div>
  <div style="margin-top:56px;font-size:44px;color:#c9ced6">Try it: <span style="font-family:ui-monospace,Menlo,monospace;color:#f4f6f8">finnamon demo</span></div>
  <div style="font-size:36px;color:#e0974a">${esc(REPO_URL || 'github.com/… (link at launch)')}</div>
  <div style="position:absolute;top:36px;left:0;width:100%;font-size:28px;color:#a3abb8">${NOTE}</div></div>`);
await browser.close();

// --- The cut ---

const s = (ms) => (ms / 1000).toFixed(3);
const total = end - t0, END_S = 5;
const list = join(out, 'frames.txt');
writeFileSync(list, frames.map((f, i) => `file '${f.file}'\nduration ${(((frames[i + 1]?.at ?? end) - f.at) / 1000).toFixed(3)}\n`).join('') + `file '${frames.at(-1).file}'\n`);
const looped = (png, seconds = total / 1000) => ['-loop', '1', '-t', String(seconds), '-i', png];
const inputs = ['-f', 'concat', '-safe', '0', '-i', list, ...looped(frame), ...looped(card, 6), ...looped(endCard, END_S)];
const filters = [
  `[0:v]fps=30,scale=${screen.width}:${screen.height}:flags=lanczos,trim=0:${s(total)},setpts=PTS-STARTPTS[phone]`,
  `color=c=#101214:s=${W}x${H}:r=30:d=${s(total)}[bg]`,
  `[bg][phone]overlay=${screen.x}:${screen.y}:shortest=1[v0]`,
  `[v0][1:v]overlay=0:0:shortest=1[v1]`,
  `[2:v]format=rgba,fade=in:st=0.3:d=0.35:alpha=1,fade=out:st=5.1:d=0.4:alpha=1[card]`,
  `[v1][card]overlay=${screen.x + 24}:${screen.y + 70}:eof_action=pass[v2]`,
];
let last = 'v2';
captions.forEach((c, i) => {
  inputs.push(...looped(c.png));
  filters.push(`[${last}][${4 + i}:v]overlay=0:${H - c.h}:enable='between(t,${s(c.at - t0)},${s(c.until - t0)})'[c${i}]`);
  last = `c${i}`;
});
filters.push(`[3:v]fps=30,format=yuv420p,setsar=1[endv]`, `[${last}]format=yuv420p,setsar=1[mainv]`, `anullsrc=r=48000:cl=stereo,atrim=0:${END_S}[enda]`);
const a0 = 4 + captions.length;
sounds.forEach((v, i) => {
  inputs.push('-i', v.file);
  const delay = Math.max(0, Math.round(v.at - t0));
  filters.push(`[${a0 + i}:a]atrim=0:${s(v.ms)},aresample=48000,aformat=channel_layouts=stereo,adelay=${delay}|${delay}[a${i}]`);
});
filters.push(`${sounds.map((_, i) => `[a${i}]`).join('')}amix=inputs=${sounds.length}:normalize=0,apad,atrim=0:${s(total)}[maina]`);
filters.push('[mainv][maina][endv][enda]concat=n=2:v=1:a=1[v][a]');
const mp4 = join(out, 'finnamon-demo.mp4');
ff(...inputs, '-filter_complex', filters.join(';'), '-map', '[v]', '-map', '[a]',
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '22', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', mp4);

// The README teaser: the first question and the start of its answer, muted, captions burned in.
const ask = captions.find(c => c.who === 'You');
const gif = join(out, 'finnamon-teaser.gif');
ff('-ss', s(ask.at - t0 - 400), '-t', '8', '-i', mp4, '-vf',
  'fps=10,scale=480:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle', gif);
for (const f of [mp4, gif]) console.log(`${f}  ${(statSync(f).size / 1e6).toFixed(1)} MB, ${duration(f).toFixed(1)} s`);
process.exit(0);
