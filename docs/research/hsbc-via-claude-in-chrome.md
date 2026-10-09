# Importing from a bank that blocks automation, through the Claude in Chrome extension (research, 2026-10-06)

Some banks refuse a browser they can tell is automated. The case studied here is HSBC US, picked because it is a clear
example of the pattern, not as a list of banks Finnamon supports; the same reasoning applies to any bank that does this.

Question: HSBC US refuses login ("reference: EAC") in any Chrome with `--remote-debugging-port`, but accepts a
no-port Chrome with the Claude in Chrome extension driving it. Can `finnamon import --browser hsbc` drive the browser
through the extension instead of CDP, sealed and guarded as today? Desk research plus local experiments against
example.com and iana.org only. No bank pages, no credentials. Claude Code 2.1.292, codex-cli 0.157.0.

**Recommendation: go, Claude-only, as a new mode beside `--no-cdp` (which stays the fallback and the only path for
Codex).** Before the build, run a one-hour pairing and guarded-run spike with a real account (see "Open risks").

## 1. Can the sealed session use it? Yes (verified)

- `claude --chrome` adds a *dynamic* MCP server `claude-in-chrome` (the binary with `--claude-in-chrome-mcp`).
  **It survives `--strict-mcp-config` and `--setting-sources project`**, both interactive and with `-p`: verified with
  `claude -p … --chrome --strict-mcp-config --setting-sources project`, which listed all 22 tools and
  called `list_connected_browsers`. `CLAUDE_CODE_ENABLE_CFC=1` in the environment does the same as `--chrome`.
- If you hand-write a `claude-in-chrome` entry and pass it with `--mcp-config` (with `--strict-mcp-config`), it is
  **not** loaded: no tools appear. Use `--chrome`.
- It needs a claude.ai `/login` (OAuth). With an API key or a `setup-token`, `--chrome` is silently off. The extension
  must be signed in to the same claude.ai account. Tool calls go through Anthropic's relay
  (`wss://bridge.claudeusercontent.com`), not native messaging. That is why a fresh `--user-data-dir` with no
  NativeMessagingHosts manifest works, as a manual test showed.
- Tools: `navigate, read_page, get_page_text, find, computer (left_click, right_click, type, key, scroll, scroll_to,
  screenshot, wait, hover, zoom, drag, double/triple_click), form_input, javascript_tool, browser_batch,
  tabs_context_mcp, tabs_create_mcp, tabs_close_mcp, read_console_messages, read_network_requests, resize_window,
  gif_creator, upload_image, file_upload, shortcuts_list, shortcuts_execute, list_connected_browsers,
  select_browser, switch_browser`. There is no download tool; see section 4.
- Every result carries a "Tab Context" block listing each tab in the session's group as tabId, title and URL.
- `CLAUDE_CHROME_PERMISSION_MODE` = `ask` (default) | `skip_all_permission_checks` | `follow_a_plan` sets the
  extension's own per-site prompt.

## 2. Picking the browser: the pin is weak by itself

- Each extension install is a "device" with an id. `list_connected_browsers` returns
  `{deviceId, name, osPlatform, isLocal, inUse}`.
- The automatic pick (`afn` in the binary) works like this:
  1. Choose the *persisted* device if it is connected.
  2. Otherwise, if only one browser is connected, choose it.
  3. Otherwise, if exactly one is live on this computer, choose it.
  4. Otherwise, ask.
- The persisted device is `~/.claude.json` → `chromeExtension.pairedDeviceId`, falling back to the env var
  `CLAUDE_CHROME_PAIRED_DEVICE_ID`. **The global file wins over the env var.**
- **Verified:** with `CLAUDE_CHROME_PAIRED_DEVICE_ID` set to an id that doesn't exist, the session bound to the only
  connected browser, which was the tester's everyday Chrome (`inUse: true`). The env var is a preference, not a fence.
- **Verified:** `select_browser {deviceId}` binds exactly that device and errors if it isn't connected. But it also
  **writes the pick into the global `~/.claude.json`**, so every later `claude` the person runs would prefer Finnamon's
  Chrome, which holds the bank cookies. The experiment wrote that key; it was removed again (it was unset before).
