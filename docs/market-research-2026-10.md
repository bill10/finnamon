# Market research, October 2026

Gathered 2026-10-03 for the launch (#110). It set the positioning: **a self-hosted finance watchdog for households,
run by your own Claude.** Claims are dated; re-check them before quoting them anywhere new.

## The market moved

- **ChatGPT Finances went free in the US on 2026-10-02** (OpenAI release notes). It syncs through Plaid, answers
  questions and draws charts; its only proactive alerts are for credit score; single user; closed.
- So **Q&A over your money is commoditised.** Asking a chatbot "what did we spend on dining" is no longer a reason
  to install anything.

## Finnamon's wedge

1. **A proactive, triaged watchdog**: it messages only when something needs a human, and stays quiet otherwise.
2. **The household's own database and keys**: SQLite on their machine, their Plaid keys, their Claude login.
3. **Household-native**: one chat, one set of budgets, joint accounts counted once.
4. **Hackable**: detectors are SQL files; the assistant is Claude Code with a skill and an allow list.

## Competitors (as of 2026-10)

| | Where data lives | Bank sync | Cost | Proactive alerts | AI | Household | Source |
|---|---|---|---|---|---|---|---|
| ChatGPT Finances (OpenAI) | OpenAI cloud | Plaid | $0 in the US since 2026-10-02 | Credit score only | Yes | Single user | Closed |
| Monarch | Cloud | Built in | $99.99/yr | Rule-based push alerts, weekly recap | Yes | Yes | Closed |
| Actual Budget | Self-hosted | SimpleFIN (US), GoCardless (EU) | Free | None | None | | MIT |
| Firefly III | Self-hosted | Separate importer | Free | Rules, bill reminders | None | | AGPL |
| Maybe | | | | | | | Archived 2025-07-24 |

Self-hosters' recurring complaint about Actual/Firefly: "It won't notify you" (HN).

## First 100 users

1. Claude Pro/Max subscribers who already use Claude Code, in the US or Canada, with an always-on Mac.
2. Then Actual Budget and Firefly III users who want alerts.

## Risks

- **Plaid Trial is 10 Items** (bank logins). Enough for most households; a heavy one hits the ceiling, and the next
  tier is paid.
- **Anthropic's consumer data use**: with "Help improve Claude" on, chats are kept 5 years instead of 30 days, and
  Finnamon's triage and chat send merchant names and amounts through Claude. The README tells users to turn it off.
- **Claude Money**: an unannounced feature seen in Claude iOS builds. If Anthropic ships its own finance product,
  the "run by your own Claude" pitch competes with the platform.

## Sources

- TechCrunch, 2026-05-15
- 9to5Mac, 2026-06-30
- OpenAI release notes, 2026-10-02 (ChatGPT Finances free in the US)
- Hacker News item 48150723
- Hacker News item 42602900
- Plaid changelog and billing docs (Trial plan: 10 Items, US/CA teams created on or after 2026-04-15): https://plaid.com/docs/account/billing/
- TechCrunch, 2025-08-28 (Anthropic consumer data use and retention)
- Monarch winter release, 2025-12-18
- actualbudget.org bank-sync docs
- github.com/maybe-finance/maybe (archived 2025-07-24)

## Positioning decided 2026-10-03

The owner settled the pitch, and it replaces the "watchdog" positioning above. Every piece of marketing uses it
(README, `docs/launch/`, the video end card, package descriptions). Copy for each channel is in `docs/launch/`.

- **Headline:** Your money. Your financial AI.
- **Line under it:** Turn Claude into your personal finance assistant.
- **Subline:** Like Mint, but it warns you when something's off, sorts your transactions with AI, and draws any chart
  you ask for. On your own computer, with no ads and no data selling, so nobody can shut it down. ("Your own
  computer (Mac, Linux or Windows via WSL2)" where platforms matter; no native Windows build.)
- **Three promises:** 1) It watches for you: it messages you only when something needs you (a duplicate charge, a
  new subscription, a budget about to go over). 2) A dashboard that's yours: ask "dining by month" or "everything
  over $500" and Claude draws it, whenever you want. 3) Yours to keep: your data lives on your machine, with your
  own keys.
- **Story:** the owner loved Mint, Mint was shut down, nothing else fit; Mint rarely flagged problems (so: alerts)
  and predated AI (so: AI categorisation and Q&A).
- **Words:** "watchdog" is retired. Privacy is "your data lives on your machine", never "never leaves your machine";
  the README's "What leaves your machine" section stays.
