"""`finnamon demo`: a made-up household in a FINNAMON_HOME of its own (~/.finnamon-demo, FINNAMON_DEMO_HOME overrides),
and the dashboard on a spare port over it, so the dashboard, its charts and tables and the assistant can be tried with no
Plaid keys, no Telegram bot and no scheduler (#112). The same data backs the README's screenshots.

Never the household's own ~/.finnamon: the demo refuses that directory, and --reset deletes only a directory it made."""
from __future__ import annotations

import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from . import assistant, budgets, charts, config, detect, properties, scheduler, store

MARKER = ".finnamon-demo"   # in the demo home: --reset deletes a directory only when this says the demo made it
READY = ".demo-ready"       # written last by build(): a build that died halfway is made again, not reused
STATE = "demo.json"         # {"pid", "started", "port"} of the dashboard the demo started
FIRST_PORT = 8890           # a few above the household's 8888; cookies are named by port, so the two never collide
START_TIMEOUT_S = 20


def home() -> Path:
    return Path(os.environ.get("FINNAMON_DEMO_HOME") or Path.home() / ".finnamon-demo").expanduser().resolve()


def check_home(h: Path) -> None:
    """The demo's directory must not be, contain or sit inside a household's: the default ~/.finnamon or the shell's FINNAMON_HOME."""
    shell = Path(os.environ["FINNAMON_HOME"]).expanduser().resolve() if os.environ.get("FINNAMON_HOME") else None
    if shell == h and (h / MARKER).exists():
        shell = None   # a shell already pointed at the demo (its own intercom, say) is not a household
    for real in {(Path.home() / ".finnamon").resolve(), *([shell] if shell else [])}:
        if h == real or real in h.parents or h in real.parents:
            raise ValueError(f"the demo will not use {h}: it overlaps a household's Finnamon directory ({real}); set FINNAMON_DEMO_HOME elsewhere")


# --- the household ----------------------------------------------------------------------------------------------------

ACCOUNTS = [   # account_id, item_id, name, type, subtype, mask, owner
    ("demo-chk", "demo-chase", "Total Checking", "depository", "checking", "4821", "joint"),
    ("demo-sav", "demo-chase", "Premier Savings", "depository", "savings", "1190", "joint"),
    ("demo-cc", "demo-chase", "Sapphire Preferred", "credit", "credit card", "7710", "alex"),
    ("demo-amex", "demo-amex", "Blue Cash Preferred", "credit", "credit card", "3005", "sam"),
    ("demo-brk", "demo-fid", "Brokerage", "investment", "brokerage", "2231", "sam"),
    ("demo-401k", "demo-fid", "401(k)", "investment", "401k", "9902", "sam"),
    ("demo-mtg", "demo-rocket", "Home Mortgage", "loan", "mortgage", "5521", "joint"),
]
ITEMS = [("demo-chase", "ins_3", "Chase", "alex"), ("demo-amex", "ins_10", "American Express", "sam"),
         ("demo-fid", "ins_12", "Fidelity", "sam"), ("demo-rocket", "ins_rocket", "Rocket Mortgage", "alex")]
