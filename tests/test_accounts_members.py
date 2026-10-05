"""Household-journey QA: owner names are checked and case-folded, members can be renamed and removed, a merge is sanity
checked and undoable, and every link path marks a joint account's mirror."""
import json

import pytest

from finnamon import cli, imports, link, owners
from tests.conftest import seed


def run(capsys, *argv):
    capsys.readouterr()
    cli.main(list(argv))
    return capsys.readouterr().out


def fails(capsys, *argv):
    capsys.readouterr()
    with pytest.raises(SystemExit):
        cli.main(list(argv))
    return capsys.readouterr().err


@pytest.fixture
def two(conn):
    seed(conn)
    conn.execute("INSERT INTO owners (owner, display_name) VALUES ('jane', 'Jane')")
    return conn


def test_unknown_owner_is_refused_everywhere_and_case_is_folded(two, capsys):
    for argv in (["account", "add", "X", "--institution", "Y", "--owner", "bob"], ["account", "owner", "chk", "carol"], ["link", "--start", "--owner", "typo"]):
        assert "not a household member (members: bill, jane" in fails(capsys, *argv), argv
    assert two.execute("SELECT count(*) FROM owners").fetchone()[0] == 2
    assert json.loads(run(capsys, "account", "add", "X", "--institution", "Y", "--owner", "JANE"))["owner"] == "jane"
    assert json.loads(run(capsys, "account", "add", "Z", "--institution", "Y", "--owner", "Joint"))["owner"] == "joint"
    assert two.execute("SELECT count(*) FROM owners").fetchone()[0] == 2   # joint is never a member row
    assert json.loads(run(capsys, "account", "add", "W", "--institution", "V", "--owner", "joint"))["item_id"] == "manual:v"
    assert two.execute("SELECT owner FROM items WHERE item_id='manual:v'").fetchone()[0] == "bill"   # a bank hangs off a real member
    run(capsys, "account", "owner", "chk", "JOINT")
    assert two.execute("SELECT owner FROM accounts WHERE account_id='chk'").fetchone()[0] == "joint"
    assert "no account" in fails(capsys, "account", "owner", "nope", "jane")
    assert "already a household member" in fails(capsys, "owner", "add", "JANE", "--no-telegram")   # one person, not two
    assert "reserved" in fails(capsys, "owner", "add", "Joint", "--no-telegram")


def test_owner_rename_and_remove(two, capsys, monkeypatch):
    two.execute("INSERT INTO accounts (account_id, item_id, name, type, owner, owner_before_merge) VALUES ('j','item1','j','depository','joint','bill')")
    assert json.loads(run(capsys, "owner", "rename", "bill", "William"))["to"] == "William"
    assert {r[0] for r in two.execute("SELECT owner FROM accounts WHERE account_id IN ('chk','cc')")} == {"William"}
    assert two.execute("SELECT owner FROM items WHERE item_id='item1'").fetchone()[0] == "William"
    assert two.execute("SELECT owner_before_merge FROM accounts WHERE account_id='j'").fetchone()[0] == "William"
    assert two.execute("SELECT telegram_user_id FROM owners WHERE owner='William'").fetchone()[0] == 111
    assert "already a household member" in fails(capsys, "owner", "rename", "William", "JANE")
    assert "reserved" in fails(capsys, "owner", "rename", "William", "joint")
    run(capsys, "owner", "rename", "jane", "Jane")   # case only
    assert [r[0] for r in two.execute("SELECT owner FROM owners ORDER BY owner")] == ["Jane", "William"]
    assert "still owns 2 accounts" in fails(capsys, "owner", "remove", "william")
    run(capsys, "account", "owner", "chk", "Jane"); run(capsys, "account", "owner", "cc", "joint")
    run(capsys, "owner", "remove", "William")
    assert two.execute("SELECT owner FROM items WHERE item_id='item1'").fetchone()[0] == "Jane"   # the bank follows a member who remains
    run(capsys, "account", "owner", "chk", "joint")
    assert "only household member" in fails(capsys, "owner", "remove", "Jane")
    monkeypatch.setenv("FINNAMON_FROM_CLAUDE", "1")
    assert "for a person at a terminal" in fails(capsys, "owner", "rename", "Jane", "x")
    assert "for a person at a terminal" in fails(capsys, "owner", "remove", "Jane")
    assert "for a person at a terminal" in fails(capsys, "account", "owner", "chk", "Jane")


def test_bundle_denies_the_human_only_household_changes():
    deny = json.loads((cli.Path(cli.__file__).parent / "assistant_bundle/.claude/settings.json").read_text())["permissions"]["deny"]
    assert {"Bash(finnamon owner rename*)", "Bash(finnamon owner remove*)", "Bash(finnamon account owner *)"} <= set(deny)


def _acct(conn, id_, typ="depository", bal=None, owner="bill"):
    conn.execute("INSERT INTO accounts (account_id, item_id, name, type, owner) VALUES (?, 'item1', ?, ?, ?)", (id_, id_, typ, owner))
    if bal is not None:
        conn.execute("INSERT INTO balances (account_id, as_of, current) VALUES (?, '2026-09-01 00:00:00', ?)", (id_, bal))