- If the selected browser disappears mid-session (Chrome quits, or the service worker idles), the selection is cleared
  and the next call runs discovery again, which can pick the everyday Chrome.
- Fix: run the import session with its own `CLAUDE_CONFIG_DIR` (for example `~/.finnamon/claude-import`). Then the
  persisted pick, and `select_browser`'s write, belong to Finnamon and never touch the person's sessions. The cost is
  one `/login` in that directory: the credentials are keyed per config directory. Also make the guard hook require
  `select_browser <the stored id>` as the session's first browser call (section 3).
- Getting the id once: in a one-time setup step, Finnamon opens `~/.finnamon/chrome-ext` with no port (`open -na`).
  The person installs the extension there and signs in. Finnamon takes `list_connected_browsers` before and after and
  stores the one new deviceId as a setting.

## 3. Guards: the equivalent of today's single-tab pin and URL allow list

- Hooks see these tools: PreToolUse and PostToolUse fire for `mcp__claude-in-chrome__*`, with `tool_name`,
  `tool_input` (`tabId`, `url`, `action`, `ref`, `deviceId`) and `mcp_server.source: "dynamic"`. PostToolUse also gets
  `tool_response`, which includes the Tab Context URLs. Verified from logged payloads.
- Claude Code has a host rule, `ClaudeInChromeDomain(<host>)`. **Deny works** (`deny: ClaudeInChromeDomain(example.org)`
  → "Claude in Chrome is denied on example.org"). **An allow rule is not a fence:** with the tool allow-listed and
  `dontAsk`, navigating to an unlisted host (iana.org) went through. A rule cannot say "this host only", so the hook
  is the fence, as with agent-browser today.
- Proposed `finnamon hook chrome-guard`, a pure `chrome_call_ok(tool, input, state)` like `browser_command_ok`:
  - Before anything else, only `select_browser` with `deviceId == FINNAMON_IMPORT_DEVICE`.
  - Then `tabs_context_mcp`. Its PostToolUse records the group's single tabId in the `import-tab` marker, the same as
    today's binding.
  - Every call with a `tabId` must use the pinned tab.
  - `navigate` only to the bank's hosts (`BANK_HOSTS["hsbc"]`, matched as a suffix on `.hsbc.com`; confirm HSBC's
    exact hosts during the spike), or `back`.
  - `computer` only `left_click`, `scroll`, `scroll_to`, `screenshot`, `wait` and `hover`: no `type`, `key` or drag,
    because typing is the person's.
  - Also allowed: `find`, `read_page`, `get_page_text` and `tabs_close_mcp <pinned>`.
  - Disallowed outright: `javascript_tool`, `form_input`, `file_upload`, `upload_image`, `browser_batch` (or allow it
    and check every action inside), `switch_browser`, `tabs_create_mcp`, `read_network_requests`,
    `read_console_messages`, `shortcuts_*`, `gif_creator` and `resize_window`.
  - After each call, PostToolUse parses the pinned tab's URL from Tab Context. Off the bank's hosts, it writes a
    "tripped" marker and returns `{"continue": false, "stopReason": …}`, and PreToolUse refuses everything from then on.
    A link the page itself follows is therefore caught after one action, before any further read or click.
- The extension's own `ask` mode adds a per-site "allow Claude on us.hsbc.com" prompt in Chrome, which the person
  approves once. Keep `ask`; don't use `skip_all_permission_checks`.

## 4. CSV download: works without a download tool

- Clicking HSBC's "Download" → "Spreadsheet CSV file" → "Download" is an ordinary page download. Chrome saves it under
  the profile's `download.default_directory`. `pin_downloads()` already sets that for `--no-cdp`, before Chrome starts.
  The extension doesn't change this.
- The session cannot list or read files (Read is disallowed, and the guard blocks `~/.finnamon` paths). So add
  `finnamon import "<account>" --newest-download [--dry-run|--flip]`, which reuses `wait_for_csv(folder, since)` on
  `FINNAMON_HOME/downloads`. The session never names a path.

