# r/selfhosted draft

**Before posting, re-read the subreddit's rules and sidebar on the day**: as of this writing, new-project posts are
only allowed on the weekly day the mods set for them, must use the matching flair, and must disclose how AI was used
to build the project. Post from the owner's account, answer comments personally.

Flair: the new-project flair the rules name. Link: `<repo>`.

---

**Title:** Finnamon: a self-hosted personal finance assistant (Plaid → SQLite on your machine, alerts to Telegram, Claude for the AI)

I loved Mint: one clean place for all my accounts and transactions, no ads. Then it was shut down, and nothing else
fit, so I built my own. Mint rarely told me when something was wrong, so I added alerts. And Mint came before AI, so I
recategorized a lot by hand. Now the AI does it, and I can just ask how we're doing.

**What it does**

- **It watches for you.** It syncs your accounts every 6 hours and messages you on Telegram only when something needs
  you: a duplicate charge, a new subscription, a budget about to go over. Most days it says nothing; Sunday you get a
  short roundup.
- **A dashboard that's yours.** A local web dashboard (accounts, budgets, net worth). Ask "dining by month" or
  "everything over $500" and Claude draws it.
- **Yours to keep.** Your data lives on your machine, in one SQLite file, with your own keys (your Plaid keys, your
  Claude login). No Finnamon server, no telemetry, no account.

**Stack:** Python daemon (launchd on Mac, systemd on Linux and WSL2), SQLite, detectors are plain `.sql` files, Node dashboard,
Telegram bot, Claude Code as the assistant. Phone access over Tailscale.

**Try it without any keys:** `finnamon demo` brings up a made-up household (four banks, six months of transactions)
and its dashboard on a spare port.

**What leaves your machine** (I'd rather you hear it from me): transactions come from your banks through Plaid on
your keys; whatever Claude reads to triage or answer goes to Anthropic under your Claude account's terms (turn off
"Help improve Claude"); Telegram carries the alerts. Bank passwords, full account numbers and keys stay local.

**Requirements and limits:** Mac, Linux or Windows via WSL2 (no native Windows), a computer that stays on, a Claude Pro or Max subscription, banks in the
US or Canada for Plaid's free Trial plan (10 bank logins; CSV import for the rest). It's for people comfortable in a
terminal. Licence: MIT.

**AI disclosure:** Finnamon was built with heavy use of Claude Code (Anthropic): most of the code was written by
Claude under my direction and review, with a test suite (unit tests plus evals that run real Claude over a fixture
household) gating every change. At runtime, the assistant itself is Claude Code; the alert rules are plain SQL and
use no AI.

Repo and 1-minute demo video: `<repo>`
