# Installing Finnamon for your household

One sitting, on a Mac that stays on (a Mac mini, or a laptop that mostly sleeps at home). About an hour the first
time, most of it waiting on Plaid. Linux works too (systemd user units instead of LaunchAgents; if `finnamon doctor` shows a Linger ✗, run
`sudo loginctl enable-linger $USER` or the services stop when you log out); these steps say macOS where it matters.
Windows works through WSL2, as a Linux install: [Windows (WSL2)](#windows-wsl2) first, then these steps inside Ubuntu.

Finnamon is a self-hosted personal tool: your bank data stays in a SQLite file on this machine, and the only
outside services are Plaid (bank data), Telegram (messages to you) and Claude Code (the assistant; or OpenAI's Codex, if
you choose it at step 3).

## 0. What you need first

| Need | Why | Get it |
|---|---|---|
| macOS (or Linux with systemd) | the daemon, heartbeat and dashboard run as LaunchAgents / user units | |
| Python 3.11+ and [uv](https://docs.astral.sh/uv/) | installs the `finnamon` command | `brew install uv` (or `pip install uv`) |
| [Claude Code](https://claude.com/claude-code), logged in with a Pro or Max plan (or a Console account), **or** OpenAI's [Codex CLI](https://github.com/openai/codex) 0.157+, logged in with ChatGPT Plus or higher (or an OpenAI API key) | triage, every chat reply, and the dashboard's intercom are `claude` sessions, or `codex` ones if you pick Codex at step 3 | Claude Code: install it, run `claude` once and sign in. Codex: [below](#codex-instead-of-claude-code) |
| Node 20.12+ | the web dashboard | `brew install node` |
| A Telegram account and a bot of your own | alerts and chat | in Telegram, message [@BotFather](https://t.me/BotFather): `/newbot`, keep the token; then `/setprivacy` → Disable |
| A Plaid account with Production access | reading your banks | [section 2](#2-plaid-the-slow-part) below: start this first, it can take a while |
| Optional: [Tailscale](https://tailscale.com) | the dashboard on your phone, and bank OAuth returns to the page | |

`finnamon doctor` checks most of that table (Claude Code or Codex, Node, the Plaid keys, the bot) and the rest of the setup below, and says what to run for each
one that fails. Run it whenever something seems off.

## 1. Install

```
git clone <repo> ~/finnamon && cd ~/finnamon
uv tool install -e .          # puts `finnamon` on PATH; -e so `finnamon update` can pull new releases into this checkout
finnamon doctor               # expect a list of ✗: nothing is set up yet, each line says what fixes it
```

If `finnamon` is "not found", `uv tool install` put it in `~/.local/bin`, which is not on your PATH yet: run
`uv tool update-shell`, then open a new terminal.

`finnamon doctor` marks lines with ✓ (fine), ✗ (a problem) and `·` or `!` (optional or unknown, never a failure).
"Remote access" (`finnamon remote`, your phone over Tailscale) and "Voice" (`finnamon voice setup`, local speech
recognition for the Talk button) are optional: skip them and Finnamon works, Talk falls back to the browser. A `PORT`
you set for the dashboard is the one `finnamon open` prints.

## 2. Plaid: the slow part

Plaid is the one step you cannot finish in an evening if it goes badly, so start it before anything else.

1. **Sign up** at [dashboard.plaid.com/signup](https://dashboard.plaid.com/signup). You get Sandbox keys (test
   banks, fake data) at once.
2. **Get Production access.** In the dashboard, "Get Production access" on the Home banner, or Build → Keys →
   "Request access" under Production. For a US or Canadian team created on or after April 15, 2026 this is the
   free **Trial** plan: real data, "Limited to 10 Items" (one Item is one bank login, so 10 banks), per
   [Plaid's pricing page](https://plaid.com/docs/account/billing/). Older teams have
   *Limited Production* instead, which [Plaid's glossary](https://plaid.com/docs/quickstart/glossary/) says
   "cannot connect to certain large institutions that use OAuth, like Bank of America, Chase, or Wells Fargo";
   for those, full Production means a paid plan ([pricing](https://plaid.com/docs/account/billing/)).
3. **OAuth banks** (Chase, Bank of America, Capital One, Schwab, PNC, US Bank and most large US banks) log in on
   the bank's own site instead of inside Plaid. Plaid requires registration in its dashboard's compliance
   center before these work in Production: application display information (what you'll see on the bank's
   consent screen; call it "Finnamon" or your household's name), company information, and for paid plans a
   security questionnaire. From [Plaid's OAuth guide](https://plaid.com/docs/link/oauth/):
   - "If you are on a Trial plan, you do not need to complete these requirements until you upgrade to a paid plan."
   - "Unless you are on a Trial plan, you must complete the Security Questionnaire before gaining access to Chase in Production."
   - "Access to almost all institutions is available within hours of completing the registration requirements,
     but some institutions, such as Fidelity and Charles Schwab, may take longer", and "it may take up to 5 business
     days from Production approval until Schwab Production access has been granted".
4. **Copy the keys**: Team Settings → Keys: the `client_id` and the Production secret (the Sandbox secret too, if
   you want the Sandbox tests). `finnamon init` asks for them.

> **Open question: does Chase link on a Trial key?** Plaid's OAuth guide exempts Trial plans from the Chase
> security questionnaire, which suggests yes, and search results for Plaid's help center article
> ["What is the Plaid Trial plan?"](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan)
> say it lists Chase among the OAuth banks included (the page itself would not load for us to confirm). Nobody has confirmed it with a Trial key against this code yet. If your OAuth bank
> fails to link on Trial, check the dashboard's compliance center for an unfinished registration step first,
> and please report what you saw.

**Redirect URIs.** Plaid's OAuth guide: "Redirect URIs must use HTTPS. The only exception is on Sandbox". On `http://localhost` the dashboard
sends none, and the OAuth bank opens in a popup (allow popups for localhost). Over Tailscale the page is HTTPS
and asks Plaid to return to it: add `https://<machine>.<tailnet>.ts.net/` under **API → Allowed redirect URIs**
in the Plaid dashboard, or that bank's link fails with an invalid-redirect error. `finnamon link` in a terminal
uses Plaid Hosted Link (a page on Plaid's own site) and needs no redirect URI.

## 3. `finnamon init`

```
finnamon init
```

Five steps, each one re-runnable (it skips what's already there). Enter on a blank Plaid client_id or Telegram token
skips that step for now; the rest still runs, and `finnamon init` again adds it later. Waiting on Plaid? `finnamon demo`
shows the whole thing on a made-up household meanwhile.

1. **Plaid keys.** Checked live against Plaid. Linking always uses **Production**: with only a Sandbox secret on file the check says so, and no
   bank can be linked until you add the Production secret. Sandbox secrets are for the test suite. Moving from Sandbox to Production later:
   `finnamon init --plaid` (prompts for the client_id and secrets without echoing them; Enter keeps one already on file).
2. **Telegram bot.** Paste the token; then open the link it prints and tap Start, which records the household
   chat. To add your partner later: `finnamon owner add <name>` (make a group with both of you and the bot). A partner who won't chat with the bot: `finnamon owner add <name> --no-telegram`; add their Telegram id later with `--user-id <id>`.
3. **Assistant.** Checks `claude` is installed and logged in, and writes the assistant's directory,
   `~/.finnamon/assistant/` (its instructions, allow list and skills), and marks it trusted for Claude Code. With OpenAI's
   Codex CLI installed it first asks which runs the assistant (Enter keeps Claude Code); `codex` shares your Codex login,
   writes Finnamon's own Codex config under `~/.finnamon/codex/` and selects it. Switch later with
   `finnamon settings set assistant codex|claude`.
4. **Dashboard.** `npm install` in `web/`.
5. **Schedule.** Installs the LaunchAgents: the always-on daemon (syncs every 6 hours, detects, triages,
   notifies, reads Telegram), an hourly heartbeat, and the dashboard on `http://localhost:8888`.

If you skip step 5, `finnamon install` does it later (and again after any release that changes the jobs).

A new household's Telegram chat shares the dashboard's conversation (`session` mode, the default: the daemon reads the bot
and types each message into the dashboard's session; no plugin). Without the dashboard, or with `finnamon channel off`,
the daemon answers with its own separate session (the legacy `daemon` mode). The Claude Code Telegram channel plugin
(`finnamon channel on`, [CHANNEL-MODE.md](CHANNEL-MODE.md)) is an option you choose later, not part of setup.

### Codex instead of Claude Code

1. **Install Codex** (0.157 or newer) and log in once as yourself:
   ```
   npm i -g @openai/codex
   codex login                  # ChatGPT Plus or higher in the browser
   codex login --device-auth    # the same on a headless box (SSH, a Mac mini with no screen): a code to enter on another device
   printenv OPENAI_API_KEY | codex login --with-api-key   # or an OpenAI API key instead of a ChatGPT plan
   ```
2. **`finnamon init`** sees `codex` at step 3 and asks which runs the assistant; answer Codex. It writes the same
   bundle as `AGENTS.md` and `.agents/skills/` in `~/.finnamon/assistant/`, and Finnamon's own Codex home,
   `~/.finnamon/codex/`: a `config.toml` with a permission profile that keeps the shell away from your keys and the
   database, the secret guard and the phone's permission prompt as hooks (their trust pinned), no web search, no
   ChatGPT apps, and the `finnamon` MCP tool, which is how the assistant runs `finnamon` commands (the same allow list as
   Claude's). Your own `~/.codex` config, MCP servers and skills never reach the household's assistant.
3. **The login is shared, not repeated.** `~/.finnamon/codex/auth.json` is a link to your `~/.codex/auth.json`, so a
   token refresh by either keeps both logged in. Only if you have no `~/.codex/auth.json` (no login yet, or a keychain
   login) does init run a separate `codex login` for Finnamon. **Never run `codex logout` with `CODEX_HOME` set to
   `~/.finnamon/codex`**: it revokes the shared tokens and logs you out everywhere.
4. **`finnamon doctor`** then checks the Codex lines: the version (0.157 or newer; newer than the tested one is a `·`
   note), the login and its link, hooks on, the generated config intact (profile, trust, pinned hooks, nothing that
   unseals it), no skills of yours leaking into the session, and the MCP package. A broken link to your login is re-made.

Switch either way later with `finnamon settings set assistant codex|claude` (at a terminal; the assistant cannot do it).

### Codex: what v1 does not do

- **No web lookups.** Codex sessions run with web search off; Claude asks you before each fetch at the dashboard.
- **Telegram through Finnamon's daemon only**: session mode, where it types each message into the dashboard's Codex
  session (init sets it for Codex), or `finnamon channel off`'s separate `codex exec resume` thread. No channel mode: the
  Telegram channel plugin is Claude Code's ([CHANNEL-MODE.md](CHANNEL-MODE.md)).
- **No "Fetch by AI" import** (`finnamon import --browser`): it still runs `claude`. CSV import works the same.
- **Codex 0.157 or newer.** Older versions are refused; Claude Code stays the assistant.

## 4. Open the dashboard and link a bank

```
finnamon open
```

The address it opens carries the dashboard's key once (`~/.finnamon/web-token`); the page keeps it as a cookie.
Until a bank is linked the alerts card says **Link a bank to start**: nothing is being watched yet. Click
**Link account** (or **Add account → Link account**), pick the bank, log in. The first sync pulls up to 24
months of history. No dashboard? `finnamon link` does the same from a terminal (`--telegram` sends the Plaid
link to your phone).

Then click the intercom button in the corner and ask it to "set up my budgets".

Banks Plaid can't reach (HSBC US personal banking): **Add account → Import CSV**, then **Fetch by AI** (you log in to
the bank in a Chrome window of Finnamon's own, Claude downloads the CSV and imports it; Claude Code only) or pick the
file yourself; see [COMMANDS.md](COMMANDS.md). For HSBC, Fetch by AI goes through the Claude in Chrome extension: Claude
Code 2.1.292 or later, and the extension installed from the Chrome Web Store in Finnamon's Chrome profile
(`~/.finnamon/chrome-extension-test`, the window Fetch by AI opens) and signed in to the same claude.ai account. The first run pairs it by itself; `finnamon doctor` shows the version and pairing.

## 5. Your phone (optional, Tailscale)

The dashboard listens on localhost only. To reach it from your phone over your tailnet:

```
finnamon remote        # tailscale serve --bg 8888, lets <machine>.<tailnet>.ts.net in, and shows a QR code for the phone
```

`finnamon remote` needs Tailscale installed and logged in, and the dashboard running on 8888; it changes nothing
otherwise and says what to do. If
your tailnet has Serve turned off, it prints Tailscale's link to enable it and waits. It never turns on Funnel (and stops if Funnel is on), and
never replaces something else `tailscale serve` already puts on HTTPS without asking. The name goes into
`~/.finnamon/web-hosts`, which the dashboard reads on every request (no restart; a `FINNAMON_WEB_HOSTS` you
exported before still works too). `finnamon doctor` has a "Remote access" line. `finnamon remote --off` takes
the names back out and stops Tailscale serving the dashboard's port. The dashboard itself refuses any request
Tailscale marks as arriving over Funnel. Set it up before with `FINNAMON_WEB_HOSTS`? Run `finnamon remote` once.

Scan the QR code with the phone's camera: the dashboard opens there, logged in. It carries a one-time pairing code, not
the dashboard's key: one device, within 5 minutes; the address is printed under it too. For the next phone, run
`finnamon remote` again (already set up, it goes straight to a fresh code). `finnamon open --host <machine>.<tailnet>.ts.net`
still prints an address with the key itself; hand that over privately, never through the household chat. Add the same `https://<machine>.<tailnet>.ts.net/` to Plaid's Allowed redirect URIs (section 2).
Never expose the port publicly: the intercom is a shell as you.

## 6. Check it

```
finnamon doctor
```

Everything should be ✓. A `!` is advice (for example the bot's privacy mode, which matters once the household
is a group). Within a day you get the first alert or nothing; on Sunday the weekly roundup arrives either way.

Later: `finnamon update` picks up new releases (pulls, migrates, rewrites the assistant's directory, restarts
what changed); the dashboard's Settings cog (a dot on it says an update is available) runs the same thing from the page, a phone over `finnamon remote` included.
[COMMANDS.md](COMMANDS.md) has the day-to-day commands.

## 7. Backups and restoring one

Once a week the daemon copies the database to `~/.finnamon/backups/finnamon-YYYY-MM-DD.db` (readable only by you)
and keeps the newest 8. `finnamon doctor` and `finnamon status` show the last one, or why it failed. To restore:

```
finnamon install --uninstall            # stop the daemon and the dashboard
lsof ~/.finnamon/finnamon.db            # should print nothing: quit any `finnamon` or `claude` still using it
cd ~/.finnamon
for f in finnamon.db finnamon.db-wal finnamon.db-shm; do [ -e $f ] && mv $f $f.broken; done
cp backups/finnamon-2026-09-20.db finnamon.db && chmod 600 finnamon.db
finnamon install                        # start them again (it asks before starting the assistant's conversation afresh)
```

The next sync fills in the transactions since the backup, and alerts raised since then may arrive a second time. A bank
linked after the backup was taken is not in it: link that one again. On a Mac the logs in `~/Library/Logs/finnamon` are rotated at 5 MB, three old copies kept.

## Windows (WSL2)

There is no native Windows Finnamon: it runs in Ubuntu inside Windows (WSL2), as the Linux install above. The
`wsl` workflow (`.github/workflows/wsl.yml`) runs this install on GitHub's Windows runners on every push to main: the
tests, `finnamon demo`, and `finnamon install`'s systemd units with the dashboard answering on localhost.

1. **WSL2 and Ubuntu.** In PowerShell as administrator: `wsl --install -d Ubuntu-24.04`, restart Windows if it asks,
   then open Ubuntu from the Start menu and create your user.
2. **systemd on.** Recent Ubuntu images ship with it on; check with `ps -p 1 -o comm=`, which should print `systemd`. If not:
   ```
   printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf
   ```
   then `wsl --shutdown` in PowerShell and open Ubuntu again.
3. **The tools, inside Ubuntu** (not the Windows ones: a Windows `node` or `claude` on the PATH WSL inherits does not work here):
   ```
   sudo apt update && sudo apt install -y git curl build-essential
   curl -LsSf https://astral.sh/uv/install.sh | sh                                   # uv
   curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs   # Node 20
   curl -fsSL https://claude.ai/install.sh | bash                                    # Claude Code; run `claude` once to sign in
   ```
4. **Clone into the Linux home**, `git clone <repo> ~/finnamon`, never under `/mnt/c/`: Windows' drives cannot keep
   the 0600 permissions on the keys and the database, and they are slow. Then [section 1](#1-install) on.
5. **Linger**: `sudo loginctl enable-linger $USER`, as on any Linux.
6. **Keep the PC and WSL running.** The daemon only watches while Ubuntu runs, and Windows stops a WSL distribution
   some time after its last terminal closes, systemd or not. Keep an Ubuntu window open, or have Task Scheduler run
   `wsl.exe -d Ubuntu-24.04 --exec sleep infinity` at log on, and set Windows not to sleep. CI cannot test this
   part; `finnamon doctor` shows a stale heartbeat if WSL was stopped, and on WSL it also checks systemd and repeats this
   advice. To stop Windows idling the distro down, put `[wsl2]` and `vmIdleTimeout=-1` in `%UserProfile%\.wslconfig`, then
   `wsl --shutdown`. Nothing inside Ubuntu can notice that it was stopped, so for an alarm, add a second Task Scheduler task
   (every 15 minutes) running `wsl.exe -d Ubuntu-24.04 --exec finnamon doctor`: a non-zero exit (doctor exits 1 on any
   problem, a stopped daemon included) is the alarm. Untested in CI.

Spoken replies: Talk uses macOS `say` on a Mac; on Linux and WSL the dashboard speaks them with the browser's own speech
synthesis (the Windows browser on WSL), and the Talk note says so. Nothing needs installing for that.

The dashboard opens in the Windows browser: WSL2 forwards `localhost`, so `http://localhost:8888` works there, and
`finnamon open` hands the address to Windows (`wslview`, else `explorer.exe`, which also prints it). Tested on WSL only
as far as CI goes; not yet tried there: `import --browser` (it needs Linux Chrome inside Ubuntu, `google-chrome`,
shown through WSLg; the Windows Chrome is not used), `finnamon voice setup` and `finnamon remote` (install Tailscale
inside Ubuntu).
