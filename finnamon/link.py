"""Plaid Hosted Link: create a session, print the URL (Telegram only with --telegram), poll /link/token/get
for the public_token (available for 6h after completion, no redirect or webhook needed), exchange it,
write the token to secrets.toml, register the Item, first sync, one-time summary.
Two shapes: the blocking `finnamon link` waits in the terminal; `start_pending` (what the assistant runs)
records the session in state.pending_link and returns, and `check_pending` (the daemon's tick, or
`finnamon link --finish`) completes it. A re-login (`start_update`, from the chat or the dashboard's Reconnect) is
remembered in state.update_sessions, and `check_updates` (the daemon's tick) syncs the bank as soon as Plaid says the
login worked. `remove` undoes a link under the run lock: /item/remove, the
Item's rows dropped, its token forgotten.

Joint-account detection (Step 5) runs on every link: a new account that matches an existing one
(persistent_account_id, or same institution + mask + subtype + ≥90% of the last 90 days'
transactions identical) is offered as a mirror."""
from __future__ import annotations

import json
import logging
import sqlite3
import time

from . import config, imports, notify, plaid_api, run, secrets, store, sync, telegram

log = logging.getLogger("finnamon.link")


def start(owner: str, update_item: str | None = None, redirect_uri: str | None = None, hosted: bool = True) -> dict:
    token = config.item_token(update_item) if update_item else None
    if update_item and not token:
        raise ValueError(f"no token for item {update_item}")
    resp = plaid_api.link_token_create(user_id=f"finnamon-{owner}", access_token=token, redirect_uri=redirect_uri, hosted=hosted)
    return {"link_token": resp["link_token"], "url": resp.get("hosted_link_url"), "expiration": resp.get("expiration")}


LOCK_WAIT_S = 120   # a public_token is good for ~30 min; a sync cycle usually isn't


