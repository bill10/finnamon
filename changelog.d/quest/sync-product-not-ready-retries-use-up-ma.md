---
bump: micro
---
### Fixed
- **A brand-new bank connection now waits out Plaid's "not ready yet" answers.** Those waits no longer use up the three restarts reserved for changes mid-pull, so the first pull gets its full retry budget instead of giving up after 3 calls.
