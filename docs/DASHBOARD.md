# The web dashboard

A local page in Finnamon's own colours: net worth as a figure with its monthly change, a ledger of cash, investments, property and
liabilities with each one's share (click the Property row to state what the house or the car is worth) beside a
3, 6 or 12-month trend, an **Add account** menu (Link account: Plaid Link runs in
the page, the bank's login opens in the same tab, nothing to copy; or Import CSV for a bank Plaid can't reach, a window that adds the manual account and takes its file, with a Fetch by AI option that runs the browser import in its own session, shown as an Import tab in the intercom), budgets as an overall bar plus the categories
that matter (a line under one says what it covers when its name doesn't: several categories, a merchant, or `fixed`) with a **Manage** mode to change, add or remove them (each shows what it counts; its pencil opens a picker of categories
from the taxonomy and merchants the household has seen, and the new-budget row has one too, for a budget whose name is
no category, like "subscriptions" made of its merchants; the Overall bar counts a charge two budgets share once, and an
"Uncategorized (N)" line opens the transactions with no category, an import's mostly, which count toward no budget), the alerts sent to the household lately, or about
to be (open or unsent, each with **It's normal** and **Dismiss**, except a bank that needs a re-login or whose connection
is expiring, which gets **Reconnect** in place of It's normal, as it does beside "needs a new login" in the accounts list:
it opens Plaid's login page for that bank in a new tab, and within a minute of the login Finnamon syncs the bank, resolves
its alert as Reconnected and says so in the chat; resolved ones in a collapsed **Resolved** list saying
how, with **Undo** on the ones you marked normal or dismissed; what triage chose not to mention stays with `finnamon alerts --suppressed`), a chart panel, a light/dark
toggle, and an intercom button in the corner that opens the household's Claude Code session. Ask it "show me
dining by month" and the chart panel re-renders; "make it weekly" re-renders it again. "Dining over $50 this month"
puts a table of those transactions on the same board. Alerts still arrive on
Telegram. A bank linked from the page belongs to the household's first member; use `finnamon link --start
--owner <name>` in a terminal for someone else's login.

`finnamon init` installs it (step 3) and schedules it with the daemon and the heartbeat (a third always-on job:
`com.finnamon.web` on macOS, `finnamon-web.service` on Linux) on http://localhost:8888, and `finnamon open` opens it:
the page needs its key, a random token in `~/.finnamon/web-token` (0600) that `finnamon open` puts in the address once
and the page keeps as a cookie, so the address bar holds none of it (the cookie, like a Jupyter notebook's, is sent to every other service you browse on localhost, so keep the machine's other local pages ones you trust). Without the key the page, its API
and the intercom's socket all answer 401, to a process on this machine as much as to a device on the tailnet: the intercom
is a shell as you. `finnamon web token` prints the key (for `curl -H 'Authorization: Bearer …'`, or `FINNAMON_WEB_TOKEN`
on another computer's `finnamon import --to`), `finnamon web token --rotate` replaces it and locks every open page out until
the next `finnamon open` (the page says so: its controls and the intercom go dark instead of retrying). `finnamon open --print`
prints the address instead of opening a browser, as `finnamon open` itself does over SSH, where a browser would open on the
box's own screen. Skipped it, or Node arrived later?

```
cd web && npm install        # Node 20.12+; node-pty ships a prebuilt binary
finnamon install             # adds the dashboard job next to the daemon and the heartbeat
```

or run it by hand: `node web/server.js` (not both at once: the second cannot take the port and quits). The session
inside the intercom runs in ask mode (`claude --permission-mode default`: allow-listed commands run unasked, the deny list is
refused, anything else, a web search or fetch included, shows Claude Code's Allow / Deny dialog; when a Telegram message
started the turn, the same request goes to the chat with Allow / Deny buttons, and the first answer wins ("don't ask again" lasts until the dashboard restarts); with the Telegram
channel attached it stays `dontAsk` with `--disallowedTools WebSearch WebFetch`, since it then reads the chat with nobody at the page) and resumes the
household's session id from `~/.finnamon/intercom.json`, so a restart costs you the scrollback and not the
conversation (a restart that cut a Telegram reply short tells the chat to resend its last minute), with the Telegram
channel attached when `finnamon channel status` said `channel` at start-up, so the page, the phone and a terminal
share one conversation. `session` gives the same one conversation without the plugin: the daemon keeps reading the bot and
posts each household message to `POST /api/telegram/turn` (bearer key only), which types it into this session as
`[telegram · <owner>] …` and answers with the reply read off the session's transcript (the same reading Talk does); a web
search or fetch asks there as it does at the page, and the chat gets the Allow / Deny buttons too. After `finnamon channel on|session|off` restart it (`finnamon update --no-pull` restarts in place
and the assistant comes back knowing the thread; `finnamon install` also works but starts it fresh), the health
pill says so.

Banks that use OAuth (Chase and friends) log in through a popup; Plaid only accepts an HTTPS return address, so
the page sends none on plain http. Over Tailscale (below) the page is HTTPS and sends its own address as the
return: add `https://<machine>.<tailnet>.ts.net/` under API → Allowed redirect URIs in the Plaid dashboard, and
the bank comes back to the page instead of a popup. The page is a shell on this machine: it listens on localhost only, answers only to
its own address (a page on another site cannot reach it from your browser) and only with its key, and a strict Content-Security-Policy on every response
keeps it running no script but its own and Plaid Link's loader — a bank memo can become chart data, never code. For your phone, run `finnamon remote`:
it finds Tailscale (on PATH or inside the macOS app), checks this machine is logged in to your tailnet, runs `tailscale serve
--bg 8888` (showing Tailscale's "enable Serve" link at once if your tailnet needs it; never Funnel, and it stops if Funnel is on, and never over an HTTPS port
that already serves something else without asking) and adds the machine's ts.net name to `~/.finnamon/web-hosts`, the names the
dashboard answers to besides localhost (read on every request, so nothing restarts; `--dry-run` shows what it would do,
and running it again changes nothing; `--off` takes the names back out and stops serving it; the page itself refuses anything Tailscale marks as Funnel traffic). `finnamon doctor` says whether it is on. Then
It ends with a QR code: scan it with the phone's camera and the dashboard opens there, logged in. The code in it is a one-time pairing code, not the key: it lets in one device, within 5 minutes, only on the tailnet name and never over Funnel, and the page swaps it for the key's cookie (the address is printed under the QR for a phone without a camera). Run `finnamon remote` again for the next phone. `finnamon open --host <machine>.<tailnet>.ts.net` still prints an address with the key itself, to open there once (hand it over privately, never through the household chat: every member and the assistant read that). The page keeps the key as a cookie for 30 days, or until `finnamon web token --rotate`; after that, a fresh QR code. The address bar forgets the key after the redirect; what a browser keeps of an address you typed or pasted is its own, so on a phone paste it once into a private window if that matters to you. The key is
the only thing the tailnet is asked for: a `Tailscale-User-Login` header is not trusted, because to this server a
`tailscale serve` proxy and any other local process both arrive from loopback and either could set it. Never a public port.

**Change category.** A transaction row in any table panel (recent, large, uncategorized, or one the assistant drew that
carries `transaction_id`) opens a picker: this charge only (`finnamon category --tx`) or every charge from its merchant
(the rule, `--tx … --every`). The category column shows the detailed category ("restaurant", not "food and drink"). After a
CSV import with rows that have no category, the page says how many and opens the uncategorized table; nothing is guessed.
