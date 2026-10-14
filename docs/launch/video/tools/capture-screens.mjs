// The real screens the launch video shows, from `finnamon demo` (a made-up household, never ~/.finnamon), its clock
// pinned to Sep 20 2026 by libfaketime as in scripts/demo/launch-video.mjs. The intercom's `claude` is the demo's
// stand-in (scripts/demo/bin/claude): asked "Show dining by month", it really runs `finnamon chart`, so the chart is the
// board's own. Writes capture/assets/*.png; `stage-assets.mjs` copies them into assets/.
//
// Needs Google Chrome, libfaketime, web/node_modules (cd web && npm install) and playwright next to this script:
//   npm i --no-save --prefix docs/launch/video/tools playwright && node docs/launch/video/tools/capture-screens.mjs
import { spawnSync, execFileSync } from 'child_process';
import { mkdirSync, mkdtempSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { chromium } from 'playwright';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');
const out = join(HERE, '..', 'capture', 'assets');
mkdirSync(out, { recursive: true });
const TODAY = '2026-09-20 09:41:00';
const DINING = `SELECT strftime('%Y-%m',date) ym, round(sum(amount)) spent FROM tx_now WHERE flow IN ('expense','refund') AND pending=0 AND category_primary='FOOD_AND_DRINK' AND date>=date('now','-5 months') GROUP BY ym ORDER BY ym`;
const SPEC = { title: 'Dining by month', mark: { type: 'bar', tooltip: true }, encoding: { x: { field: 'ym', type: 'ordinal', title: null }, y: { field: 'spent', type: 'quantitative', title: '$' } } };
const TURNS = [{ match: 'dining by month', step: 'Drawing dining by month', reply: 'Done. Dining by month is on your board.',
  command: 'finnamon chart --id dining-monthly --sql "SELECT … FROM tx_now …"',
  exec: ['chart', '--id', 'dining-monthly', '--sql', DINING, '--spec-json', JSON.stringify(SPEC)], today: TODAY }];

const scratch = mkdtempSync(join(tmpdir(), 'finnamon-launch-'));
const env = { ...process.env, FINNAMON_DEMO_HOME: join(scratch, 'home'), CLAUDE_BIN: join(REPO, 'scripts/demo/bin/claude'),
              CLAUDE_CONFIG_DIR: join(scratch, 'claude-config'), DEMO_TURNS: JSON.stringify(TURNS) };
mkdirSync(env.CLAUDE_CONFIG_DIR, { recursive: true });
for (const k of ['FINNAMON_HOME', 'FINNAMON_ASSISTANT', 'FINNAMON_CLAUDE_BIN', 'CLAUDECODE', 'FINNAMON_FROM_AGENT', 'FINNAMON_FROM_CLAUDE']) delete env[k];
const finnamon = (...a) => spawnSync('faketime', ['-f', `@${TODAY}`, 'python3', '-m', 'finnamon.cli', 'demo', ...a], { cwd: REPO, env, encoding: 'utf8' });
process.on('exit', () => { finnamon('--stop'); rmSync(scratch, { recursive: true, force: true }); });
for (const [sig, code] of [['SIGINT', 130], ['SIGTERM', 143], ['SIGHUP', 129]]) process.on(sig, () => process.exit(code));   // so 'exit' still stops the dashboard
const started = finnamon('--reset', '--print');
const url = started.stdout?.match(/the demo dashboard: (\S+)/)?.[1];
if (!url) throw new Error(`finnamon demo did not start:\n${started.stdout}\n${started.stderr}`);

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const browser = await chromium.launch({ channel: 'chrome' });
const offset = new Date(TODAY.replace(' ', 'T')).getTime() - Date.now();
async function open(viewport, deviceScaleFactor, isMobile) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor, isMobile, hasTouch: isMobile, colorScheme: 'light' });
  await ctx.addInitScript(({ offset }) => {   // the page's clock agrees with the server's: Sep 20
    const D = Date;
    window.Date = class extends D { constructor(...a) { super(...(a.length ? a : [D.now() + offset])); } static now() { return D.now() + offset; } };
  }, { offset });
  const page = await ctx.newPage();
  await page.goto(url);
  await page.waitForLoadState('networkidle');
  await sleep(2000);
  return page;
}

// Desktop, 1440x900 at 2x.
let p = await open({ width: 1440, height: 900 }, 2, false);
await p.screenshot({ path: join(out, 'dashboard.png') });
await p.locator('#alerts').screenshot({ path: join(out, 'alerts.png') });
await p.locator('#budgets').screenshot({ path: join(out, 'budgets.png') });
// Ask the intercom for the chart. The session starts on the first keystroke, so an empty Enter wakes it first.
await p.locator('#intercom-btn').click();
await sleep(1500);
await p.locator('#terminal .xterm-helper-textarea').focus();
await p.keyboard.press('Enter');
await p.waitForFunction(() => /demo household/.test(document.querySelector('#terminal').innerText), null, { timeout: 30_000 });
await sleep(1000);
await p.keyboard.type('Show dining by month', { delay: 40 });
await p.keyboard.press('Enter');
await p.waitForFunction(() => /on your board/.test(document.querySelector('#terminal').innerText), null, { timeout: 30_000 });
await sleep(800);
await p.locator('#intercom').screenshot({ path: join(out, 'intercom-ask.png') });
await p.locator('#intercom-close').click();
await p.reload();
await p.waitForLoadState('networkidle');
await sleep(2500);
const panel = p.locator('#chart-panel figure.panel', { hasText: 'Dining by month' });
await panel.scrollIntoViewIfNeeded();
await sleep(800);
await panel.screenshot({ path: join(out, 'chart-dining.png') });

// Phone, 390x844 at 3x.
p = await open({ width: 390, height: 844 }, 3, true);
await p.screenshot({ path: join(out, 'phone.png') });
await browser.close();
execFileSync('cp', [join(REPO, 'web/public/logo.png'), join(out, 'logo.png')]);
console.log(`screens in ${out}`);
process.exit(0);
