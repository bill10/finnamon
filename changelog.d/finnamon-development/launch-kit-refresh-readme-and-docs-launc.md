---
bump: micro
---
### Changed
- **README and launch kit brought up to v0.56.** The README, INSTALL, COMMANDS and `docs/launch/` now cover Fetch by AI for banks Plaid can't reach (HSBC through the Claude in Chrome extension, on your normal Claude login), what it sends to Anthropic, and that it is Claude Code only. `docs/launch/checklist.md` gives the launch-day order and who does each step.
- **Try it in 30 seconds.** The README opens with `finnamon demo` (first free port from 8890, `--print` to only print the address), and every clone command uses https://github.com/bill10/finnamon.
- **Uninstalling is documented** (INSTALL section 8): Plaid items, the jobs, channel mode, `~/.finnamon`, logs, the `~/.claude.json` trust entry, the command and the checkout.
- **COMMANDS: "Banks Plaid doesn't reach" is its own section**, and the README's sample alerts use the alerts' real wording.
