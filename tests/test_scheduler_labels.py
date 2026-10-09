"""Job names per FINNAMON_HOME: the household's keep today's names, any other home (a test, a second install) gets its
own, so it can never list, replace or remove the household's jobs. launchctl, systemctl and loginctl are all faked."""
from types import SimpleNamespace

import pytest

from finnamon import cli, scheduler
from tests.conftest import REAL_LOG_DIR, REAL_UNIT_DIR


@pytest.fixture
def argv(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler.subprocess, "run", lambda a, **kw: calls.append(a) or SimpleNamespace(returncode=0, stdout="yes\n", stderr=""))
    monkeypatch.setattr(scheduler.os, "getuid", lambda: 503)
    monkeypatch.setattr(scheduler, "_launchd_reload", lambda domain, lb, p: calls.append(["reload", lb]))
    monkeypatch.setattr(scheduler, "web_args", lambda: None)
    return calls


def test_the_households_names_are_pinned(household_home, monkeypatch):
    monkeypatch.setattr(scheduler, "web_args", lambda: None)   # a checkout with web/node_modules would add the web unit
    assert scheduler.suffix("Darwin") == scheduler.suffix("Linux") == ""
    assert scheduler.label("daemon") == "com.finnamon.daemon" and scheduler.unit("heartbeat", "timer") == "finnamon-heartbeat.timer"
    assert sorted(scheduler.render("Darwin")) == ["com.finnamon.daemon.plist", "com.finnamon.heartbeat.plist"]
    assert sorted(scheduler.render("Linux")) == ["finnamon-daemon.service", "finnamon-heartbeat.service", "finnamon-heartbeat.timer"]
    assert "<key>Label</key><string>com.finnamon.daemon</string>" in scheduler.render("Darwin")["com.finnamon.daemon.plist"]


def test_another_home_gets_its_own_names_and_logs(home, tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler.Path, "home", lambda: tmp_path)
    sfx = scheduler.suffix("Darwin")
    assert sfx.startswith("-") and len(sfx) == 9
    assert scheduler.label("daemon") == f"com.finnamon{sfx}.daemon" and scheduler.unit("daemon") == f"finnamon{sfx}-daemon.service"
    assert "com.finnamon.daemon.plist" not in scheduler.render("Darwin") and "finnamon-daemon.service" not in scheduler.render("Linux")
    assert REAL_LOG_DIR() == tmp_path / "Library" / "Logs" / f"finnamon{sfx}"
    monkeypatch.setenv("FINNAMON_HOME", str(home / "other"))
    assert scheduler.suffix("Darwin") != sfx, "two scratch homes do not share jobs either"


def test_restart_under_another_home_bounces_its_own_jobs(home, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler.subprocess, "run", lambda a, **kw: calls.append(a) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(scheduler, "_not_running", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.os, "getuid", lambda: 503)
    sfx = scheduler.suffix("Darwin")
    scheduler.restart(["daemon"], "Darwin"); scheduler.restart(["web"], "Linux")
    assert calls == [["launchctl", "kickstart", "-k", f"gui/503/com.finnamon{sfx}.daemon"], ["systemctl", "--user", "restart", f"finnamon{sfx}-web.service"]]


@pytest.mark.parametrize("osn", ["Darwin", "Linux"])
def test_a_home_already_installed_under_the_plain_names_keeps_them(home, tmp_path, monkeypatch, osn):
    """A box that set FINNAMON_HOME before per-home names existed: renaming would start a second daemon beside its first.
    The unit was written with whatever spelling the home had then (here a symlink to it)."""
    link = tmp_path / "link"; link.symlink_to(home)
    hashed = scheduler.suffix(osn)
    assert hashed
    with monkeypatch.context() as m:
        m.setattr(scheduler, "suffix", lambda os_name=None: "")
        m.setenv("FINNAMON_HOME", str(link))
        plain = scheduler.render(osn)
    d = scheduler.unit_dir(osn); d.mkdir(exist_ok=True)
    for name, body in plain.items():
        (d / name).write_text(body)
    assert scheduler.suffix(osn) == ""
    monkeypatch.setenv("FINNAMON_HOME", str(home / "other"))
    assert scheduler.suffix(osn) != "", "another home's FINNAMON_HOME in the plain units does not hand this one their names"


@pytest.mark.parametrize("osn", ["Darwin", "Linux"])
def test_install_and_uninstall_under_a_scratch_home_never_touch_the_households(household_home, tmp_path, monkeypatch, argv, osn):
    monkeypatch.setattr(scheduler, "unit_dir", REAL_UNIT_DIR)
    scheduler.install(osn)   # the household's jobs, into the scratch ~
    theirs = {p.name: p.read_text() for p in scheduler.unit_dir(osn).iterdir()}
    argv.clear()
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path / "scratch"))
    mine = set(scheduler.install(osn))
    assert {p.name: p.read_text() for p in scheduler.unit_dir(osn).iterdir() if p.name in theirs} == theirs
    assert not {n for n in theirs if str(scheduler.unit_dir(osn) / n) in mine}
    scheduler.uninstall(osn)
    assert sorted(p.name for p in scheduler.unit_dir(osn).iterdir()) == sorted(theirs)
    touched = " ".join(" ".join(a) for a in argv)
    assert "com.finnamon.daemon" not in touched and "finnamon-daemon.service" not in touched and "finnamon-web.service" not in touched