def finish_in_page(conn: sqlite3.Connection, owner: str, public_token: str, wait_s: int = LOCK_WAIT_S, sleep=time.sleep) -> dict:
    """The web dashboard's Plaid Link handed us a public_token: register, sync, announce, report mirrors. Waits a while
    for the run lock (the daemon may be mid-cycle) because giving up means a whole new bank login."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            held = run.lock()
            break
        except run.Locked:
            if time.monotonic() >= deadline:
                raise run.Locked("a sync held the lock for too long; the bank login must be repeated from Link account") from None
            sleep(3)
    try:
        r = complete(conn, owner, public_token)
    finally:
        held.close()
    return _linked(conn, r)


def _linked(conn: sqlite3.Connection, r: dict) -> dict:
    """The bank is linked either way; a chat hiccup must not read as "try again"."""
    r["state"] = "linked"
    try:
        announce(conn, r["item_id"], r["mirror_candidates"])
        r["announced"] = True
    except telegram.TelegramError as e:
        log.warning("linked %s but the chat announcement failed: %s", r["item_id"], e)
        r["announced"] = False
    return r


LINK_WAIT_S = 4 * 3600  # Hosted Link sessions last ~4h; wait as long as the link is valid


CLOSED = "closed"   # poll_public_token: the person closed Link without adding a bank


def poll_public_token(link_token: str) -> str | None:
    """One /link/token/get: the public_token, CLOSED if every session ended without an Item, None if one is still open.
    A closed-then-reopened URL yields several sessions; a token in any of them wins."""
    sessions = plaid_api.link_token_get(link_token).get("link_sessions") or []
    for sess in sessions:
        for r in (sess.get("results") or {}).get("item_add_results") or []:
            if r.get("public_token"):
                return r["public_token"]
        if (sess.get("on_success") or {}).get("public_token"):   # the older field; an update-mode session may report only this
            return sess["on_success"]["public_token"]
    if sessions and all(s.get("finished_at") for s in sessions):
        return CLOSED
    return None


def wait_for_public_token(link_token: str, timeout_s: int = LINK_WAIT_S, poll_s: int = 5, sleep=time.sleep) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pt = poll_public_token(link_token)
        if pt == CLOSED:
            return None
        if pt:
            return pt
        sleep(poll_s)
    return None


PENDING_TTL_S = LINK_WAIT_S + 6 * 3600   # the session lives ~4h and its public_token another 6h after completion


UPDATE_LINK_VALID = "30 minutes"   # Plaid expires a link token for an existing Item (update mode) after 30 min, not 4h


def send_url(conn: sqlite3.Connection, url: str, what: str = "Connect a bank", valid: str = f"{LINK_WAIT_S // 3600}h") -> bool:
    """Post the Plaid link to the household chat (only ever on request: the chat is shared, not your phone)."""
    chat = store.get_state(conn, "chat_id")
    if not chat:
        return False
    telegram.send_message(chat, f"{telegram.esc(what)} here (valid for about {valid}): {telegram.esc(url)}")
    return True


UPDATE_ITEM_GAP_S = 15 * 60   # one re-login link per Item per 15 min: a link lives 30 min, so a second one is never needed sooner
UPDATE_DAILY_CAP = 6          # per household per 24h: a prompt-injected memo must not fill the chat with Plaid link tokens


def _update_rate_limit(conn: sqlite3.Connection, item_id: str, now: float) -> list:
    """The recent links the assistant sent (state.update_links, [[item_id, epoch], ...] within 24h, "new" for an add);
    raises ValueError over the limit. ponytail: read-then-write without a lock, so two runs at once may both pass once."""
    try:
        sent = [[str(i), float(t)] for i, t in json.loads(store.get_state(conn, "update_links") or "[]") if 0 <= now - float(t) < 86400]
    except (ValueError, TypeError):   # only we write it; a corrupt value must not lock out every re-login
        sent = []
    last = max((t for i, t in sent if i == item_id), default=None)
    if last is not None and item_id != "new" and now - last < UPDATE_ITEM_GAP_S:   # adds only count toward the cap
        raise ValueError(f"a link for this bank went to the chat {int((now - last) // 60)} min ago and is still valid; "
                         f"use that one (a new one is possible in {int((UPDATE_ITEM_GAP_S - (now - last)) // 60) + 1} min)")
    if len(sent) >= UPDATE_DAILY_CAP:
        raise ValueError(f"{UPDATE_DAILY_CAP} bank links went to the chat in the last 24 hours, the most allowed; "
                         "use one already sent, or a person at the terminal runs `finnamon link --update <item_id>`")
    return sent


UPDATE_SESSION_TTL_S = 3600   # how long the tick polls a re-login: the token lives 30 min, and a login begun at minute 29 may still finish
UPDATE_REUSE_S = 20 * 60      # the dashboard's Reconnect hands back a session this young (with 10 min left of its 30) rather than open another


UPDATE_SESSION_KEYS = {"item_id", "link_token", "url", "started_at", "expires_at"}


def _update_sessions(conn: sqlite3.Connection) -> list[dict]:
    """state.update_sessions: [{item_id, link_token, url, started_at, expires_at}, ...], epochs. Only we write it; a
    corrupt value reads as empty rather than wedging every re-login."""
    try:
        return [x for x in json.loads(store.get_state(conn, "update_sessions") or "[]") if UPDATE_SESSION_KEYS <= x.keys()]
    except (ValueError, TypeError, AttributeError):
        return []


def _set_update_sessions(conn: sqlite3.Connection, keep) -> None:
    """Re-read, then filter: a session the page opened while the tick was polling is not lost.
    ponytail: read-then-write without a lock; the window is one statement wide."""
    kept = [x for x in _update_sessions(conn) if keep(x)]
    store.set_state(conn, "update_sessions", json.dumps(kept) if kept else None)


def start_update(conn: sqlite3.Connection, item_id: str, now: float | None = None, to_chat: bool = True) -> dict:
    """Non-blocking re-login: an update-mode Hosted Link for an Item we already have, under the Item's own owner.
    to_chat (the assistant's `fix X`, always to the chat): rate limited, and the message names whose login it is (two
    logins at one bank are common in a joint household). Not to_chat (the dashboard's Reconnect, a person's click): no
    15-min budget, but a double click reuses the session still open rather than starting another. Either way the session
    is remembered, and the daemon's tick syncs the bank once the login is done (check_updates)."""
    row = conn.execute("SELECT institution, owner FROM items WHERE item_id=? AND source='plaid'", (item_id,)).fetchone()
    if not row:
        raise ValueError(f"no linked bank {item_id}; `finnamon status` lists them")
    now = time.time() if now is None else now
    inst = row[0] or item_id
    if to_chat:
        if not store.get_state(conn, "chat_id"):   # before start(): no Link session opened only to be dropped
            raise ValueError("no household chat to send the link to")
        sent = _update_rate_limit(conn, item_id, now)
    else:
        fresh = [x for x in _update_sessions(conn) if x["item_id"] == item_id and 0 <= now - float(x["started_at"]) < UPDATE_REUSE_S]
        try:   # a session already over (closed, or done while the tick waited on the lock) is no page to hand back
            if fresh and poll_public_token(fresh[-1]["link_token"]) is None:
                return {"item_id": item_id, "institution": inst, "owner": row[1], "url": fresh[-1]["url"], "reused": True}
        except plaid_api.PlaidError:
            pass   # unknown: a new session is the safe answer
    s = start(row[1], item_id)
    sessions = _update_sessions(conn) + [{"item_id": item_id, "link_token": s["link_token"], "url": s["url"],
                                          "started_at": now, "expires_at": now + UPDATE_SESSION_TTL_S}]
    store.set_state(conn, "update_sessions", json.dumps(sessions))
    if not to_chat:
        return {"item_id": item_id, "institution": inst, "owner": row[1], "url": s["url"], "reused": False}
    send_url(conn, s["url"], f"Log back into {inst} ({row[1]}'s login)", UPDATE_LINK_VALID)
    store.set_state(conn, "update_links", json.dumps(sent + [[item_id, now]]))   # only a link that reached the chat counts
    return {"item_id": item_id, "institution": inst, "owner": row[1], "sent": True, "valid_for": UPDATE_LINK_VALID,
            "next": "once they log in at the link, Finnamon syncs the bank within a minute and says so in the chat"}


