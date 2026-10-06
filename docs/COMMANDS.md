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
| a bank Plaid doesn't reach (HSBC US personal banking shows in Plaid's search and then says "not supported") | **Add account → Import CSV** on the dashboard opens a window that adds the account (a name, the bank, and what kind it is; one row per account, so HSBC Checking and HSBC Savings both sit under the one HSBC) and then takes the bank's CSV export. In a terminal that first step is `finnamon account add "HSBC Checking" --institution HSBC` (or ask the assistant). Feeding it the export: pick the file in that window, or **Fetch by AI** (a separate Claude session opens a browser window, you log in, it downloads and imports, and you watch it in the panel's Import tab, and its Stop button ends it; it needs the page open on the box itself, elsewhere the panel says what to run instead), `finnamon import "HSBC Checking" <file.csv>`, or `finnamon import --browser hsbc` in a terminal (on another computer with a checkout, add `--to https://<the box's dashboard>` to either form, with `FINNAMON_WEB_TOKEN` set to what `finnamon web token` prints on the box, and the file is uploaded there). The browser fetch needs Google Chrome installed (set `FINNAMON_CHROME` to its executable if it lives somewhere unusual); it runs on a Chrome profile of Finnamon's own, `~/.finnamon/chrome/`, so the bank remembers the device between runs and your everyday profile stays out of it, and you log in yourself with nothing attached to the window until you say you're in. **Fetch by AI fails with "reference: EAC"?** Nobody outside HSBC knows exactly what it means. Suspects, likeliest first: the debugging port Finnamon opens its window with (a fresh Chrome on a new profile with no port has logged in fine), Finnamon's own profile reading as a new device, or a Chrome build its risk engine hasn't seen yet (for a few days after each Chrome update). `finnamon import --browser hsbc --diagnose` tells them apart: you log in by hand twice, without the port and then with it, and it says which suspect your answers point to. For the build, compare the build your everyday browser runs in memory with the one on disk: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --version` vs the Versions/ path in the running helpers from `ps`; the new build only affects newly launched windows). `finnamon import --browser hsbc` and `finnamon doctor` say so themselves when they can tell. Use your everyday browser to download the CSV and run `finnamon import "HSBC Checking" <file.csv>` instead; or fetch in your everyday browser: start Chrome with a debugging port (`open -a "Google Chrome" --args --remote-debugging-port=9222`; Chrome 136 and later ignore the port on the default profile folder, so it has to run on its own `--user-data-dir`) and run `finnamon import --browser hsbc --attach` (Fetch by AI does this by itself whenever a Chrome answers on port 9222): it opens one tab in that browser, you log in there, and the session drives that tab only and closes it at the end. That uses your everyday profile, so the bank sees the build and the device it knows, and the trade-off is a debugging port open on the browser that holds all your cookies, for as long as it runs. Or retry Fetch by AI in a few days. Removing one account is its trash icon in that window or `finnamon account remove "HSBC Checking"` (the bank goes with its last account; remove each account the same way). Detectors look at the last week by transaction date, so import weekly if you want its alerts and not only its budgets and net worth. HSBC US only shows about the last six months of transactions online, whatever date range you ask it for, and the export only takes rows the page has already loaded, so "Show more transactions" is what decides how far back a first import reaches; anything older exists only as statement PDFs, which `finnamon import` cannot read |
| pick up new code after `git pull` | `finnamon update` (pulls, applies migrations, rewrites `~/.finnamon/assistant/` from the release, restarts only what went stale, and the assistant carries on: it resumes its session by id rather than starting a new one. `--no-pull` adopts a tree you pulled yourself, `--dry-run` says what it would restart, `--check` prints what a pull would bring as JSON). The dashboard shows "Update available" in its header when there is something to pull, and its Update button runs the same `finnamon update` |
| reprovision the services | `finnamon install` (rewrites the unit files and reloads every job, and retires the household's conversation so the assistant starts fresh; it asks first, and `finnamon update` is the one that keeps it. Needed when a release changes the units, which `update` detects and tells you.) |

Household, not personal: one bot, one chat, one memory, one set of budgets. Everyone in the chat
sees everything. Joint accounts linked from two logins are detected and counted once.
