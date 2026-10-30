---
bump: patch
---
### Fixed
- **A re-login alert closes itself as soon as the bank syncs good again.** Before, an open bank-health alert ("<Bank> needs a
  re-login", "hasn't synced since…") closed only during a full sync cycle's checks. So after a Reconnect, the bank could
  read good in `finnamon status` while the alert sat in Recent alerts until the next cycle, hours later. That happened
  after a `finnamon sync`, or when the re-login sync's last Plaid call hit a rate limit. Now every sync that brings a bank
  back closes its alert on the spot as "Synced again" (#48). A CSV import does the same for a manual
  account's "hasn't been imported since" alert.
- **A Reconnect whose sync lands but then hits a Plaid rate limit counts as reconnected.** The chat hears "<bank> is
  reconnected" right away instead of the daemon re-syncing the bank every minute for up to an hour, or dropping the
  login quietly.
