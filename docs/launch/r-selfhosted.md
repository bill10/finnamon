# r/selfhosted draft

**Before posting, re-read the subreddit's rules, sidebar and pinned posts on the day.** The 2026-10-05 notes said
new-project posts are only allowed on the weekly day the mods set for them, must use the matching flair, and must
disclose how AI was used to build the project. Re-check on 2026-10-08: Reddit refuses this agent's fetches (403), so
the live rules were **not** read. A third-party list dated August 2026 says projects younger than three months (from
their first public commit or post) go only in the pinned **New Project Megathread**. Finnamon's repo goes public on
launch day, so expect this to be a megathread entry, not a standalone post: use the text below as the entry, trimmed
if the megathread asks for a template. Post from the owner's account, answer comments personally.

Flair: the new-project flair the rules name. Link: `<repo>`.

---

**Title:** Finnamon: a self-hosted personal finance assistant (Plaid → SQLite on your machine, alerts to Telegram, Claude or Codex for the AI)

I loved Mint: one clean place for all my accounts and transactions, no ads. Then it was shut down, and nothing else
fit, so I built my own. Mint rarely told me when something was wrong, so I added alerts. And Mint came before AI, so I
recategorized a lot by hand. Now the AI does it, and I can just ask how we're doing.

**What it does**

- **It watches for you.** It syncs your accounts every 6 hours and messages you on Telegram only when something needs
  you: a duplicate charge, a new subscription, a budget about to go over. Most days it says nothing; Sunday you get a
  short roundup.
- **A dashboard that's yours.** A local web dashboard (accounts, budgets, net worth). Ask "dining by month" or
  "everything over $500" and the assistant draws it.
- **Yours to keep.** Your data lives on your machine, in one SQLite file, with your own keys (your Plaid keys, your
  Claude or Codex login). No Finnamon server, no telemetry, no account.
- **Bank not on Plaid?** "Fetch by AI": you log in to the bank yourself in a Chrome window of Finnamon's own, and Claude
  clicks through to the CSV export and imports it. It never types into the page or runs scripts, and never sees
  your password; on HSBC a guard also holds it to the bank's own site. I use it for HSBC US, which Plaid doesn't support. Or import the CSV by hand.

**Stack:** Python daemon (launchd on Mac, systemd on Linux and WSL2), SQLite, detectors are plain `.sql` files, Node dashboard,
Telegram bot, Claude Code or OpenAI's Codex CLI as the assistant (web lookups and "Fetch by AI" are Claude-only for now). Phone access over Tailscale.

**Try it without any keys:** `finnamon demo` brings up a made-up household (four banks, six months of transactions)
and its dashboard on a spare port.

**What leaves your machine** (I'd rather you hear it from me): transactions come from your banks through Plaid on
your keys; whatever the assistant reads to triage or answer goes to Anthropic under your Claude account's terms (turn off
"Help improve Claude"), or to OpenAI on Codex (turn off ChatGPT's "Improve the model for everyone"). Fetch by AI is
Claude-only, so the bank pages it reads after you log in go to Anthropic; Telegram carries the alerts. Bank passwords, full account numbers and keys stay local.

**Requirements and limits:** Mac, Linux or Windows via WSL2 (no native Windows), a computer that stays on, a Claude Pro or Max subscription or a ChatGPT Plus plan or higher (Codex), banks in the
US or Canada for Plaid's free Trial plan (10 bank logins; Fetch by AI or CSV import for the rest). It's for people comfortable in a
terminal. Licence: MIT.

**AI disclosure:** Finnamon was built with heavy use of Claude Code (Anthropic): most of the code was written by
Claude under my direction and review, with a test suite (unit tests plus evals that run real Claude over a fixture
household) gating every change. At runtime, the assistant itself is Claude Code (or Codex, if you pick it); the alert rules are plain SQL and
use no AI.

Repo and 1-minute demo video: `<repo>`
