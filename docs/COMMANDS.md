# Commands and talking to the assistant

Setup is [INSTALL.md](INSTALL.md); the dashboard is [DASHBOARD.md](DASHBOARD.md).

In the Telegram chat, just talk. The daemon hands each message to Claude Code (or Codex, when `finnamon settings set
assistant codex` chose it: the same skills, as `$finnamon` and `$triage`, and the CLI through its `finnamon` tool) with the `finnamon`
skill; the assistant reads with `finnamon query`/`alerts`/`budget` and writes only through the commands
allow-listed in the assistant's `.claude/settings.json` (`normal`, `budget`, `threshold`, `settings`, `category`, `alias`,
`triage set`, `detect --draft`, `account add`, `account type`, `import`, `link --start` / `--finish`).
"It is normal" on an alert mutes that pattern (`finnamon normal --alert <id>`); on a subscription that stopped or
changed price ("yes, I cancelled it") it acknowledges that one subscription, so another at the same merchant still
alerts. "Acme is normal" (`finnamon normal "Acme"`, no `--kind`) mutes Acme's charges, price changes included, but never a subscription
stopping; that takes `--kind anomaly:recurring_changed`. `finnamon normal --list` shows the rules with
their ids, `finnamon normal --remove <id>` deletes one. "Fine this once" is `finnamon alerts --dismiss <id>`: resolved,
no rule, the next one still alerts. `finnamon alerts --undo <id>` reopens either kind and removes only the rule that
alert wrote. Each covers every alert on that alert's transaction: when two detectors fire on one charge, it is one alert
(the other under `folded`), one message and one Dismiss.
"Costco is groceries" is a rule, `finnamon category costco groceries`: every Costco charge, past and future. "That one
charge was groceries" is a one-time edit of that charge only, `finnamon category --tx <transaction_id> groceries` (`--clear`
undoes it; it wins over the rule). `finnamon category costco --clear` deletes the rule (charges fall back to the bank's category; one-time edits stay) and `finnamon category --rules` lists the rules and one-time edits; the assistant asks which you mean when it can't tell.
A budget covers one or more categories and merchants: `finnamon budget set dining 400 --category restaurants
--category "fast food" --category coffee`, `finnamon budget set "water and trash" 90 --merchant "Seattle Public Utilities"
--merchant Recology` (each flag repeats; a merchant is any name its charges show, as for `finnamon category`, and a name with no charge is refused with suggestions; either one given replaces all of the budget's categories and merchants; a charge
several of them match counts once; two budgets may share a category, and `budget set` then warns (`overlaps`) since both
alert on those charges; the dashboard's Overall counts such a charge once). Every `budget set` says what it counts
(`counts`, as people read it); a new budget named without `--category` takes its name as the category and says so
(`guessed`) when the name was only read as one ("dining" → Restaurant, "utilities" → every utility but rent). A name that is
no category ("subscriptions", "kids", "car insurance") is refused with what it might mean: name its categories or merchants.
`finnamon alias --list` shows every alias with the transactions it covers today and `finnamon alias --remove "<name>"`
deletes one; an alias whose raw name or pattern matches no transaction is refused with the raw names it might mean.
A bank's CSV has no categories: `finnamon import` says how many new rows have none (`uncategorized`, by merchant), and
`finnamon category --uncategorized` lists them all; categorize them by merchant (a rule each) or by charge (`--tx`).
`finnamon category --tx <transaction_id> <category> --every` writes the rule for that charge's merchant (the dashboard's
"Every charge from …"). `finnamon threshold "<account>" <amount>` takes a checking or savings account (a card's or a
brokerage's could never fire), named as `finnamon account list` shows it or by its last four digits. The mortgage counts (the checking-side payment, once,
even when the bank filed it as a transfer) toward a budget on the mortgage category (`LOAN_PAYMENTS_MORTGAGE_PAYMENT`, or all
of `LOAN_PAYMENTS`) or on its merchant, and never toward any other category; transfers and card payments never count.
Pace counts the mortgage, and a charge of a monthly or yearly bill or subscription Plaid sees as recurring (the same
merchant, within 25% of its amount), once at its amount and projects only the rest; `--fixed` (`--no-fixed` undoes it)
marks a budget that is one bill a month, so it is compared to its limit and never projected. A limit alone (a dashboard
limit edit too) keeps the categories, merchants and `--fixed`; a removed budget set again keeps its categories and
merchants but comes back not fixed. `finnamon budget` lists each with its `categories`, `merchants`, `fixed`, `spent`,
`recurring` (the part of `spent` that is not projected) and `pace`.
It can start adding a bank ("add my Chase account" → `finnamon link --start` gives you the Plaid link; you
log in, the daemon finishes), and send a broken bank's re-login link to the chat (reply "fix Chase" to a re-login or
expiring-connection alert → `finnamon link --update <item_id> --telegram`; when two logins share a bank the alert says
"fix <item id>" and names whose login it is; on the dashboard the alert has a Reconnect button that opens the login
page directly). Within a minute of the login the daemon syncs that bank and says "<bank> is reconnected". Links it sends to the chat are rate-limited: one per bank per 15 minutes,
6 a day counting added banks; a person at the terminal is not limited. It cannot unlink, sync, or add people; those are yours:

