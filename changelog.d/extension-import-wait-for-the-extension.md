---
bump: patch
---
### Fixed
- **Fetch by AI waits for the Claude extension to connect.** A freshly opened Finnamon Chrome may leave the extension idle until its icon is clicked; the import now says so, waits up to about two minutes for it (or for the paired browser), and only then falls back to the by-hand export, instead of telling you to run `--pair-extension`.
- **The tab guard reads the extension's quoted Tab Context URLs.** A fresh tab (`"chrome://newtab/"`) no longer trips "off the bank's site", and the attach step opens HSBC's dashboard, which is already logged in.
