# changelog.d

Each PR adds one file here, `changelog.d/<branch-name>.md` (a `/` in the branch name makes a subfolder; that is fine), and
never edits `VERSION` or `CHANGELOG.md`:

```markdown
---
bump: patch
---
### Fixed
- **What changed, in bold.** Why it matters to the household and how to use it.
```

`bump` is `major`, `minor`, `patch` or `micro`, the part of `VERSION` (`MAJOR.MINOR.PATCH.MICRO`) it raises. Once the
tests pass on main, `.github/workflows/release.yml` runs `scripts/release.py`: the largest bump among the fragments
wins, their notes become the next `## [X] - date` section of `CHANGELOG.md`, the fragments are deleted, and the result
is committed to main as `vX release: <headline>` and tagged `vX`. See `docs/DEVELOPMENT.md`, "Releases".
