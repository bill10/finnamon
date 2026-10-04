"""scripts/release.py: changelog.d/ fragments -> VERSION and a CHANGELOG.md section (run by .github/workflows/release.yml)."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("release", Path(__file__).resolve().parent.parent / "scripts" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

LOG = "# Changelog\n\nIntro.\n\n## [0.8.12.2] - 2026-09-28\n\n### Added\n- CI.\n"


def _repo(tmp_path, fragments, version="0.8.12.2\n"):
    (tmp_path / "VERSION").write_text(version)
    (tmp_path / "CHANGELOG.md").write_text(LOG)
    d = tmp_path / "changelog.d"; d.mkdir()
    (d / "README.md").write_text("# changelog.d\n\nnot a fragment\n")
    for name, text in fragments.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text)
    return tmp_path


def test_the_largest_bump_wins_and_zeroes_the_parts_after_it():
    assert release.next_version("0.8.12.2\n", ["micro"]) == "0.8.12.3"
    assert release.next_version("0.8.12.2", ["micro", "patch"]) == "0.8.13.0"
    assert release.next_version("0.8.12.2", ["patch", "minor", "micro"]) == "0.9.0.0"
    assert release.next_version("0.8.12.2", ["major"]) == "1.0.0.0"
    assert release.next_version("0.9.9.9", ["micro"]) == "0.9.9.10"
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH.MICRO"):
        release.next_version("0.8.12", ["micro"])


def test_a_fragment_needs_a_known_bump_and_notes():
    assert release.parse_fragment("---\r\nbump: minor\r\n---\r\n### Added\r\n- x\r\n") == ("minor", "### Added\n- x")
    for bad in ("### Added\n- x\n", "---\nbump: huge\n---\n- x\n", "---\nbump: patch\n---\n\n"):
        with pytest.raises(ValueError, match="changelog.d/x.md"):
            release.parse_fragment(bad, "changelog.d/x.md")


def test_release_merges_every_fragment_into_one_dated_section(tmp_path):
    root = _repo(tmp_path, {"b-fix.md": "---\nbump: micro\n---\n### Fixed\n- B.\n",
                            "a-feat.md": "---\nbump: minor\n---\n### Added\n- A.\n"})
    assert release.release(root, "2026-09-29") == "0.9.0.0"
    assert (root / "VERSION").read_text() == "0.9.0.0\n"
    assert (root / "CHANGELOG.md").read_text() == (
        "# Changelog\n\nIntro.\n\n## [0.9.0.0] - 2026-09-29\n\n### Added\n- A.\n\n### Fixed\n- B.\n\n"
        "## [0.8.12.2] - 2026-09-28\n\n### Added\n- CI.\n")
    assert [p.name for p in (root / "changelog.d").iterdir()] == ["README.md"], "fragments go, the README stays"


def test_no_fragments_is_a_no_op(tmp_path):
    root = _repo(tmp_path, {})
    assert release.release(root) is None
    assert (root / "VERSION").read_text() == "0.8.12.2\n" and (root / "CHANGELOG.md").read_text() == LOG


def test_a_bad_fragment_changes_nothing(tmp_path):
    root = _repo(tmp_path, {"good.md": "---\nbump: micro\n---\n- ok\n", "bad.md": "no front matter\n"})
    with pytest.raises(ValueError, match="bad.md"):
        release.release(root)
    assert (root / "VERSION").read_text() == "0.8.12.2\n" and (root / "changelog.d" / "good.md").exists()


def test_a_changelog_with_no_sections_gets_one_at_the_end():
    assert release.changelog_with("# Changelog\n", "0.1.0.0", "2026-01-02", ["- x"]) == "# Changelog\n\n## [0.1.0.0] - 2026-01-02\n\n- x\n\n"


def test_the_checked_in_fragments_parse():
    """A malformed fragment would only fail after merge, in the release workflow; catch it on the PR."""
    d = Path(__file__).resolve().parent.parent / "changelog.d"
    for p in sorted(d.rglob("*.md")):
        if p != d / "README.md":
            release.parse_fragment(p.read_text(encoding="utf-8"), p.name)


def test_the_command_line_prints_only_a_new_version(tmp_path):
    """The workflow reads stdout as the version and treats empty as "no release": nothing else may be printed."""
    import subprocess, sys
    script = Path(__file__).resolve().parent.parent / "scripts" / "release.py"
    root = _repo(tmp_path, {})
    run = lambda: subprocess.run([sys.executable, str(script), str(root)], capture_output=True, text=True, check=True).stdout
    assert run() == ""
    (root / "changelog.d" / "x.md").write_text("---\nbump: patch\n---\n- x\n")
    assert run() == "0.8.13.0\n"


def test_the_release_headline_drops_the_version_type_and_pr_number():
    """release.yml's sed turns the merge subject into the release commit's headline."""
    import re, subprocess
    yml = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml").read_text()
    expr = re.search(r"sed -E '([^']*)'", yml)[1]
    sed = lambda s: subprocess.run(["sed", "-E", expr], input=s + "\n", capture_output=True, text=True, check=True).stdout.strip()
    assert sed("v0.8.12.2 ci: run pytest on every PR (#60)") == "run pytest on every PR"
    assert sed("feat(web)!: a page (#7)") == "a page"
    assert sed("Merge pull request #9 from x/y") == "Merge pull request #9 from x/y"


def test_a_fragment_in_a_subfolder_is_released_and_named_by_its_path(tmp_path):
    """A branch named team/fix-x writes changelog.d/team/fix-x.md; its notes must not be dropped."""
    root = _repo(tmp_path, {})
    (root / "changelog.d" / "team").mkdir()
    (root / "changelog.d" / "team" / "fix-x.md").write_text("---\nbump: micro\n---\n- X.\n")
    assert release.release(root, "2026-09-29") == "0.8.12.3"
    assert "- X." in (root / "CHANGELOG.md").read_text() and not (root / "changelog.d" / "team" / "fix-x.md").exists()
    (root / "changelog.d" / "team" / "bad.md").write_text("no front matter\n")
    with pytest.raises(ValueError, match="changelog.d/team/bad.md"):
        release.release(root)
