# Launch kit

Copy for the launch, all on the pitch settled 2026-10-03 ([market research](../market-research-2026-10.md#positioning-decided-2026-10-03)).
Claims are dated; re-check them on launch day. Brought up to v0.56.2 (Fetch by AI, the Claude in Chrome extension
import, Codex) on 2026-10-08; live pages re-checked that day are listed in each file. `<repo>` is the public clone URL, set when the public repo exists (#113).

| File | For |
|---|---|
| [checklist.md](checklist.md) | Launch-day order, who does each step, and what each needs |
| [show-hn-facts.md](show-hn-facts.md) | Show HN: facts and answers only. **The post itself is written by the owner** (HN does not allow AI-written posts or comments) |
| [r-selfhosted.md](r-selfhosted.md) | r/selfhosted draft post, with the AI-use disclosure |
| [r-claudeai.md](r-claudeai.md) | r/ClaudeAI post, "Built with Claude" flair |
| [selfhst.md](selfhst.md) | selfh.st newsletter submission |
| [x-thread.md](x-thread.md) | A 6-post thread for X |
| [one-liners.md](one-liners.md) | Headline variants, 140/280-character descriptions, GitHub repo description and topics |

Words: never "watchdog"; privacy is "your data lives on your machine", never "never leaves your machine" (it doesn't:
Plaid, the assistant's provider, Telegram, and the bank pages Fetch by AI reads all leave it).
Fetch by AI: "you log in yourself", "it never types into the page or sees your password", "Claude Code only"; it is on demand, so never
call it a sync. Platforms:
Mac, Linux or Windows via WSL2 (verified in CI by #120). Say "via WSL2": there is no native Windows build.