def test_status_lists_only_this_homes_jobs(home, monkeypatch):
    listing = "PID\tStatus\tLabel\n123\t0\tcom.finnamon.daemon\n456\t0\tcom.finnamon-deadbeef.daemon\n"
    monkeypatch.setattr(scheduler.subprocess, "run", lambda a, **kw: SimpleNamespace(returncode=0, stdout=listing, stderr=""))
    assert scheduler.status("Darwin") == "not loaded"
    monkeypatch.setattr(scheduler, "suffix", lambda os_name=None: "")
    assert scheduler.status("Darwin") == "123\t0\tcom.finnamon.daemon"


def test_linux_install_says_when_linger_is_off(home, monkeypatch, capsys):
    monkeypatch.setattr(scheduler, "web_args", lambda: None)
    monkeypatch.setattr(scheduler.subprocess, "run", lambda a, **kw: SimpleNamespace(
        returncode=1 if a[:2] == ["loginctl", "enable-linger"] else 0, stdout="no\n", stderr="Access denied" if a[0] == "loginctl" else ""))
    monkeypatch.setenv("USER", "bill")
    scheduler.install("Linux")
    err = capsys.readouterr().err
    assert "linger is off for bill" in err and "Access denied" in err and "sudo loginctl enable-linger bill" in err
    assert scheduler.linger_problem(run=lambda a, **kw: SimpleNamespace(returncode=0, stdout="yes\n", stderr="")) is None
    assert "cannot check linger for bill (Failed to get user)" in scheduler.linger_problem(run=lambda a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="Failed to get user\n"))
    def boom(*a, **kw): raise FileNotFoundError("loginctl")
    assert "cannot check linger" in scheduler.linger_problem(run=boom)
    monkeypatch.delenv("USER")
    assert scheduler.login_user() == scheduler.pwd.getpwuid(scheduler.os.getuid()).pw_name


def test_doctor_reports_linger_on_linux(home, fake_claude, monkeypatch, capsys):
    d = scheduler.unit_dir("Linux"); d.mkdir(exist_ok=True)
    for name, body in scheduler.render("Linux").items():
        (d / name).write_text(body)
    monkeypatch.setattr(cli.platform, "system", lambda: "Linux")
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")
    monkeypatch.setattr(scheduler, "linger_problem", lambda run=None: "linger is off for bill, so the services stop when you log out")
    with pytest.raises(SystemExit):
        cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "✗ Linger: linger is off for bill" in out and "fix: sudo loginctl enable-linger" in out


def test_a_scratch_HOME_named_dot_finnamon_is_not_the_household(tmp_path, monkeypatch):
    """The QA incident: HOME=<scratch>, FINNAMON_HOME=$HOME/.finnamon took the plain names and replaced the real jobs.
    The household is ~/.finnamon of the login (the password database), not of $HOME."""
    monkeypatch.setattr(scheduler, "real_home", lambda: tmp_path / "real-user")
    monkeypatch.setattr(scheduler.Path, "home", lambda: tmp_path / "scratch")
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path / "scratch" / ".finnamon"))
    assert scheduler.suffix("Darwin") and scheduler.label("daemon") != "com.finnamon.daemon"
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path / "real-user" / ".finnamon"))
    assert scheduler.suffix("Darwin") == ""