def check_updates(conn: sqlite3.Connection, now: float | None = None) -> list[dict]:
    """The daemon's tick: each open re-login session, polled (outside the run lock: most ticks find nothing over). Once
    a session is over, that one bank syncs at once, under the lock; when it is held, the next tick retries.
    Success: Plaid's results carry a public token, or (update-mode results are thin) a bank in error syncs again. Then
    the Item is good, its health alerts are resolved 'reconnected' and the chat hears "<bank> is reconnected". A session
    closed with no token on a bank that still syncs (its connection only expiring) proves nothing, so its consent alert
    stays open. Expired, forgotten, or still-refused sessions are dropped quietly: the next sync, or another Reconnect,
    covers it."""
    sessions = _update_sessions(conn)
    if not sessions:
        return []
    now = time.time() if now is None else now
    gone, ended = {x["link_token"] for x in sessions if now >= float(x["expires_at"])}, []
    for x in sessions:
        if x["link_token"] in gone:
            continue
        try:
            pt = poll_public_token(x["link_token"])
        except plaid_api.PlaidError as e:
            if e.code != "INVALID_LINK_TOKEN":
                log.warning("re-login %s: %s", x["item_id"], e)
                continue   # retried next tick
            pt = CLOSED
        if pt is not None:
            ended.append((x, pt != CLOSED))
    out = []
    try:
        if not ended:
            return out
        try:
            held = run.lock()
        except run.Locked:
            return out
        try:
            for x, proof in sorted(ended, key=lambda e: not e[1]):   # a session with proof first: it decides for its bank
                if x["link_token"] in gone:
                    continue   # its bank was reconnected by an earlier session this tick
                row = conn.execute("SELECT institution, status FROM items WHERE item_id=?", (x["item_id"],)).fetchone()
                if not row or (not proof and row[1] == "good"):   # unlinked meanwhile; or closed on a bank that never broke
                    gone.add(x["link_token"])
                    continue
                r = {"item_id": x["item_id"], "institution": row[0] or x["item_id"], "sync": reconnect(conn, x["item_id"], renewed=proof)}
                gone.add(x["link_token"])   # only now: a sync that raised (a locked database) is retried next tick
                out.append(r)
                if r["sync"]["error"]:
                    log.info("re-login session for %s ended; the bank still says %s", x["item_id"], r["sync"]["error"])
                    continue
                gone.update(y["link_token"] for y in sessions if y["item_id"] == x["item_id"])   # one sync per bank; a session opened since stays
                try:
                    if chat := store.get_state(conn, "chat_id"):
                        telegram.send_message(chat, f"{telegram.esc(r['institution'])} is reconnected.")
                except telegram.TelegramError as e:   # the bank is back either way
                    log.warning("reconnected %s but the chat message failed: %s", x["item_id"], e)
        finally:
            held.close()
    finally:
        if gone:
            _set_update_sessions(conn, lambda x: x["link_token"] not in gone)
    return out