| You want | Run |
|---|---|
| add a bank / fix a broken login / unlink | `finnamon link` (or `--start` then `--finish`; add `--telegram` to get the Plaid link on your phone) / `finnamon link --update <item_id>` / `finnamon link --remove <item_id>` |
| add your partner | `finnamon owner add jane` (the first time, create a Telegram group with both of you and the bot; once the household is a group, add them to it and the code counts only there; `--new-group` moves the household to a new group); in channel mode `finnamon owner add jane --user-id <telegram user id>`; a partner who won't use Telegram: `finnamon owner add jane --no-telegram` (add the id later the same way) |
| check the setup, with a fix for each problem | `finnamon doctor` |
| the dashboard on your phone (Tailscale) | `finnamon remote`, then scan its QR code with the phone (again for each phone; see [DASHBOARD.md](DASHBOARD.md); `finnamon remote --off` undoes it) |
| see what's going on | `finnamon status`, `finnamon alerts`, `finnamon alerts --suppressed`, `finnamon channel status` (who answers the bot: `session`, the default for new installs, shares one conversation between the chat and the dashboard; `channel` is the Claude plugin; `daemon` is the legacy separate session; switch with `finnamon channel session|on|off`, see [CHANNEL-MODE.md](CHANNEL-MODE.md)) |
| approve a detector Claude drafted | `finnamon detect --review` |
| run a cycle by hand | `finnamon run` |
| switch the assistant to Codex (or back) | `finnamon settings set assistant codex` (refused until `finnamon init` has set Codex up and `finnamon doctor`'s Codex lines are clear; `claude` switches back; the dashboard restarts onto it). Triage then runs `$triage` through `codex exec`, and in the dashboard's Codex session you can type `$triage` yourself |
| change daemon timing (sync interval, 1 to 12 hours; Claude timeout) | `finnamon settings set sync_interval_hours 8 --ops` |
| tell it what the house is worth | `finnamon property set "House on Elm St" 850000` (or ask the assistant; it's counted into net worth) |
| a bank Plaid doesn't reach (HSBC US personal banking shows in Plaid's search and then says "not supported") | **Add account → Import CSV** on the dashboard, then Fetch by AI, Fetch without AI or the CSV file: see [Banks Plaid doesn't reach](#banks-plaid-doesnt-reach) below |
| pick up new code after `git pull` | `finnamon update` (pulls, applies migrations, rewrites `~/.finnamon/assistant/` from the release, restarts only what went stale, and the assistant carries on: it resumes its session by id rather than starting a new one. `--no-pull` adopts a tree you pulled yourself, `--dry-run` says what it would restart, `--check` prints what a pull would bring as JSON). The dashboard shows "Update available" in its header when there is something to pull, and its Update button runs the same `finnamon update` |
| reprovision the services | `finnamon install` (rewrites the unit files and reloads every job, and retires the household's conversation so the assistant starts fresh; it asks first, and `finnamon update` is the one that keeps it. Needed when a release changes the units, which `update` detects and tells you.) |

Household, not personal: one bot, one chat, one memory, one set of budgets. Everyone in the chat
sees everything. Joint accounts linked from two logins are detected and counted once.

## Banks Plaid doesn't reach

**Add the account.** **Add account → Import CSV** on the dashboard opens a window that adds the account (a name, the
bank, and what kind it is; one row per account, so HSBC Checking and HSBC Savings both sit under the one HSBC) and then
takes the bank's CSV export. In a terminal: `finnamon account add "HSBC Checking" --institution HSBC` (or ask the
assistant).

**Feed it the export**, one of four ways:

- **Pick the file** in that window, or `finnamon import "HSBC Checking" <file.csv>`.
- **Fetch by AI** (Claude Code only): a separate Claude session opens a Chrome window, you log in to the bank, and it
  downloads the CSV export and imports it. You watch it in the intercom's Import tab, whose Stop button ends it. It needs
  the dashboard open on the Finnamon box itself; elsewhere the panel says what to run instead. In a terminal:
  `finnamon import --browser <bank>`.
- **Fetch without AI**, the button next to it (or `finnamon import --browser <bank> --no-cdp`): Finnamon's Chrome
  opens at the bank with nothing attached, the export steps are shown, you download the CSV yourself, and it is
  imported as it lands in `~/.finnamon/downloads`, after a preview you confirm.
- **Your everyday browser**: download the CSV and import the file as above.

On another computer with a checkout, add `--to https://<the box's dashboard>` to `finnamon import`, with
`FINNAMON_WEB_TOKEN` set to what `finnamon web token` prints on the box, and the file is uploaded there.

**What Fetch by AI needs.** Google Chrome (set `FINNAMON_CHROME` to its executable if it lives somewhere unusual). It
runs on Chrome profiles of Finnamon's own under `~/.finnamon/`, so the bank remembers the device between runs and your
everyday profile stays out of it. You always log in yourself; the AI never types into the page.

- **Most banks**: Chrome opens with a debugging port, and the session drives it through the `agent-browser` CLI (on
  your PATH) after you say you're in: one tab, a fixed list of commands (clicks, scrolls, a few keys such as Enter and
  Tab; no typing text, no scripts). A window that opens blank: go to your bank's login yourself.
- **HSBC US** refuses any Chrome with a debugging port ("reference: EAC"), so it goes through the **Claude in Chrome
  extension** instead (`finnamon import --browser hsbc --extension`). It needs Claude Code 2.1.292 or later and your
  normal claude.ai login (not an API key), and the extension installed from the Chrome Web Store in Finnamon's
  extension profile, `~/.finnamon/chrome-extension-test`, signed in to the same account.
  1. That profile opens blank (or is reused if it is already open). Finnamon waits up to about two minutes for the
     extension to connect; if it stays idle, click its icon.
  2. The session opens HSBC's login in a tab of the extension's tab group. Log in there, then tell it you're in.
  3. It clicks through to Download → Spreadsheet CSV on HSBC's own site only (a guard refuses typing, scripts, new tabs
     and other sites, and stops the session if the tab leaves the bank), then imports the file.

  The first run pairs the extension by itself when exactly one browser is connected; otherwise it falls back to the
  by-hand export and names `finnamon import --pair-extension` (run it with that Chrome profile closed). On a Codex
  household the dashboard shows only Fetch without AI for an HSBC account. `finnamon doctor` checks the
  Claude Code version and shows the pairing.