G, R, S = "FOOD_AND_DRINK", "RENT_AND_UTILITIES", "GENERAL_MERCHANDISE"
MONTHLY = [   # day, account, amount, raw name, merchant, primary, detailed
    (1, "demo-chk", 2950.00, "ROCKET MORTGAGE PMT", "Rocket Mortgage", "LOAN_PAYMENTS", "LOAN_PAYMENTS_MORTGAGE_PAYMENT"),
    (1, "demo-chk", -4100.00, "ACME CORP PAYROLL", "Acme Corp", "INCOME", "INCOME_WAGES"),
    (15, "demo-chk", -4100.00, "ACME CORP PAYROLL", "Acme Corp", "INCOME", "INCOME_WAGES"),
    (7, "demo-chk", -3650.00, "NORTHWIND HEALTH PAYROLL", "Northwind Health", "INCOME", "INCOME_WAGES"),
    (21, "demo-chk", -3650.00, "NORTHWIND HEALTH PAYROLL", "Northwind Health", "INCOME", "INCOME_WAGES"),
    (16, "demo-chk", 800.00, "TRANSFER TO SAVINGS", None, "TRANSFER_OUT", "TRANSFER_OUT_SAVINGS"),
    (16, "demo-sav", -800.00, "TRANSFER FROM CHECKING", None, "TRANSFER_IN", "TRANSFER_IN_SAVINGS"),
    (18, "demo-chk", 89.99, "COMCAST XFINITY", "Xfinity", R, "RENT_AND_UTILITIES_INTERNET_AND_CABLE"),
    (22, "demo-chk", 118.40, "VERIZON WIRELESS", "Verizon", R, "RENT_AND_UTILITIES_TELEPHONE"),
    (5, "demo-cc", 15.49, "NETFLIX.COM", "Netflix", "ENTERTAINMENT", "ENTERTAINMENT_TV_AND_MOVIES"),
    (9, "demo-cc", 11.99, "SPOTIFY USA", "Spotify", "ENTERTAINMENT", "ENTERTAINMENT_MUSIC_AND_AUDIO"),
    (3, "demo-amex", 89.00, "BAY CLUB FITNESS", "Bay Club", "PERSONAL_CARE", "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS"),
]
SCATTER = [   # per month: how many, account, amount range, raw name, merchant, primary, detailed
    (5, "demo-cc", (70, 190), "WHOLEFDS MKT", "Whole Foods", G, "FOOD_AND_DRINK_GROCERIES"),
    (4, "demo-amex", (40, 120), "TRADER JOE'S #127", "Trader Joe's", G, "FOOD_AND_DRINK_GROCERIES"),
    (3, "demo-cc", (11, 19), "CHIPOTLE 1043", "Chipotle", G, "FOOD_AND_DRINK_RESTAURANT"),
    (4, "demo-amex", (38, 115), "TST* NOPA", "Nopa", G, "FOOD_AND_DRINK_RESTAURANT"),
    (3, "demo-cc", (24, 61), "DOORDASH*THAI HOUSE", "DoorDash", G, "FOOD_AND_DRINK_RESTAURANT"),
    (8, "demo-cc", (5, 9), "BLUE BOTTLE COFFEE", "Blue Bottle Coffee", G, "FOOD_AND_DRINK_COFFEE"),
    (2, "demo-cc", (44, 68), "SHELL OIL 5741", "Shell", "TRANSPORTATION", "TRANSPORTATION_GAS"),
    (5, "demo-amex", (14, 140), "AMAZON MKTPL", "Amazon", S, "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES"),
    (1, "demo-amex", (35, 95), "TARGET 00012", "Target", S, "GENERAL_MERCHANDISE_SUPERSTORES"),
    (1, "demo-chk", (140, 230), "PGANDE WEB ONLINE", "PG&E", R, "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY"),
]
BUDGETS = [("Groceries", "FOOD_AND_DRINK_GROCERIES", 1100), ("Dining", "FOOD_AND_DRINK_RESTAURANT", 350),
           ("Shopping", "GENERAL_MERCHANDISE", 600), ("Coffee", "FOOD_AND_DRINK_COFFEE", 60)]
DINING_A_DAY = 12.40   # the README's and the launch video's numbers (scripts/demo/launch-video.mjs): $248 by Sep 20, pace $372


def _months(today: date, n: int) -> list[date]:
    first = today.replace(day=1)
    out = []
    for _ in range(n):
        out.append(first)
        first = (first - timedelta(days=1)).replace(day=1)
    return out[::-1]