def test_merge_sanity_check_force_and_unmerge_restores_owners(two, capsys):
    _acct(two, "a", "credit", 400); _acct(two, "b", "depository", 5000, "jane")
    assert "not the same account" in fails(capsys, "account", "merge", "a", "b")   # an Amex is not a checking account
    _acct(two, "c", "depository", 90000, "jane")
    assert "too far apart" in fails(capsys, "account", "merge", "c", "b")
    assert "no account" in fails(capsys, "account", "merge", "c", "ghost") and "itself" in fails(capsys, "account", "merge", "c", "c")
    assert two.execute("SELECT count(*) FROM accounts WHERE mirror_of IS NOT NULL").fetchone()[0] == 0
    run(capsys, "account", "merge", "c", "b", "--force")
    assert [tuple(r) for r in two.execute("SELECT owner, owner_before_merge FROM accounts WHERE account_id IN ('b','c') ORDER BY 1")] == [("joint", "jane")] * 2
    assert "already part of a merge" in fails(capsys, "account", "merge", "a", "c")
    assert json.loads(run(capsys, "account", "unmerge", "b"))["unmerged"] == ["c"]   # either side of the pair
    assert [tuple(r) for r in two.execute("SELECT owner, mirror_of, owner_before_merge FROM accounts WHERE account_id IN ('b','c') ORDER BY 1")] == [("jane", None, None)] * 2
    assert "not part of a merge" in fails(capsys, "account", "unmerge", "b")
    _acct(two, "d", "depository", 5100, "bill")
    run(capsys, "account", "merge", "d", "b")   # near balances: fine, no --force
    two.execute("DELETE FROM owners WHERE owner='jane'")   # the old owner left in between: stays joint rather than dangling
    run(capsys, "account", "unmerge", "d")
    assert two.execute("SELECT owner FROM accounts WHERE account_id='b'").fetchone()[0] == "joint"
    assert two.execute("SELECT owner FROM accounts WHERE account_id='d'").fetchone()[0] == "bill"


def test_every_link_path_marks_the_mirror_and_joint_link_marks_accounts(two, tg, monkeypatch):
    mirror = {"new": "n1", "existing": "chk", "name": "Checking", "mask": "4821", "match": "persistent_account_id"}
    two.execute("INSERT INTO items (item_id, owner) VALUES ('item2','jane')")
    two.execute("INSERT INTO accounts (account_id, item_id, name, type, mask, owner) VALUES ('n1','item2','x','depository','4821','jane')")
    monkeypatch.setattr(link, "announce", lambda *a, **k: None)
    r = link._linked(two, {"item_id": "item2", "mirror_candidates": [mirror]})
    assert r["marked_joint"] == [mirror] and r["mirror_candidates"] == []
    assert [tuple(x) for x in two.execute("SELECT account_id, owner, mirror_of FROM accounts WHERE account_id IN ('chk','n1') ORDER BY 1")] == [("chk", "joint", None), ("n1", "joint", "chk")]
    # a re-link: the existing copy's login is broken, so the person decides which copy goes
    two.execute("UPDATE accounts SET mirror_of=NULL WHERE account_id='n1'"); two.execute("UPDATE items SET status='ITEM_LOGIN_REQUIRED' WHERE item_id='item1'")
    r = link._linked(two, {"item_id": "item2", "mirror_candidates": [mirror]})
    assert r["mirror_candidates"] == [mirror] and r["marked_joint"] == []
    assert "marked joint" in link.mirror_note([], [mirror]) and "account merge n1 chk" in link.mirror_note([mirror])


def test_joint_link_files_the_bank_under_the_first_member_and_its_accounts_joint(two, monkeypatch):
    monkeypatch.setattr(link.plaid_api, "public_token_exchange", lambda pt: {"item_id": "item3", "access_token": "t"})
    monkeypatch.setattr(link.secrets, "update", lambda **k: None)
    monkeypatch.setattr(link.plaid_api, "item_get", lambda t: {"item": {"institution_id": "ins_3", "institution_name": "Ally"}})

    def fake_sync(conn, item_id, tok):
        conn.execute("INSERT INTO accounts (account_id, item_id, name, type, owner) VALUES ('s1', ?, 's', 'depository', (SELECT owner FROM items WHERE item_id=?))", (item_id, item_id))
        return {"accounts": 1}
    monkeypatch.setattr(link.sync, "sync_item", fake_sync)
    link.complete(two, "joint", "public-x")
    assert two.execute("SELECT owner FROM items WHERE item_id='item3'").fetchone()[0] == "bill"
    assert two.execute("SELECT owner FROM accounts WHERE account_id='s1'").fetchone()[0] == "joint"
    assert two.execute("SELECT count(*) FROM owners").fetchone()[0] == 2


def test_manual_joint_account_via_import_layer(two):
    r = imports.add_account(two, "Joint Chk", "HSBC", "checking", "joint")
    assert r["owner"] == "joint" and two.execute("SELECT owner FROM items WHERE item_id=?", (r["item_id"],)).fetchone()[0] == "bill"
    assert owners.resolve(two, " jane ") == "jane"