**What Fetch by AI keeps on the box.** The bank pages' text and the screenshots the session takes stay in the
assistant's Claude transcripts, under `~/.claude/projects/<the assistant directory's slug>/` (for example
`-Users-you--finnamon-assistant`; delete a session's `.jsonl` there to drop it). Each imported CSV is moved to
`~/.finnamon/downloads/imported/`, where any older than 30 days is deleted at the end of the next import (the rows
themselves stay in the database).

**When the bank refuses the login.** **Reset browser profile** in the same window (or `finnamon import --browser
--reset-profile`) moves Finnamon's profile aside so the next fetch starts fresh; the bank sees a new device, and the old
folder (`~/.finnamon/chrome.old-…`) can go once that works. `finnamon import --browser <bank> --diagnose` has you log in
by hand on throwaway profiles, with and without a debugging port, and says which way your answers point: the port, or a
bank that refuses every login for now (wait a day, or use your everyday browser). To use a Chrome you already run with
a debugging port, `finnamon import --browser <bank> --attach` opens one tab there and drives only that tab (only when
you type `--attach`; it puts a debugging port on the browser that holds all your cookies, and HSBC refuses it too).

**Good to know.** Remove an account with its trash icon in that window or `finnamon account remove "HSBC Checking"`
(the bank goes with its last account). Detectors look at the last week by transaction date, so import weekly if you
want alerts from it and not only budgets and net worth. HSBC US shows only about the last six months online, and its
export takes only the rows the page has loaded, so "Show more transactions" decides how far back a first import
reaches; anything older exists only as statement PDFs, which `finnamon import` cannot read.
