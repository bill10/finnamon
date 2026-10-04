"""Turn changelog.d/ fragments into a release. Run by .github/workflows/release.yml once the tests pass on main.

PRs never edit VERSION or CHANGELOG.md, so parallel PRs cannot conflict on them; each adds changelog.d/<branch>.md:

    ---
    bump: patch
    ---
    ### Fixed
    - ...

`python3 scripts/release.py [root]` collects every fragment, in subfolders too (changelog.d/README.md is not one), raises VERSION by the largest bump
among them, adds a "## [X] - date" section to the top of CHANGELOG.md, deletes the fragments and prints the new
version. With no fragments it changes nothing and prints nothing."""

import datetime
import re
import sys
from pathlib import Path

LEVELS = ("major", "minor", "patch", "micro")


def parse_fragment(text: str, name: str = "fragment") -> tuple[str, str]:
    m = re.match(r"---\n(.*?)\n---\n(.*)\Z", text.replace("\r\n", "\n"), re.S)
    bump = m and re.search(r"^bump:\s*(\S+)\s*$", m[1], re.M)
    if not bump or bump[1] not in LEVELS:
        raise ValueError(f'{name}: needs front matter "---\\nbump: {"|".join(LEVELS)}\\n---"')
    if not m[2].strip():
        raise ValueError(f"{name}: has no release notes under its front matter")
    return bump[1], m[2].strip()


def next_version(version: str, bumps) -> str:
    """The largest bump wins and zeroes the parts after it: 0.8.12.2 + patch = 0.8.13.0."""
    parts = version.strip().split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        raise ValueError(f'VERSION must be MAJOR.MINOR.PATCH.MICRO, got "{version.strip()}"')
    i = min(LEVELS.index(b) for b in bumps)
    return ".".join(str(int(n) + 1) if j == i else "0" if j > i else n for j, n in enumerate(parts))


def changelog_with(changelog: str, version: str, date: str, bodies) -> str:
    section = f"## [{version}] - {date}\n\n" + "\n\n".join(bodies) + "\n\n"
    m = re.search(r"^## \[", changelog, re.M)
    return changelog[:m.start()] + section + changelog[m.start():] if m else changelog.rstrip() + "\n\n" + section


def release(root, date: str | None = None) -> str | None:
    root = Path(root)
    d = root / "changelog.d"
    # rglob: a branch named "team/fix-x" writes changelog.d/team/fix-x.md; skipping it would drop its notes silently
    names = sorted(p for p in d.rglob("*.md") if p != d / "README.md")
    if not names:
        return None
    fragments = [parse_fragment(p.read_text(encoding="utf-8"), f"changelog.d/{p.relative_to(d)}") for p in names]
    version = next_version((root / "VERSION").read_text(encoding="utf-8"), [b for b, _ in fragments])
    date = date or datetime.date.today().isoformat()
    log = root / "CHANGELOG.md"
    log.write_text(changelog_with(log.read_text(encoding="utf-8"), version, date, [b for _, b in fragments]), encoding="utf-8")
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    for p in names:
        p.unlink()
    return version


if __name__ == "__main__":
    if (v := release(sys.argv[1] if len(sys.argv) > 1 else ".")):
        print(v)
