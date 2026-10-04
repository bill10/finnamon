"""Weekly backups, macOS log rotation, and detectors reading outside the write lock."""
import sqlite3
import stat

from finnamon import backup, config, detect, scheduler, store

from .conftest import AS_OF, seed


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_backup_is_made_0600_in_a_0700_dir_and_pruned(conn, monkeypatch):
    seed(conn)
    d = backup.backup_dir()
    d.mkdir()
    for i in range(1, 10):   # nine older copies
        (d / f"finnamon-2026-01-0{i}.db").write_text("old")
    dest = backup.maybe(conn)
    assert dest and dest.exists() and mode(dest) == 0o600 and mode(d) == 0o700
    copy = sqlite3.connect(dest)
    assert copy.execute("SELECT count(*) FROM accounts").fetchone()[0] == 2
    kept = backup.backups()
    assert len(kept) == backup.KEEP and kept[-1] == dest and kept[0].name == "finnamon-2026-01-03.db"
    assert store.get_state(conn, "last_backup_at") and store.get_state(conn, "backup_error") is None
    assert backup.maybe(conn) is None   # a week has not passed
    store.set_state(conn, "last_backup_at", "2020-01-01 00:00:00")
    assert backup.maybe(conn) == dest   # same day: replaced, not duplicated
    assert len(backup.backups()) == backup.KEEP


def test_failed_backup_is_recorded_and_never_raises(conn, monkeypatch):
    config.home().joinpath("backups").write_text("a file where the directory should be")
    assert backup.maybe(conn) is None
    assert store.get_state(conn, "backup_error") and store.get_state(conn, "last_backup_at") is None
    config.home().joinpath("backups").unlink()
    assert backup.maybe(conn) is None, "a recent failure waits RETRY_HOURS"
    store.set_state(conn, "backup_error", "2020-01-01 00:00:00 OSError: disk full")
    assert backup.maybe(conn) and store.get_state(conn, "backup_error") is None


def test_cycle_backs_up(conn, monkeypatch):
    from finnamon import run
    monkeypatch.setattr(run.sync, "sync_all", lambda c: [])
    run.cycle(conn, do_triage=False, do_notify=False)
    assert len(backup.backups()) == 1


def test_rotate_logs(tmp_path):
    d = tmp_path / "Logs"
    d.mkdir()
    big, small = d / "daemon.err", d / "web.log"
    big.write_bytes(b"x" * (scheduler.LOG_MAX_BYTES + 1))
    small.write_text("hi")
    small.chmod(0o644)
    (d / "daemon.err.1").write_text("one")
    (d / "daemon.err.2").write_text("two")
    (d / "daemon.err.3").write_text("three")   # the oldest drops off
    assert scheduler.rotate_logs(d, "Linux") == []
    assert scheduler.rotate_logs(d, "Darwin") == ["daemon.err"]
    assert big.stat().st_size == 0 and (d / "daemon.err.1").stat().st_size == scheduler.LOG_MAX_BYTES + 1
    assert (d / "daemon.err.2").read_text() == "one" and (d / "daemon.err.3").read_text() == "two" and not (d / "daemon.err.4").exists()
    assert mode(small) == 0o600 and mode(big) == 0o600 and mode(d / "daemon.err.1") == 0o600
    assert small.read_text() == "hi"


def test_detector_select_does_not_hold_the_write_lock(conn, monkeypatch):
    seed(conn)
    other = sqlite3.connect(config.db_path(), isolation_level=None, timeout=0)
    real = detect.select
    writes = []

    def select(c, f, as_of):
        other.execute("INSERT INTO state(key, value) VALUES ('probe', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f.stem,))
        writes.append(f.stem)   # the dashboard's write went through while the detector was reading
        return real(c, f, as_of)
    monkeypatch.setattr(detect, "select", select)
    detect.run(conn, AS_OF)
    assert writes and not [a for a in conn.execute("SELECT kind FROM alerts") if a[0] == "detector_error"]


def test_a_failed_rerun_keeps_the_days_good_copy(conn, monkeypatch):
    good = backup.run(conn)
    before = good.read_bytes()
    monkeypatch.setattr(backup.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    import pytest
    with pytest.raises(OSError):
        backup.run(conn)
    assert good.read_bytes() == before and not list(backup.backup_dir().glob("*.tmp"))