@pytest.mark.parametrize("osn", ["Darwin", "Linux"])
def test_install_and_uninstall_refuse_jobs_that_belong_to_another_home(household_home, tmp_path, monkeypatch, argv, osn):
    monkeypatch.setattr(scheduler, "unit_dir", REAL_UNIT_DIR)
    with monkeypatch.context() as m:   # the plain names, written for some other home (the reverse of the incident)
        m.setenv("FINNAMON_HOME", str(tmp_path / "elsewhere"))
        m.setattr(scheduler, "suffix", lambda os_name=None: "")
        scheduler.install(osn)
    theirs = {p.name: p.read_text() for p in scheduler.unit_dir(osn).iterdir()}
    argv.clear()
    for f in (lambda: scheduler.install(osn), lambda: scheduler.uninstall(osn), lambda: scheduler.uninstall(osn, dry_run=True)):
        with pytest.raises(RuntimeError, match="belongs to another Finnamon home.*elsewhere.*--force"):
            f()
    assert argv == [] and {p.name: p.read_text() for p in scheduler.unit_dir(osn).iterdir()} == theirs
    scheduler.install(osn, force=True)
    assert all(str(household_home) in p.read_text() for p in scheduler.unit_dir(osn).iterdir() if not p.name.endswith(".timer"))


@pytest.mark.parametrize("osn", ["Darwin", "Linux"])
def test_uninstall_dry_run_names_the_units_and_removes_nothing(household_home, monkeypatch, argv, osn):
    monkeypatch.setattr(scheduler, "unit_dir", REAL_UNIT_DIR)
    scheduler.install(osn)
    before = sorted(p.name for p in scheduler.unit_dir(osn).iterdir())
    argv.clear()
    named = scheduler.uninstall(osn, dry_run=True)
    assert sorted(cli.Path(n).name for n in named) == before and argv == []
    assert sorted(p.name for p in scheduler.unit_dir(osn).iterdir()) == before
    assert sorted(cli.Path(n).name for n in scheduler.uninstall(osn)) == before and not list(scheduler.unit_dir(osn).iterdir())


def test_cli_uninstall_dry_run(home, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(scheduler, "uninstall", lambda os_name=None, dry_run=False, force=False: seen.append((dry_run, force)) or ["/u/a.plist"])
    cli.main(["install", "--uninstall", "--dry-run"])
    assert capsys.readouterr().out.strip() == "would remove /u/a.plist" and seen == [(True, False)]
    monkeypatch.setattr(scheduler, "uninstall", lambda **k: (_ for _ in ()).throw(RuntimeError("x belongs to another Finnamon home")))
    with pytest.raises(SystemExit):
        cli.main(["install", "--uninstall"])
    assert "belongs to another Finnamon home" in capsys.readouterr().err


def test_a_HOME_other_than_the_passwd_one_keeps_its_plain_jobs(tmp_path, monkeypatch):
    """A person who runs with HOME set elsewhere installed the plain names under that HOME: they stay theirs, and a scratch
    HOME's files never land in the login's own LaunchAgents."""
    monkeypatch.setattr(scheduler, "unit_dir", REAL_UNIT_DIR)
    monkeypatch.setattr(scheduler, "real_home", lambda: tmp_path / "passwd-home")
    monkeypatch.setattr(scheduler.Path, "home", lambda: tmp_path / "their-home")
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path / "their-home" / ".finnamon"))
    assert scheduler.unit_dir("Darwin") == tmp_path / "their-home" / "Library" / "LaunchAgents"
    with monkeypatch.context() as m:
        m.setattr(scheduler, "suffix", lambda os_name=None: "")
        plain = scheduler.render("Darwin")
    d = scheduler.unit_dir("Darwin"); d.mkdir(parents=True)
    for name, body in plain.items():
        (d / name).write_text(body)
    assert scheduler.suffix("Darwin") == "" and scheduler.foreign_home("Darwin") is None


def test_the_households_home_from_a_scratch_HOME_is_refused(tmp_path, monkeypatch, argv):
    """FINNAMON_HOME is the real ~/.finnamon but HOME is a scratch dir: the plain names, with HOME=<scratch> baked in."""
    monkeypatch.setattr(scheduler, "real_home", lambda: tmp_path / "real-user")
    monkeypatch.setattr(scheduler.Path, "home", lambda: tmp_path / "scratch")
    monkeypatch.setenv("FINNAMON_HOME", str(tmp_path / "real-user" / ".finnamon"))
    for f in (lambda: scheduler.install("Darwin"), lambda: scheduler.uninstall("Darwin")):
        with pytest.raises(RuntimeError, match="not your login's"):
            f()
    assert argv == []
    scheduler.install("Darwin", force=True)