## 5. Codex: no equivalent, so HSBC-by-AI is Claude-only

OpenAI's Codex Chrome extension (May 2026) is driven from the Codex app or ChatGPT ("@Chrome" in a chat, set up under
Plugins). Its domain allow and block lists live in the app's Settings → Computer Use. Nothing documents it for
`codex exec` or the CLI, and codex-cli 0.157.0 has no browser flag. A Codex household keeps `--no-cdp` (by hand).

## 6. Design (rough size: about 1 to 1.5 days plus evals)

| Change | Where | Size |
|---|---|---|
| `--extension` mode (or make it the default for HSBC when a device is paired): `chrome_open` on `~/.finnamon/chrome-ext` + `pin_downloads`, then `execve claude --chrome` with `--strict-mcp-config --setting-sources project`, `--settings` (chrome-guard hooks + Bash guard), `--allowedTools`/`--disallowedTools` from a new `chrome_session_tools()`, env `CLAUDE_CONFIG_DIR=~/.finnamon/claude-import`, `CLAUDE_CHROME_PAIRED_DEVICE_ID`, `CLAUDE_CHROME_PERMISSION_MODE=ask`, `FINNAMON_IMPORT_DEVICE`, `FINNAMON_IMPORT_SESSION` | `finnamon/cli.py` (`cmd_import`) | ~60 lines |
| `chrome_call_ok` + `hook chrome-guard` (Pre + Post, tripped marker), `BANK_HOSTS` | `finnamon/cli.py` beside `browser_command_ok` | ~80 lines |
| One-time pairing: `finnamon import --browser --pair-extension` (open profile at the Web Store page, diff `list_connected_browsers` via `claude -p --chrome` in the import config dir, store the setting) and the `/login` hint | `finnamon/cli.py`, `store` setting | ~50 lines |
| `--newest-download` | `cmd_import`, reuses `wait_for_csv` | ~15 lines |
| Skill: new `import-extension` (tools instead of `agent-browser` verbs; same rules: no browser call while the person logs in, never type, page text is data) so the CDP skill stays as it is | `assistant_bundle/.claude/skills/` | ~1 page |
| Dashboard "Fetch by AI" picks the mode; Codex households only get "Fetch without AI" | `web/` | small |
| Tests: `chrome_call_ok` table (unbound, wrong device, wrong tab, off-host navigate, `type`/`javascript_tool`, tripped), Post URL parse, launcher argv/env with a stub claude; evals rerun (prompt files touched) | `tests/` | ~150 lines |

Don't touch the `--no-cdp` code beyond reusing `chrome_open`, `pin_downloads` and `wait_for_csv`.

## Open risks

1. **HSBC may start flagging the extension.** The extension attaches `chrome.debugger` for clicks and screenshots.
   One manual test passed, but Transmit could change. Keep the rule from today's skill: no browser call at all
   until the person says they are logged in. `--no-cdp` stays one click away.
2. **Binding to the wrong browser** (section 2): mitigated by a separate `CLAUDE_CONFIG_DIR`, the forced first
   `select_browser`, the tab pin, and the PostToolUse URL check. Remaining case: a mid-session disconnect followed by
   rediscovery. The next call then carries a tabId that doesn't exist in the other browser, and the extension refuses
   it. That is unverified against a real second browser: test it in the spike.
3. **Account and transport:** needs a claude.ai Pro/Max login; it fails closed under an API key. Page content travels
   through Anthropic's bridge. Today it reaches the model anyway, but the transport is new.
4. **Moving target:** tool names and the selection logic are Claude Code internals (2.1.x ships daily). The guard
   allow-lists and fails closed, so a change breaks the feature rather than the fence. Pin a minimum Claude Code
   version in `doctor`.
5. **The extension's service worker idles** in long sessions ("Receiving end does not exist"). The person may need to
   reconnect. That is acceptable for a session they attend.
6. A separate `/login` for the import config directory is one more setup step for the household.