def reconnect(conn: sqlite3.Connection, item_id: str, renewed: bool = True) -> dict:
    """Sync one Item after a re-login; when that works, it is good again and its open health alerts are resolved, each
    keyed apart so the detector can raise a new one if the bank breaks again today. Not renewed (no proof the login
    happened): the consent alert stays open, since a bank whose connection is only expiring syncs either way."""
    result = sync.sync_item(conn, item_id)
    if not result["error"]:
        conn.execute("UPDATE items SET status='good', last_error=NULL WHERE item_id=?", (item_id,))
        prefix, consent = f"health:{item_id}:", f"health:{item_id}:consent:"   # not LIKE: an _ in the id would match another bank's
        conn.execute("UPDATE alerts SET resolved_at=?, resolution='reconnected', key=key || ':reconnected:' || id "
                     "WHERE resolved_at IS NULL AND substr(key, 1, ?) = ? AND (? OR substr(key, 1, ?) <> ?)",
                     (store.now_local(), len(prefix), prefix, renewed, len(consent), consent))
    return result


def start_pending(conn: sqlite3.Connection, owner: str, to_telegram: bool = False, limited: bool = False, now: float | None = None) -> dict:
    """Non-blocking add (the assistant may run this): open a Hosted Link session, remember it in state, return the URL.
    A session still open from an earlier --start is reused, never orphaned: a person may be half-way through it, and
    an Item created in a forgotten session is billed with no local token to remove it by."""
    r = check_pending(conn)
    if r and r["state"] == "linked":
        return r   # the person finished between two requests; say so rather than hand out a new link
    now = time.time() if now is None else now
    sent = _update_rate_limit(conn, "new", now) if limited and to_telegram else None   # the assistant's sends share the re-login budget
    if r and r["state"] in ("waiting", "closed") and not conn.execute(
            "SELECT ? < datetime('now','localtime', ?)", (r["pending"].get("started_at"), f"-{LINK_WAIT_S} seconds")).fetchone()[0]:
        s = dict(r["pending"], reused=True)   # the URL is still valid: same session, never a second one
    else:
        s = start(owner)
        store.set_state(conn, "pending_link", json.dumps({"owner": owner, "link_token": s["link_token"], "url": s["url"],
                                                          "started_at": store.now_local()}))
    if to_telegram and send_url(conn, s["url"]) and limited:
        store.set_state(conn, "update_links", json.dumps(sent + [["new", now]]))
    return s


