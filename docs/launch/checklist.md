# Launch-day checklist

In this order. **Owner** = Bill, in person. **Billion** = the Billion agent, posting from the owner's logged-in browser
after the owner says go; it never logs in or creates accounts, and it stops and asks if a page differs from these notes.
Written 2026-10-08 against v0.56.2.

| # | Step | Who | Needs |
|---|---|---|---|
| 0 | Day-before check | Billion | `finnamon demo` runs on a clean clone; README links resolve; re-read each subreddit's rules, sidebar and pinned posts (Reddit could not be fetched by an agent on 2026-10-08) and fix the drafts if they changed |
| 1 | Make the repo public | Owner (owner's call) | Settings → General → Change visibility. Then, logged out: the README renders, the GIF and screenshots load, and the [demo video](https://github.com/bill10/finnamon/releases/download/pr-assets/finnamon-demo.mp4) plays (404 logged out while the repo is private). Logged out, `git clone https://github.com/bill10/finnamon` works (the docs already use that URL; #113) |
| 2 | GitHub description and topics | Billion, or owner | The text and topics in [one-liners.md](one-liners.md) ("GitHub repo"); the live description still says "Turn Claude into…" with no Codex, and there are no topics. `gh repo edit --description … --add-topic …` or the repo's About gear |
| 3 | Show HN | **Owner only** | The owner writes the title, post and every reply ([HN guidelines](https://news.ycombinator.com/newsguidelines.html), checked 2026-10-08: "Please don't put generated text in HN posts", "Don't post generated text or AI-edited text" in comments; Show HN titles begin "Show HN"). Raw material: [show-hn-facts.md](show-hn-facts.md). Weekday morning US Eastern; stay around for comments |
| 4 | r/selfhosted | Billion posts from the owner's account; owner answers comments | [r-selfhosted.md](r-selfhosted.md). Likely the pinned New Project Megathread (projects under three months old) rather than a standalone post, on whatever day the rules name; the right flair; the AI-use disclosure. Confirm on the live sidebar first |
| 5 | r/ClaudeAI | Billion posts from the owner's account; owner answers comments | [r-claudeai.md](r-claudeai.md), "Built with Claude" flair; a screenshot or the demo, and a prompt if the sidebar asks. No referral links |
| 6 | selfh.st | Billion | [selfhst.md](selfhst.md): https://selfh.st/submit/ → Self-Host Weekly (Newsletter) → Project Launch; AI-assisted: Yes |
| 7 | X | Billion posts from the owner's account | [x-thread.md](x-thread.md), 6 posts; the teaser GIF on post 1, the dashboard screenshot on post 4 |

After each post, note its URL here or on the card. If a post is removed, stop the steps after it and tell the owner.
