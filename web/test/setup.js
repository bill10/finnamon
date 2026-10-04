// Preloaded by `npm test` (--import): the server reads FINNAMON_HOME at import time, and a test that forgets to inject
// `token` must create the dashboard's key in a scratch directory, never in the household's real ~/.finnamon.
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const home = mkdtempSync(join(tmpdir(), 'finnamon-web-test-'));
process.env.FINNAMON_HOME = home;
delete process.env.PORT;   // the cookie is named by port; a shell with PORT exported must not rename it under the tests
process.on('exit', () => rmSync(home, { recursive: true, force: true }));
