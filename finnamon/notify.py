"""Alerts → Telegram. One message per transaction: every alert on it (store.ALERT_GROUP) is one line, stamped together,
so a quoted reply maps to exactly one alert's family.
Sunday roundup: one numbered message for low-confidence anomalies, with roundup_items rows.

    pending alert ──render()──▶ HTML ──send_message──▶ 200: sent_at + telegram_message_id
                                                    └─▶ 429/5xx: stays unsent, retried next run
"""
from __future__ import annotations

import json
import logging
import re
import shlex
import sqlite3
import time
import datetime

from . import store, telegram
from .telegram import esc

log = logging.getLogger("finnamon.notify")
last_error: telegram.TelegramError | None = None   # the latest send failure; `finnamon notify` turns it into its exit code

SENDABLE = "sent_at IS NULL AND resolved_at IS NULL AND (tier = 'rule' OR (verdict = 'promote' AND confidence = 'high'))"   # resolved while queued (Dismiss on the page): never sent
ROUNDUP_CHUNK_CHARS = 3500  # under Telegram's 4096 with HTML entities to spare; never truncate escaped HTML


# NACHA fields after the DES: description; the class code (WEB/PPD/...) only counts at the very end of that tail
_ACH_TAIL = re.compile(r"\s+(ID|INDN|CO ID|PPD ID|CCD ID)\b:?.*$|\s+(PPD|CCD|TEL|WEB|ARC|POS)$", re.IGNORECASE)
# noise anywhere: confirmation numbers, store/check numbers, masked ids, and NACHA person/company fields without a DES: prefix
_NOISE = re.compile(r"\s*\b(Confirmation|Conf|REF)\b\s*[#:]?\s*\S*\d\S*"      # confirmation / reference numbers (must contain a digit)
                    r"|(?<!check )(?<!check)\s*#\s*\d{3,}"                       # store numbers, but a check number is information
                    r"|\s*\bX{3,}\d+"                                             # masked ids
                    r"|\s+\b(INDN|CO ID|PPD ID|CCD ID)\b:.*$"                    # NACHA person/company fields without a DES: prefix
                    r"|(?<=\d)\s+(WEB|PPD|CCD|TEL|ARC)$", re.IGNORECASE)          # a trailing SEC code, only after a number
_CHASE = re.compile(r"ORIG CO NAME:(?P<co>.*?)\s+ORIG ID:.*?CO ENTRY DESCR:(?P<desc>.*?)\s+SEC:", re.IGNORECASE)


def tidy(name) -> str:
    """A bank descriptor the way a person would say it. 'AMERICAN EXPRESS DES:ACH PMT ID:A4476 INDN:Jane Doe CO ID:XXXXX33497 WEB'
    becomes 'AMERICAN EXPRESS (ACH PMT)'; 'Online Banking transfer to CHK 9665 Confirmation# XXXXX40731' becomes
    'Online Banking transfer to CHK 9665'. Plaid's merchant_name is already clean and passes through."""
    s = "" if name is None else str(name)
    m = _CHASE.search(s)                   # Chase-style: ORIG CO NAME:VENMO ORIG ID:... CO ENTRY DESCR:PAYMENT SEC:WEB TRACE#:... IND NAME:...
    if m:
        s = f"{m['co'].strip()} ({m['desc'].strip()})" if m["desc"].strip() else m["co"].strip()
    if " DES:" in s:                       # NACHA-style: <company> DES:<description> ID:<id> INDN:<person> CO ID:<id> <class>
        company, _, rest = s.partition(" DES:")
        desc = _ACH_TAIL.sub("", rest).strip()
        s = f"{company.strip()} ({desc})" if desc else company.strip()
    while (n := _NOISE.sub("", s)) != s:   # to a fixed point, so tidy(tidy(x)) == tidy(x)
        s = n
    s = re.sub(r"\s{2,}", " ", s).strip(" -,").strip()   # .strip(" -,") leaves a tab or an NBSP, and a name of nothing but those is no name
    return s or str(name or "").strip()


