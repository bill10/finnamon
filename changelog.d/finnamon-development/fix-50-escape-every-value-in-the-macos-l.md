---
bump: patch
---
### Fixed
- **A path with `&` or `<` no longer breaks the macOS launchd install.** Every value in the generated plist is now XML-escaped (and unescaped when the installed home is read back), so a repo under `R&D/` or a home like `Money & Bills/.finnamon` installs and doesn't report drift forever.