def check_pending(conn: sqlite3.Connection) -> dict | None:
    """Finish a pending add if the person is done. None when nothing is pending; otherwise a dict with 'state':
    waiting | closed | expired | linked (with the link result). Runs under the run lock so the daemon tick and
    `link --finish` can't both exchange the token; when the lock is held it reports waiting and the next tick retries.
    The state row is cleared only once the exchange succeeded: a failed exchange is retried, not forgotten.
    Mirror candidates are reported, not applied: `account merge` is a person's call."""
    raw = store.get_state(conn, "pending_link")
    if not raw:
        return None
    try:
        p = json.loads(raw)
        p["link_token"], p["owner"]
    except (ValueError, KeyError, TypeError):
        log.warning("pending_link row is malformed; dropping it")
        store.set_state(conn, "pending_link", None)
        return {"state": "expired"}
    if p.get("started_at") and conn.execute("SELECT ? < datetime('now','localtime', ?)", (p["started_at"], f"-{PENDING_TTL_S} seconds")).fetchone()[0]:
        store.set_state(conn, "pending_link", None)
        return {"state": "expired"}
    try:
        held = run.lock()
    except run.Locked:
        return {"state": "waiting", "pending": p}
    try:
        if p.get("item_id") and config.item_token(p["item_id"]):
            r = complete(conn, p["owner"], "", resume=(p["item_id"], config.item_token(p["item_id"])))
            store.set_state(conn, "pending_link", None)
        else:
            try:
                pt = poll_public_token(p["link_token"])
            except plaid_api.PlaidError as e:
                if e.code == "INVALID_LINK_TOKEN":   # Plaid forgot the session; nothing to wait for, and retrying it is pointless
                    store.set_state(conn, "pending_link", None)
                    return {"state": "expired"}
                raise
            if pt is None:
                return {"state": "waiting", "pending": p}
            if pt == CLOSED:
                # closed for now; the same URL can be reopened until it expires, so keep polling. Say so once.
                if p.get("closed_notified"):
                    return {"state": "waiting", "pending": p}
                p["closed_notified"] = True
                store.set_state(conn, "pending_link", json.dumps(p))
                return {"state": "closed", "pending": p}

            def remember(item_id: str) -> None:
                p["item_id"] = item_id
                store.set_state(conn, "pending_link", json.dumps(p))
            try:
                r = complete(conn, p["owner"], pt, on_exchanged=remember)
            except plaid_api.PlaidError as e:
                if e.code == "INVALID_PUBLIC_TOKEN":   # spent by an attempt that died before it could record the item_id
                    log.warning("public token already spent; item %s may exist at Plaid with no local token (remove it in the Plaid dashboard)", p.get("item_id"))
                    store.set_state(conn, "pending_link", None)
                    return {"state": "expired"}
                raise
            store.set_state(conn, "pending_link", None)
    finally:
        held.close()
    return _linked(conn, r)


def register_item(conn: sqlite3.Connection, owner: str, access_token: str, item_id: str) -> str:
    secrets.update(items={item_id: access_token})  # secret first, before any further network call: an orphan token is harmless, an unrecorded Item is billed with no way to remove it
    inst_id, inst_name = None, None
    try:
        item = plaid_api.item_get(access_token)["item"]
        inst_id = item.get("institution_id")
        inst_name = item.get("institution_name")
        if inst_id and not inst_name:
            inst_name = plaid_api.institution_get(inst_id)["institution"]["name"]
    except plaid_api.PlaidError as e:
        log.warning("item/get failed: %s", e)
    conn.execute("INSERT OR IGNORE INTO owners (owner) VALUES (?)", (owner,))
    conn.execute("INSERT INTO items (item_id, institution_id, institution, owner) VALUES (?,?,?,?) "
                 "ON CONFLICT(item_id) DO UPDATE SET institution_id=excluded.institution_id, institution=excluded.institution, owner=excluded.owner, status='good', last_error=NULL",
                 (item_id, inst_id, inst_name, owner))
    return inst_name or item_id


def complete(conn: sqlite3.Connection, owner: str, public_token: str, update_item: str | None = None, on_exchanged=None,
             resume: tuple[str, str] | None = None) -> dict:
    if update_item:
        # Update mode: same Item, same token; just clear the error and resync.
        conn.execute("UPDATE items SET status='good', last_error=NULL WHERE item_id=?", (update_item,))
        return {"item_id": update_item, "updated": True, "sync": reconnect(conn, update_item, renewed=bool(public_token))}
    if resume:
        item_id, access_token = resume   # exchanged on an earlier attempt; a public_token is single-use
    else:
        ex = plaid_api.public_token_exchange(public_token)
        item_id, access_token = ex["item_id"], ex["access_token"]
        if on_exchanged:
            on_exchanged(item_id)   # the pending row learns the item_id, so a crash from here on is resumed without a second exchange
    name = register_item(conn, owner, access_token, item_id)
    try:
        result = sync.sync_item(conn, item_id, access_token)
    except Exception as e:  # noqa: BLE001 - the Item is registered; the daemon will sync it. Don't lose the mirror prompt and the summary.
        log.exception("first sync of %s failed", item_id)
        result = {"item_id": item_id, "accounts": 0, "transactions": 0, "recurring": 0, "holdings": 0, "error": f"{type(e).__name__}: {e}"[:200]}
    mirrors = detect_mirrors(conn, item_id)
    return {"item_id": item_id, "institution": name, "sync": result, "mirror_candidates": mirrors}