def money(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return esc(x)
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def day(iso, year: bool = False) -> str:
    """A date a person reads: 2026-10-01 → Oct 1 (with the year when it isn't this one). Anything that isn't a date passes through."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)", str(iso or ""))
    if not m:
        return esc(iso)
    y, mo, d = map(int, m.groups())
    try:
        s = f"{datetime.date(y, mo, d):%b} {d}"
    except ValueError:
        return esc(iso)
    return f"{s}, {y}" if year or y != datetime.date.today().year else s


def when(ts) -> str:
    """A timestamp without seconds: 2026-10-04 10:22:13 → Oct 4, 10:22 AM."""
    m = re.match(r"(\d{4}-\d\d-\d\d)[ T](\d\d):(\d\d)", str(ts or ""))
    if not m:
        return day(ts)
    h, mi = int(m[2]), m[3]
    return f"{day(m[1])}, {h % 12 or 12}:{mi} {'AM' if h < 12 else 'PM'}"


BANK_PROBLEMS = {   # Plaid's code → what a person would say; the code stays in parentheses
    "INSTITUTION_DOWN": "the bank's site is down", "INSTITUTION_NOT_RESPONDING": "the bank isn't responding", "INSTITUTION_NOT_AVAILABLE": "the bank isn't available right now",
    "RATE_LIMIT_EXCEEDED": "too many requests, so it will retry", "INTERNAL_SERVER_ERROR": "Plaid had a problem on its side", "ITEM_NOT_FOUND": "the connection no longer exists",
    "INVALID_ACCESS_TOKEN": "the saved connection was rejected", "NO_TOKEN": "the connection's key is missing", "ACCESS_NOT_GRANTED": "access to the accounts was not granted",
    "USER_SETUP_REQUIRED": "the bank wants you to finish setup on its own site", "PRODUCTS_NOT_SUPPORTED": "the bank doesn't support what Finnamon asked for",
    "DB_ERROR": "Finnamon couldn't save what the bank sent", "SYNC_ERROR": "the last sync failed", "ADDITIONAL_CONSENT_REQUIRED": "the bank needs a new permission from you",
}


def _title(name) -> str:
    """A budget as the household would say it: stored lowercase, shown capitalized."""
    n = str(name or "")
    return esc(n[:1].upper() + n[1:])


def _fix(p: dict) -> tuple[str, str]:
    """The bank's name and the reply that fixes it; when two logins share the bank, both carry whose login and the item id."""
    bank = esc(p.get("institution") or p.get("item_id"))
    if p.get("duplicate"):
        bank = f"{bank} ({esc(p.get('owner'))}'s login, {esc(p.get('item_id'))})" if p.get("owner") else f"{bank} ({esc(p.get('item_id'))})"
        return bank, f"fix {esc(p.get('item_id'))}"
    return bank, f"fix {bank}"


RELOGIN = ("ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "PENDING_DISCONNECT")   # what an update-mode login fixes


def relogin_item(alert: sqlite3.Row) -> str | None:
    """The Item a re-login fixes, when that is what the alert asks for: the dashboard shows Reconnect for it."""
    p = json.loads(alert["payload_json"])
    if alert["kind"] == "consent_expiring" or (alert["kind"] == "sync_health" and p.get("status") in RELOGIN):
        return p.get("item_id")
    return None


EXPECTED = " Expected? Reply <i>it's normal</i> and I won't ask again."   # the README's line


def render(alert: sqlite3.Row, page: bool = False, cue: bool = True) -> str:
    """Telegram HTML for an alert. The web dashboard puts this straight into the page too, so every bank string
    goes through esc(): this is an XSS boundary, not just Telegram formatting. page: the dashboard's words where it
    shows a Reconnect button instead of "reply fix X". cue=False drops the "reply it's normal" line (the roundup has
    its own footer)."""
    cue = cue and not page
    p = json.loads(alert["payload_json"])
    k = alert["kind"]
    acct = f"{esc(p.get('account'))} …{esc(p.get('mask'))}" if p.get("mask") else esc(p.get("account"))
    if k == "sync_health":
        if p.get("status") in RELOGIN:
            bank, fix = _fix(p)
            return (f"🔌 <b>{bank} needs a re-login.</b> " + ("" if page else f"Reply <i>{fix}</i> and I'll send the login link here. ")
                    + "Until then its accounts aren't updating.")
        doctor = " Run <code>finnamon doctor</code> on the Finnamon box to see what to fix; this closes by itself once the bank syncs again."
        if p.get("status") not in (None, "good"):
            code = str(p.get("status"))
            err = esc(p.get("last_error") or "").rstrip(". ")
            what = BANK_PROBLEMS.get(code, "the sync hit a problem")
            return (f"🔌 <b>{esc(p.get('institution') or p.get('item_id'))} couldn't sync:</b> {what} ({esc(code)})." + (f" {err}." if err else "") + doctor)
        if p.get("source") == "manual":
            return (f"🗂 <b>{esc(p.get('institution') or p.get('item_id'))} hasn't been imported since {when(p.get('last_synced_at')) if p.get('last_synced_at') else 'it was added'}.</b> "
                    f"Download its CSV and use Import CSV on the dashboard, or <code>finnamon import</code>.")
        return f"🩺 <b>{esc(p.get('institution') or p.get('item_id'))} hasn't synced since {when(p.get('last_synced_at')) if p.get('last_synced_at') else 'it was linked'}.</b>" + doctor
    if k == "consent_expiring":   # raised by sync.sync_accounts, once per expiry date
        bank, fix = _fix(p)
        if page:
            return f"⏳ <b>{bank}'s connection expires {day(str(p.get('expires') or '')[:10])}.</b> After that date its accounts stop updating."
        return (f"⏳ <b>{bank}'s connection expires {day(str(p.get('expires') or '')[:10])}.</b> Reply <i>{fix}</i> and I'll send a link "
                f"to renew it here; after that date its accounts stop updating.")
    if k == "duplicate_charge":   # the bank sends no name at all for some charges: say so, the way the anomaly line does
        normal = " Reply <i>it's normal</i> if it was meant." if cue else ""   # the README's line; the page has its own button
        what = f"{esc(tidy(p.get('merchant')) or 'an unnamed charge')} {money(p.get('amount'))} on {acct}"
        same = p.get("date_a") == p.get("date_b")
        if int(p.get("count") or 2) > 2:   # one alert for the whole run of repeats
            span = f"all on {day(p.get('date_b'))}" if same else f"{day(p.get('date_a'))} to {day(p.get('date_b'))}"
            return f"⚠️ <b>Charged {int(p['count'])} times:</b> {what}, {span}. Same merchant, same amount.{normal}"
        span = f"both on {day(p.get('date_a'))}" if same else f"charged {day(p.get('date_a'))} and again {day(p.get('date_b'))}"
        return f"⚠️ <b>Possible duplicate:</b> {what}, {span}. Same merchant, same amount.{normal}"
    if k == "new_recurring":
        return (f"🔁 <b>New recurring charge:</b> {esc(tidy(p.get('merchant')) or 'an unnamed payee')} {money(p.get('amount'))} "
                f"{esc((p.get('frequency') or '').lower())} on {acct}, first seen {day(p.get('first_date'))}." + (EXPECTED if cue else ""))
    if k == "recurring_price":
        return (f"🔁 <b>{esc(tidy(p.get('merchant')) or 'A subscription')} went from {money(p.get('old_amount'))} to {money(p.get('amount'))}</b> "
                f"on {acct}, {day(p.get('date'))}.")
    if k == "channel_deaf":
        n = int(p.get("pending") or 0)
        return (f"🔇 <b>I stopped hearing this chat.</b> {n} message{'' if n == 1 else 's'} {'is' if n == 1 else 'are'} waiting and nothing is collecting them, "
                f"since {esc(p.get('since') or 'a few minutes ago')}. I can still send, which is how you are reading this. "
                "In a terminal on the Finnamon box: <code>finnamon update --no-pull</code> restarts Finnamon and I pick the chat back up.")
    if k == "low_balance":
        return f"💧 <b>{acct} is at {money(p.get('available'))}</b>, below your {money(p.get('threshold'))} threshold."
    if k == "budget_pace":
        if p.get("state") == "over":
            return f"📊 <b>{_title(p.get('budget'))} is over budget:</b> {money(p.get('spent'))} of {money(p.get('limit'))} with {int(p['days']) - int(p['day'])} days left."
        return (f"📊 <b>{_title(p.get('budget'))} on pace to go over:</b> {money(p.get('spent'))} spent by day {esc(p.get('day'))}, "
                f"tracking to {money(p.get('projection'))} against your {money(p.get('limit'))} budget.")
    if k == "detector_error":
        return f"🩺 <b>Detector {esc(p.get('detector'))} failed:</b> {esc(p.get('error'))}"
    if k == "run_error":
        return f"🩺 <b>A sync run crashed:</b> {esc(p.get('error'))} (see the log on the Finnamon box). I'll try again on the next run."
    if k == "triage_error":
        return "🩺 <b>Anomaly triage is failing.</b> On the Finnamon box, run <code>cd ~/.finnamon/assistant &amp;&amp; claude --strict-mcp-config</code> to check the login (or open the dashboard's intercom), then <code>finnamon triage</code>."
    if k.startswith("anomaly:"):
        what = tidy(p.get("merchant") or p.get("name")) or "a transaction"
        amount = next((p[k] for k in ("amount", "last_amount", "avg_amount") if p.get(k) is not None), None)   # a recurring change describes the charge it tracks
        try:
            outgoing = float(amount or 0) >= 0
        except (TypeError, ValueError):
            outgoing = True
        head = f"🔍 <b>{money(amount if outgoing else abs(float(amount)))} {'to' if outgoing else 'from'} {esc(what)}</b>"
        if p.get("account"):
            head += f" on {acct}"
        if p.get("date"):
            head += f", {day(p.get('date'))}"
        if k == "anomaly:first_merchant" and p.get("merchant"):
            head += f": first time at {esc(what)}"
        return f"{head}. {esc(alert['reason'] or '')}".strip() + (EXPECTED if cue else "")
    return f"ℹ️ {esc(k)}: <code>{esc(json.dumps(p)[:500])}</code>"


RESTARTED = "The assistant restarted; resend anything from the last minute."


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(f"SELECT * FROM alerts WHERE {SENDABLE} ORDER BY id").fetchall()


def best(rows: list) -> sqlite3.Row:
    """The one to show for a transaction several alerts fired on: a rule's own words first, then the surest verdict,
    then the fullest reason, then the oldest."""
    return min(rows, key=lambda a: (a["tier"] != "rule", a["verdict"] != "promote", a["confidence"] != "high", -len(a["reason"] or ""), a["id"]))


def _stamp(conn: sqlite3.Connection, ids: list[int], chat_id, mid) -> None:
    conn.execute(f"UPDATE alerts SET sent_at = datetime('now','localtime'), telegram_chat_id = ?, telegram_message_id = ? WHERE id IN ({','.join('?' * len(ids))})",
                 (None if chat_id is None else str(chat_id), mid, *ids))


def _failed(e: telegram.TelegramError) -> None:
    global last_error
    last_error = e


def send_pending(conn: sqlite3.Connection, chat_id: int | str | None = None, sleep=time.sleep) -> int:
    chat_id = chat_id or store.get_state(conn, "chat_id")
    if not chat_id:
        log.warning("no chat_id in state; nothing sent")
        return 0
    sent = tries = 0
    done: set[int] = set()
    for alert in pending(conn):
        if alert["id"] in done:   # folded into a sibling's message (or its failed try) this run
            continue
        done.update(store.alert_group(conn, alert["id"]))
        if tries and tries % 15 == 0:
            sleep(60)  # Telegram allows ~20 messages/min per chat
        tries += 1
        try:
            send_one(conn, alert, chat_id)
        except telegram.TelegramError as e:
            _failed(e)
            log.warning("send failed for alert %s: %s", alert["id"], e)
            if e.code == 429:
                sleep(e.retry_after or 5)
            continue
        sent += 1
    return sent


def send_one(conn: sqlite3.Connection, alert, chat_id: int | str | None = None) -> int | None:
    """Send one alert now, with every other alert pending on its transaction, and stamp them all. Raises TelegramError;
    send_pending handles the retry policy around it. A transaction already told folds into that message, unsent.

    Something that has to be read soon cannot wait for the next cycle: the sync thread runs every
    sync_interval_hours (6 by default), which would make a three-minute detection a six-hour telling."""
    chat_id = chat_id or store.get_state(conn, "chat_id")
    if not chat_id:
        return None
    ids = store.alert_group(conn, alert["id"])
    q = ",".join("?" * len(ids))
    family = [alert] + [r for r in conn.execute(f"SELECT * FROM alerts WHERE id IN ({q}) AND sent_at IS NULL AND resolved_at IS NULL "
                                                "AND (tier = 'rule' OR verdict = 'promote')", ids) if r["id"] != alert["id"]]   # a low one rides along too
    rule = any(a["tier"] == "rule" for a in family)   # a rule is never folded into an anomaly's line or a roundup: it is the stronger news
    told = conn.execute(f"SELECT telegram_chat_id, telegram_message_id FROM alerts WHERE id IN ({q}) AND sent_at IS NOT NULL AND (tier = 'rule' OR NOT ?) "
                        "ORDER BY sent_at, id LIMIT 1", (*ids, rule)).fetchone()
    if told:
        _stamp(conn, [a["id"] for a in family], told[0], told[1])
        return told[1]
    mid = telegram.send_message(chat_id, render(best(family)))
    _stamp(conn, [a["id"] for a in family], chat_id, mid)
    return mid


def send_roundup(conn: sqlite3.Connection, as_of: str, chat_id: int | str | None = None, force: bool = False) -> int | None:
    """Sunday: one numbered message with the week's low-confidence anomalies, or one all-quiet line when there are
    none, because a quiet watchdog must not look like a dead one. A Sunday the machine slept through is sent on the
    Monday, and not again. Returns message_id or None."""
    dow = conn.execute("SELECT strftime('%w', ?)", (as_of,)).fetchone()[0]
    week = conn.execute("SELECT strftime('%Y-%W', date(?, ?))", (as_of, "-1 days" if dow == "1" else "+0 days")).fetchone()[0]   # Monday belongs to the Sunday before
    if not force and (dow not in ("0", "1") or store.get_state(conn, "last_roundup_week") == week):
        return None
    chat_id = chat_id or store.get_state(conn, "chat_id")
    in_slot = dow in ("0", "1")   # a forced roundup mid-week must not stamp, and so cancel, the coming Sunday's
    if not chat_id:
        if in_slot:
            store.set_state(conn, "last_roundup_week", week)
        return None
    rows = conn.execute(f"SELECT *, {store.ALERT_GROUP} AS grp FROM alerts WHERE sent_at IS NULL AND resolved_at IS NULL AND verdict='promote' AND confidence='low' ORDER BY id").fetchall()
    families: dict[str, list] = {}   # one numbered item per transaction; the rest of its alerts ride on that item's message
    for r in rows:
        families.setdefault(r["grp"], []).append(r)
    for g, fam in list(families.items()):   # a transaction already told (a rule fired on it) is not told again
        ids = store.alert_group(conn, fam[0]["id"])
        told = conn.execute(f"SELECT telegram_chat_id, telegram_message_id FROM alerts WHERE id IN ({','.join('?' * len(ids))}) AND sent_at IS NOT NULL "
                            "ORDER BY sent_at, id LIMIT 1", ids).fetchone()
        if told:
            _stamp(conn, [a["id"] for a in fam], told[0], told[1])
            del families[g]
    rows = list(families.values())
    if not rows:
        try:
            mid = telegram.send_message(chat_id, quiet_line(conn, as_of))
        except telegram.TelegramError as e:
            _failed(e)
            log.warning("all-quiet roundup failed: %s (the week is not marked done)", e)
            return None
        if in_slot:
            store.set_state(conn, "last_roundup_week", week)
        return mid
    # One numbered list, chunked under Telegram's limit; each chunk is its own message with its own roundup_items rows.
    items = [(n, fam, f"{n}. {render(best(fam), cue=False).replace('🔍 ', '')}") for n, fam in enumerate(rows, 1)]
    footer = "\nReply <i>normal 3</i> (etc.) to stop hearing about a pattern."
    chunks: list[list[tuple]] = [[]]
    size = len("📋 <b>Things I noticed this week</b>")
    for it in items:
        if chunks[-1] and size + len(it[2]) + len(footer) > ROUNDUP_CHUNK_CHARS:
            chunks.append([]); size = 0
        chunks[-1].append(it); size += len(it[2]) + 1
    first_mid = None
    for ci, chunk in enumerate(chunks):
        head = "📋 <b>Things I noticed this week</b>" + (f" ({ci + 1}/{len(chunks)})" if len(chunks) > 1 else "")
        text = "\n".join([head, *[it[2] for it in chunk]]) + (footer if ci == len(chunks) - 1 else "")
        try:
            mid = telegram.send_message(chat_id, text)
        except telegram.TelegramError as e:
            _failed(e)
            log.warning("roundup chunk %d failed: %s (alerts stay unsent; the week is not marked done)", ci, e)
            return first_mid
        first_mid = first_mid or mid
        with store.tx(conn):
            for n, fam, _ in chunk:
                _stamp(conn, [a["id"] for a in fam], chat_id, mid)
                conn.execute("INSERT INTO roundup_items (telegram_chat_id, telegram_message_id, n, alert_id) VALUES (?,?,?,?)", (str(chat_id), mid, n, best(fam)["id"]))
    if first_mid and in_slot:
        store.set_state(conn, "last_roundup_week", week)
    return first_mid


def quiet_line(conn: sqlite3.Connection, as_of: str) -> str:
    """The roundup of a week with nothing in it: still proof that syncing and watching happened."""
    # min, not max: the proof is only as fresh as the stalest bank, and a dead one must not hide behind a live one
    accts, oldest = conn.execute("SELECT count(*), min(i.last_synced_at) FROM accounts a JOIN items i ON i.item_id=a.item_id WHERE a.mirror_of IS NULL").fetchone()
    n = conn.execute("SELECT count(*) FROM tx_now WHERE date > date(?, '-7 days')", (as_of,)).fetchone()[0]
    sent = conn.execute("SELECT count(*) FROM alerts WHERE sent_at > datetime(?, '-7 days')", (as_of,)).fetchone()[0]
    # untriaged candidates (triage down) and alerts whose send failed are not "nothing"
    waiting = conn.execute(f"SELECT count(*) FROM alerts WHERE (tier='anomaly' AND verdict IS NULL AND sent_at IS NULL) OR ({SENDABLE})").fetchone()[0]
    if waiting:
        flagged = f"{waiting} alert{'' if waiting == 1 else 's'} {'is' if waiting == 1 else 'are'} still waiting to be checked or sent"
    elif sent:
        flagged = f"nothing to add to the {sent} alert{'' if sent == 1 else 's'} already sent"
    else:
        flagged = "nothing flagged"
    head = f"🟡 <b>A quiet week, but {flagged}.</b>" if waiting else "🟢 <b>All quiet this week.</b>"
    tail = "" if waiting else f" {flagged[0].upper() + flagged[1:]}."
    return (f"{head} {accts} account{'' if accts == 1 else 's'} watched, all synced by {esc(when(oldest) if oldest else 'never')}; "
            f"{n:,} transaction{'' if n == 1 else 's'} in the last 7 days.{tail}")


SUGGESTED_LOW_BALANCE = 500


def first_link_welcome(conn: sqlite3.Connection, item_id: str) -> str:
    """Appended to the household's first linked-bank summary, once: what is watched now, and what turns on more."""
    lines = ["\n<b>What I watch from now on:</b> duplicate charges, new subscriptions and price changes, anything unusual "
             "(I check it before I bother you), and banks that stop syncing. Urgent things come right away; the rest waits "
             "for a Sunday roundup, which comes every week, even if only to say all is quiet.",
             "\n<b>To get more out of week one:</b>",
             "• Budgets turn on pace alerts: reply <i>set up my budgets</i> and I'll propose some from your history."]
    chk = conn.execute("SELECT account_id, name FROM accounts WHERE item_id=? AND type='depository' AND mirror_of IS NULL "
                       "ORDER BY coalesce(subtype,'')<>'checking', name LIMIT 1", (item_id,)).fetchone()
    if chk and store.setting(conn, "low_balance_threshold", chk[0]) is None:
        lines.append(f"• Low-balance alerts are off. Reply <i>alert me if {esc(chk[1])} drops below ${SUGGESTED_LOW_BALANCE}</i> "
                     f"(or any amount) to turn them on, or run "
                     f"<code>finnamon threshold {esc(shlex.quote(chk[1]))} {SUGGESTED_LOW_BALANCE}</code>.")
    return "\n".join(lines)


def item_linked_summary(conn: sqlite3.Connection, item_id: str, as_of: str | None = None) -> str:
    """One-time summary after a new Item's first sync (User Workflow Step 2).

    A list, and no balances or amounts: the chat is shared and scrolls back forever. Numbers are one question away."""
    as_of = as_of or store.now_local()
    inst = conn.execute("SELECT institution FROM items WHERE item_id=?", (item_id,)).fetchone()[0] or item_id
    accts = conn.execute("SELECT name, mask, type, subtype FROM accounts WHERE item_id=? ORDER BY type, name", (item_id,)).fetchall()
    n, oldest = conn.execute("SELECT count(*), min(date) FROM transactions t JOIN accounts a ON a.account_id=t.account_id WHERE a.item_id=?", (item_id,)).fetchone()
    holdings = conn.execute("SELECT count(*) FROM holdings h JOIN accounts a ON a.account_id=h.account_id WHERE a.item_id=?", (item_id,)).fetchone()[0]
    # NULLIF before COALESCE: Plaid sends '' as well as NULL, and a plain COALESCE would take the '' and never reach the
    # description, so a stream whose name lives there would be dropped by the filter rather than named by it.
    rec = conn.execute("SELECT DISTINCT COALESCE(NULLIF(trim(merchant_name),''), NULLIF(trim(description),'')) m FROM recurring r JOIN accounts a ON a.account_id=r.account_id "
                       "WHERE a.item_id=? AND r.direction='outflow' AND r.status<>'EARLY_DETECTION' AND m IS NOT NULL "
                       "ORDER BY last_amount DESC LIMIT 8", (item_id,)).fetchall()   # the nameless rows go in SQL so they cannot spend one of the 8 slots
    # merchants, not money moving between the household's own accounts or onto its own loans and cards.
    # tx_all_accounts, not tx_now: `finnamon link` marks a duplicate account as a mirror before it sends this,
    # so tx_now would report the bank it just linked as empty. Scoped to one Item, so nothing double-counts.
    # display before the raw name for the same reason as the NULLIF above: an alias is the household's own name
    # for a payee, but COALESCE stops at '' rather than at NULL, so a blank one must still fall through.
    top = conn.execute("SELECT COALESCE(NULLIF(trim(display),''), NULLIF(trim(name),'')) m FROM tx_all_accounts "
                       "WHERE item_id=? AND amount>0 AND pending=0 AND date>=date(?,'-90 days') "
                       "AND COALESCE(category_primary,'') NOT IN ('TRANSFER_IN','TRANSFER_OUT','LOAN_PAYMENTS') AND m IS NOT NULL "
                       "GROUP BY m ORDER BY sum(amount) DESC LIMIT 5", (item_id, as_of)).fetchall()
    lines = [f"<b>Linked {esc(inst)}</b> ({len(accts)} account{'' if len(accts) == 1 else 's'}):"]
    for name, mask, typ, sub in accts:
        kind = (sub or typ or "").replace("_", " ")
        lines.append(f"• {esc(name)}" + (f" …{esc(mask)}" if mask else "") + (f" ({esc(kind)})" if kind else ""))
    facts = [f"{n:,} transaction{'' if n == 1 else 's'} since {day(oldest, year=True)}" if n else
             ("no transactions to show (an investment or loan account often has none)" if any(t in ("investment", "loan") for _, _, t, _ in accts) else "no transactions yet")]
    if holdings:
        facts.append(f"{holdings} holdings")
    lines.append("\n" + ", ".join(facts) + ".")
    def bullets(rows):   # a row the bank left nameless is no bullet, and an empty list is no heading either
        return [f"• {esc(t)}" for r in rows if (t := tidy(r[0]))]   # tidy() is the one place a name of pure whitespace becomes no name
    for head, rows in (("Recurring I can see:", rec), ("Top merchants, last 90 days:", top)):
        if (b := bullets(rows)):
            lines.append(f"\n<b>{head}</b>")
            lines += b
    lines.append("\nI'll start watching from today. Balances and amounts: just ask me here.")
    return "\n".join(lines)