def seed(conn, today: date | None = None, months: int = 6) -> None:
    """The household, relative to `today` so the page always looks current: two people, four banks, seven accounts,
    `months` of transactions and daily balances, budgets, recurring charges, a house and a car, alerts from the real
    detectors (one this month over budget, a duplicate charge, a new subscription, checking under its threshold) and a
    chart board. Deterministic for a given `today`."""
    today = today or date.today()
    rnd = random.Random(112)
    start = _months(today, months)[0]
    now = f"{today} 08:00:00"
    with store.tx(conn):
        for o, n in (("alex", "Alex"), ("sam", "Sam")):
            conn.execute("INSERT INTO owners (owner, display_name) VALUES (?,?)", (o, n))
        for item, ins, inst, owner in ITEMS:
            conn.execute("INSERT INTO items (item_id, institution_id, institution, owner, first_synced_at, last_synced_at, created_at) VALUES (?,?,?,?,?,?,?)",
                         (item, ins, inst, owner, f"{start} 06:00:00", now, f"{start} 06:00:00"))
        conn.executemany("INSERT INTO accounts (account_id, item_id, name, type, subtype, mask, owner) VALUES (?,?,?,?,?,?,?)", ACCOUNTS)
        rows = []

        def tx(d: date, acct, amount, name, merchant, primary, detailed):
            if start <= d <= today:
                rows.append((f"demo-{len(rows):05d}", acct, d.isoformat(), round(amount, 2), name, merchant,
                             f"mch_{merchant.lower().replace(' ', '_')}" if merchant else None, primary, detailed))

        for m in _months(today, months):
            last = ((m + timedelta(days=32)).replace(day=1) - timedelta(days=1)).day
            for day, *rest in MONTHLY:
                tx(m.replace(day=min(day, last)), *rest)
            upto = today.day if m.month == today.month and m.year == today.year else last   # this month: its share so far
            for k, acct, (lo, hi), *rest in SCATTER:
                for _ in range(max(1, round(k * upto / last))):
                    tx(m.replace(day=rnd.randint(1, upto)), acct, rnd.uniform(lo, hi), *rest)
            if m.month == today.month and m.year == today.year:   # dining this month: $12.40 a day, so $248 by the 20th, on pace past its $350
                dining = [i for i, r in enumerate(rows) if r[8] == "FOOD_AND_DRINK_RESTAURANT" and r[2][:7] == m.isoformat()[:7]]
                want = round(DINING_A_DAY * today.day, 2)
                k = want / sum(rows[i][3] for i in dining)
                for i in dining[:-1]:
                    rows[i] = (*rows[i][:3], round(rows[i][3] * k, 2), *rows[i][4:])
                rows[dining[-1]] = (*rows[dining[-1]][:3], round(want - sum(rows[i][3] for i in dining[:-1]), 2), *rows[dining[-1]][4:])
            for acct in ("demo-cc", "demo-amex"):   # each card paid off from checking on the 25th
                spent = sum(r[3] for r in rows if r[1] == acct and r[2][:7] == m.isoformat()[:7])
                tx(m.replace(day=25), "demo-chk", spent, f"{'CHASE' if acct == 'demo-cc' else 'AMEX'} EPAYMENT", None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
                tx(m.replace(day=25), acct, -spent, "PAYMENT THANK YOU", None, "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
        # The stories the alerts tell: a trip two months back, a TV bought this month, a double fill-up, a new subscription.
        tx(_months(today, 3)[0].replace(day=11), "demo-cc", 642.30, "DELTA AIR LINES", "Delta", "TRAVEL", "TRAVEL_FLIGHTS")
        tx(today.replace(day=1), "demo-amex", 899.99, "BEST BUY 00217", "Best Buy", S, "GENERAL_MERCHANDISE_ELECTRONICS")
        for back in (3, 2):
            tx(today - timedelta(days=back), "demo-chk", 52.18, "SHELL OIL 5741", "Shell", "TRANSPORTATION", "TRANSPORTATION_GAS")
        for back in (35, 5):
            tx(today - timedelta(days=back), "demo-cc", 44.00, "PELOTON* MEMBERSHIP", "Peloton", "PERSONAL_CARE", "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS")
        conn.executemany("INSERT INTO transactions (transaction_id, account_id, date, amount, name, merchant_name, merchant_entity_id, "
                         "pfc_primary, pfc_detailed, pfc_confidence, raw_json) VALUES (?,?,?,?,?,?,?,?,?,'HIGH','{}')", rows)

        # Daily balances: a slow walk from where each account started to where it is today.
        ends = {"demo-chk": (6400, 1842.17), "demo-sav": (18200, 22950), "demo-cc": (1900, 2310.55), "demo-amex": (1100, 1488.02),
                "demo-brk": (84000, 91870), "demo-401k": (141000, 152400), "demo-mtg": (418900, 412300)}
        days = (today - start).days
        NOISE = {"demo-chk": 900, "demo-sav": 0, "demo-cc": 400, "demo-amex": 300, "demo-brk": 700, "demo-401k": 900, "demo-mtg": 0}   # dollars a day can wander
        for acct, (a, b) in ends.items():
            for i in range(days + 1):
                v = b if i == days else a + (b - a) * i / days + rnd.uniform(-1, 1) * NOISE[acct]
                conn.execute("INSERT INTO balances VALUES (?,?,?,?)", (acct, f"{start + timedelta(days=i)} 06:00:00", round(v, 2), round(v, 2)))

        def stream(sid, acct, merchant, amount, freq, first, last, nxt, seen, direction="outflow"):
            conn.execute("INSERT INTO recurring (stream_id, account_id, direction, merchant_name, description, frequency, avg_amount, last_amount, "
                         "first_date, last_date, predicted_next_date, status, first_seen_at, is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,'MATURE',?,1)",
                         (sid, acct, direction, merchant, merchant.upper(), freq, amount, amount, first, last, nxt, seen))
        for day, acct, amount, _, merchant, primary, _ in MONTHLY:
            if merchant and primary != "INCOME":
                last = date.fromisoformat(max(r[2] for r in rows if r[5] == merchant))
                stream(f"demo-{merchant.lower().replace(' ', '-')}", acct, merchant, amount, "MONTHLY", str(start), str(last), str(last + timedelta(days=30)), f"{start} 06:00:00")
        stream("demo-acme", "demo-chk", "Acme Corp", 4100, "SEMI_MONTHLY", str(start), str(today), str(today + timedelta(days=15)), f"{start} 06:00:00", "inflow")
        stream("demo-peloton", "demo-cc", "Peloton", 44, "MONTHLY", str(today - timedelta(days=35)), str(today - timedelta(days=5)), str(today + timedelta(days=25)),
               f"{today - timedelta(days=2)} 06:00:00")

    for name, cat, limit in BUDGETS:
        budgets.budget_set(conn, name, limit, cat)
    properties.set_value(conn, "House", 685000)
    properties.set_value(conn, "Car", 21500)
    store.set_setting(conn, "low_balance_threshold", 2500, account_id="demo-chk")
    store.set_state(conn, "last_run", now)
    store.set_state(conn, "demo", "1")   # `finnamon status` says so, and the dashboard's pill reads "Demo household"
    # Alerts the way the daemon raises them: today's open, then what only last month's run would have raised, already dealt with.
    for i in detect.run(conn, f"{today} 08:00:00"):   # delivered, as if a bot had sent them: nothing reads "unsent" in a demo
        conn.execute("UPDATE alerts SET sent_at=? WHERE id=? AND tier='rule'", (now, i))
    for i in detect.run(conn, f"{today.replace(day=1) - timedelta(days=1)} 20:00:00"):
        if conn.execute("SELECT tier FROM alerts WHERE id=?", (i,)).fetchone()[0] == "rule":   # anomalies wait for triage, as they would
            budgets.dismiss(conn, i)
    for name, arg in (("spend_by_category", None), ("monthly_in_out", None), ("recurring", None)):
        charts.write_spec(conn, name, arg, months)


# --- the dashboard over it --------------------------------------------------------------------------------------------

def build(h: Path) -> None:
    """A fresh demo home: the household, the assistant bundle (trusted, so the intercom has its allow list) and the marker."""
    if h.exists() and any(h.iterdir()) and not (h / MARKER).exists():
        raise ValueError(f"{h} exists and was not made by `finnamon demo`; it is left alone. Set FINNAMON_DEMO_HOME to another directory")
    h.mkdir(parents=True, exist_ok=True)
    os.chmod(h, 0o700)
    (h / MARKER).write_text("made by `finnamon demo`; `finnamon demo --reset` deletes this directory\n")
    seed(store.connect())
    assistant.install()
    assistant.trust()
    (h / READY).write_text("")


def _state(h: Path) -> dict:
    try:
        return json.loads((h / STATE).read_text())
    except (OSError, ValueError):
        return {}


def _started(pid: int) -> str:
    """The process's start time as ps prints it ('' = no such process): with the pid, it names one process for good."""
    r = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _ours(st: dict) -> bool:
    """demo.json still names a live process we started: the household's dashboard also runs server.js, and after a reboot
    it may hold the old pid, so the start time has to match too."""
    return bool(st.get("pid") and st.get("started")) and _started(st["pid"]) == st["started"]


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _free_port() -> int:
    for port in range(FIRST_PORT, FIRST_PORT + 100):
        if _port_free(port):
            return port
    raise RuntimeError(f"no free port between {FIRST_PORT} and {FIRST_PORT + 99}; pass --port")


def _up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1):
            return True
    except urllib.error.HTTPError:
        return True   # 401 without the key: it is listening
    except OSError:
        return False


def stop(h: Path) -> bool:
    st = _state(h)
    ours = _ours(st)
    if ours:
        os.kill(st["pid"], signal.SIGTERM)   # the server stops its Claude session and exits (web/server.js shutdown)
        for _ in range(50):
            if not _ours(st):
                break
            time.sleep(0.1)
    (h / STATE).unlink(missing_ok=True)
    return ours


def start(h: Path, port: int | None = None) -> str:
    """The dashboard over the demo home, detached; returns its address with the key."""
    st = _state(h)
    if _ours(st):
        return url(st["port"])
    web = scheduler.web_args()
    if not web:
        raise RuntimeError("the dashboard needs Node 20+ and `npm install` in web/ (cd web && npm install)")
    if port and not _port_free(port):   # something else answering there would pass for the demo
        raise RuntimeError(f"port {port} is in use; pass another --port, or leave it out for the first free one from {FIRST_PORT}")
    port = port or _free_port()
    env = {**os.environ, "FINNAMON_HOME": str(h), "PORT": str(port), "FINNAMON_DEMO": "1",
           "FINNAMON_BIN": f"{sys.executable} -m finnamon.cli", "FINNAMON_REPO": scheduler.repo_dir()}
    for k in ("FINNAMON_ASSISTANT", "FINNAMON_WEB_HOSTS", "CLAUDECODE", "FINNAMON_FROM_CLAUDE"):
        env.pop(k, None)
    with open(h / "web.log", "ab") as log:
        p = subprocess.Popen(web, cwd=scheduler.repo_dir(), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (h / STATE).write_text(json.dumps({"pid": p.pid, "started": _started(p.pid), "port": port}))
    deadline = time.monotonic() + START_TIMEOUT_S
    while not _up(port):
        if p.poll() is not None or time.monotonic() > deadline:   # (the port was free a moment ago, so whatever answers is ours)
            stop(h)
            tail = (h / "web.log").read_text(errors="replace")[-1500:]
            raise RuntimeError(f"the demo dashboard did not start; its log ({h / 'web.log'}):\n{tail}")
        time.sleep(0.2)
    return url(port)


def url(port: int) -> str:
    return f"http://localhost:{port}/?token={config.web_token()}"