def remove(conn: sqlite3.Connection, item_id: str) -> dict:
    """Unlink: tell Plaid (stops billing), drop the Item's rows, forget its token. Alerts keep their payloads.

    Holds the run lock so a daemon sync can't interleave with the delete (raises run.Locked; retry in a minute)."""
    lock = run.lock()
    try:
        return _remove(conn, item_id)
    finally:
        lock.close()


def _remove(conn: sqlite3.Connection, item_id: str) -> dict:
    token = config.item_token(item_id)
    inst = conn.execute("SELECT institution FROM items WHERE item_id=?", (item_id,)).fetchone()
    if not inst and not token:
        raise ValueError(f"unknown item {item_id}")
    if token:
        try:
            plaid_api.item_remove(token)
        except plaid_api.PlaidError as e:
            if e.code not in ("ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"):   # already gone on Plaid's side (a re-run after a crash, or revoked at the bank)
                raise
    with store.tx(conn):
        accts = [r[0] for r in conn.execute("SELECT account_id FROM accounts WHERE item_id=?", (item_id,))]
        for a in accts:
            _drop_rows(conn, a)
        # unsent alerts about this Item would go out pointing at accounts that no longer exist
        now = store.now_local()
        conn.execute("UPDATE alerts SET resolved_at=?, sent_at=COALESCE(sent_at, ?), verdict=COALESCE(verdict, 'suppress') "
                     "WHERE sent_at IS NULL AND (account_id IN (SELECT account_id FROM accounts WHERE item_id=?) OR key LIKE ?)",
                     (now, now, item_id, f"health:{item_id}:%"))
        conn.execute("DELETE FROM accounts WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM items WHERE item_id=?", (item_id,))
    if token:
        secrets.remove_item(item_id)
    return {"removed": item_id, "institution": inst[0] if inst else None, "accounts": len(accts)}


def _drop_rows(conn: sqlite3.Connection, account_id: str) -> None:
    conn.execute("UPDATE accounts SET mirror_of=NULL WHERE mirror_of=?", (account_id,))
    # one-time category edits are keyed by transaction: an imported row's id is a hash of its content, so a re-import would revive them
    conn.execute("DELETE FROM tx_category_override WHERE transaction_id IN (SELECT transaction_id FROM transactions WHERE account_id=?)", (account_id,))
    # every table keyed by account_id: the four with a FK (foreign_keys=ON makes a miss here fail loudly) plus settings and suppressions
    for table in ("balances", "transactions", "recurring", "holdings", "settings", "suppressions"):
        conn.execute(f"DELETE FROM {table} WHERE account_id=?", (account_id,))


def remove_account(conn: sqlite3.Connection, ref: str) -> dict:
    """One manual account (by id or name) and everything keyed by it; its item too when it was the bank's last account.
    A Plaid account is refused: Plaid would bring it back on the next sync, so unlinking is per Item (`link --remove`)."""
    lock = run.lock()   # before the lookup: a sync or a second remove must not change the row between reading and deleting it
    try:
        with store.tx(conn):
            r = imports.lookup_account(conn, ref)
            if not r:
                raise ValueError(f"no account {ref!r}; `finnamon account list` shows them")
            if r["source"] != "manual":
                raise ValueError(f"{r['name']} is synced from Plaid; unlink its bank with `finnamon link --remove {r['item_id']}`")
            _drop_rows(conn, r["account_id"])
            now = store.now_local()
            conn.execute("UPDATE alerts SET resolved_at=?, sent_at=COALESCE(sent_at, ?), verdict=COALESCE(verdict, 'suppress') "
                         "WHERE sent_at IS NULL AND account_id=?", (now, now, r["account_id"]))
            conn.execute("DELETE FROM accounts WHERE account_id=?", (r["account_id"],))
            last = not conn.execute("SELECT 1 FROM accounts WHERE item_id=?", (r["item_id"],)).fetchone()
            if last:
                conn.execute("UPDATE alerts SET resolved_at=?, sent_at=COALESCE(sent_at, ?), verdict=COALESCE(verdict, 'suppress') "
                             "WHERE sent_at IS NULL AND key LIKE ?", (now, now, f"health:{r['item_id']}:%"))
                conn.execute("DELETE FROM items WHERE item_id=?", (r["item_id"],))
    finally:
        lock.close()
    return {"removed": r["account_id"], "name": r["name"], "transactions": r["transactions"], "item_removed": r["item_id"] if last else None}


def detect_mirrors(conn: sqlite3.Connection, new_item_id: str) -> list[dict]:
    """Accounts on the new Item that look like accounts we already have from another Item."""
    out = []
    new = conn.execute("SELECT * FROM accounts WHERE item_id=? AND mirror_of IS NULL", (new_item_id,)).fetchall()
    inst = conn.execute("SELECT institution_id FROM items WHERE item_id=?", (new_item_id,)).fetchone()[0]
    for a in new:
        cands = conn.execute(
            "SELECT acc.* FROM accounts acc JOIN items i ON i.item_id=acc.item_id WHERE acc.item_id<>? AND acc.mirror_of IS NULL "
            "AND ((acc.persistent_account_id IS NOT NULL AND acc.persistent_account_id=?) "
            "  OR (i.institution_id=? AND acc.mask=? AND acc.subtype=?))",
            (new_item_id, a["persistent_account_id"], inst, a["mask"], a["subtype"])).fetchall()
        for c in cands:
            if a["persistent_account_id"] and a["persistent_account_id"] == c["persistent_account_id"]:
                out.append({"new": a["account_id"], "existing": c["account_id"], "name": a["name"], "mask": a["mask"], "match": "persistent_account_id", "score": 1.0})
                continue
            total, same = conn.execute(
                "SELECT count(*), sum(EXISTS(SELECT 1 FROM transactions e WHERE e.account_id=? AND e.date=n.date AND e.amount=n.amount AND e.name=n.name)) "
                "FROM transactions n WHERE n.account_id=? AND n.date >= date('now','-90 days') AND n.pending=0",
                (c["account_id"], a["account_id"])).fetchone()
            score = (same or 0) / total if total else 0
            if total >= 5 and score >= 0.9:
                out.append({"new": a["account_id"], "existing": c["account_id"], "name": a["name"], "mask": a["mask"],
                            "match": f"{same} of {total} transactions identical", "score": score})
    return out


def mark_mirror(conn: sqlite3.Connection, new_account_id: str, existing_account_id: str) -> None:
    with store.tx(conn):
        conn.execute("UPDATE accounts SET mirror_of=?, owner='joint' WHERE account_id=?", (existing_account_id, new_account_id))
        conn.execute("UPDATE accounts SET owner='joint' WHERE account_id=?", (existing_account_id,))


def failover(conn: sqlite3.Connection, account_id: str) -> str | None:
    """If this account's Item is broken and a mirror exists on a healthy Item, flip which row is the mirror."""
    m = conn.execute("SELECT m.account_id, i.status FROM accounts m JOIN items i ON i.item_id=m.item_id WHERE m.mirror_of=?", (account_id,)).fetchone()
    if not m or m["status"] != "good":
        return None
    with store.tx(conn):
        conn.execute("UPDATE accounts SET mirror_of=NULL WHERE account_id=?", (m["account_id"],))
        conn.execute("UPDATE accounts SET mirror_of=? WHERE account_id=?", (m["account_id"], account_id))
    return m["account_id"]


def mirror_line(m: dict) -> str:
    return f"{m['name']} …{m['mask']} looks like an account already linked ({m['match']})"


def mirror_note(mirrors: list[dict]) -> str:
    return "\n".join(f"{mirror_line(m)}; if it is the same account from a second login, "
                     f"run finnamon account merge {m['new']} {m['existing']} in a terminal on the Finnamon box." for m in mirrors)


def announce(conn: sqlite3.Connection, item_id: str, mirrors: list[dict] | None = None) -> None:
    chat = store.get_state(conn, "chat_id")
    if chat:
        text = notify.item_linked_summary(conn, item_id)
        first = not store.get_state(conn, "first_link_welcomed") and not conn.execute(
            "SELECT 1 FROM items WHERE item_id<>? AND source='plaid'", (item_id,)).fetchone()   # households linked before this shipped are not first
        if first:
            text += "\n" + notify.first_link_welcome(conn, item_id)
        if mirrors:
            text += "\n\n" + telegram.esc(mirror_note(mirrors))
        telegram.send_message(chat, text)
        if first:
            store.set_state(conn, "first_link_welcomed", store.now_local())
