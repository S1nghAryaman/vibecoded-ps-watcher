#!/usr/bin/env python3
"""
Inter IIT Tech Meet 15.0 - High Prep (Quant) PS release watcher  [ntfy version]

Polls the site's own JSON API (the same one the /problem-statement page uses)
and sends a phone push notification via ntfy when a HIGH PREP PS appears.
Quant-looking PSs are sent at max priority (loud, persistent).

Setup (no account, no bot):
  pip install requests
  1. python ps_watcher_ntfy.py --new-topic      # prints a random private topic name
  2. Install the "ntfy" app (Play Store / App Store) -> "+" -> subscribe to that
     topic (server: ntfy.sh, the default).
  3. export NTFY_TOPIC="the-topic-name"          # Windows: set NTFY_TOPIC=...
     python ps_watcher_ntfy.py --test            # phone should buzz
  4. python ps_watcher_ntfy.py                   # start watching (Ctrl+C to stop)

Anyone who knows the topic name can read it, so keep it random and private.

Options:
  --interval 60     seconds between checks (default 60; jitter is added)
  --quant-only      only alert on high-prep PSs that match quant keywords
  --once            run one check and exit (useful for cron / Task Scheduler)
"""

import argparse
import json
import os
import random
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

SITE = "https://interiit-techmeet.com"
API_URL = f"{SITE}/api/v1/problem-statements"
PS_PAGE = f"{SITE}/problem-statement?domain=high-prep"
STATE_FILE = Path(__file__).with_name("ps_watcher_ntfy_state.json")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

QUANT_KEYWORDS = [
    "quant", "trading", "trade", "algo", "algorithmic", "market", "finance",
    "financial", "securities", "stock", "equity", "options", "derivative",
    "portfolio", "alpha", "hft", "high frequency", "order book", "orderbook",
    "backtest", "capital", "fund", "invest", "volatility", "arbitrage",
    "ebullient", "graviton", "jane street", "tower research", "optiver",
    "quadeye", "nk securities", "da vinci", "squarepoint", "worldquant",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (PS-release-watcher; personal use)",
    "Accept": "application/json",
}
FAIL_ALERT_AFTER = 10  # consecutive failed checks before warning you


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------- ntfy ----------------

def ntfy_topic():
    t = os.environ.get("NTFY_TOPIC", "").strip()
    if not t:
        sys.exit("Set NTFY_TOPIC env var first (run with --new-topic to generate one).")
    return t


def notify(text, title="Inter IIT PS watcher", priority=3, tags="bell",
           click=None, retries=3):
    """Push to ntfy. priority: 1 (min) .. 5 (max = loud + persistent)."""
    headers = {
        "Title": title.encode("ascii", "ignore").decode(),  # HTTP headers must be ASCII
        "Priority": str(priority),
        "Tags": tags,  # ntfy turns these into emoji
    }
    if click:
        headers["Click"] = click  # tapping the notification opens this URL
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(f"{NTFY_SERVER}/{ntfy_topic()}",
                              data=text.encode("utf-8"), headers=headers, timeout=15)
            if r.ok:
                return True
            log(f"ntfy error {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log(f"ntfy send failed (attempt {attempt}): {e}")
        time.sleep(3 * attempt)
    return False


# ---------------- PS checking ----------------

def norm_domain(d):
    # Same normalisation the site's frontend uses
    return (d or "").lower().strip().replace("_", "-")


def fetch_ps():
    r = requests.get(API_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):  # tolerate a wrapped response if the API changes
        for key in ("data", "items", "problemStatements", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        raise ValueError(f"Unexpected JSON shape: keys={list(data)[:10]}")
    if not isinstance(data, list):
        raise ValueError(f"Unexpected JSON type: {type(data).__name__}")
    return data


def ps_key(ps):
    return str(ps.get("id") or ps.get("_id") or ps.get("title") or json.dumps(ps, sort_keys=True))


def quant_hits(ps):
    blob = " ".join(
        str(ps.get(k, "")) for k in
        ("title", "company", "description", "summary", "shortDescription", "domain")
    )
    tags = ps.get("tags") or ps.get("keywords") or []
    if isinstance(tags, list):
        blob += " " + " ".join(map(str, tags))
    blob = blob.lower()
    return sorted({k for k in QUANT_KEYWORDS if k in blob})


def format_alert(ps, hits):
    lines = [f"Title: {ps.get('title', '(untitled)')}",
             f"Company: {ps.get('company') or '-'}"]
    if hits:
        lines.append(f"Matched: {', '.join(hits)}")
    if ps.get("pdfUrl") and ps.get("id"):
        lines.append(f"PDF: {API_URL}/{requests.utils.quote(str(ps['id']))}/pdf")
    return "\n".join(lines)


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"seen": [], "total_seen": 0}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def check_once(state, quant_only):
    items = fetch_ps()
    seen = set(state["seen"])
    high = [p for p in items if norm_domain(p.get("domain")) == "high-prep"]
    new_alerts = 0

    for ps in high:
        key = ps_key(ps)
        if key in seen:
            continue
        hits = quant_hits(ps)
        if quant_only and not hits:
            log(f"New high-prep PS (not quant, skipped): {ps.get('title')}")
        else:
            sent = notify(
                format_alert(ps, hits),
                title="QUANT HIGH PREP PS IS OUT" if hits else "New HIGH PREP PS released",
                priority=5 if hits else 4,
                tags="rotating_light,chart_with_upwards_trend" if hits else "rotating_light",
                click=PS_PAGE,
            )
            if not sent:
                log("Notification failed; will retry this PS next check.")
                continue  # don't mark as seen so it retries
            log(f"ALERT SENT: {ps.get('title')}  quant={bool(hits)}")
            new_alerts += 1
        seen.add(key)

    # Heads-up if PSs start appearing in other categories (release has begun)
    if len(items) > state.get("total_seen", 0) and not high:
        others = sorted({norm_domain(p.get("domain")) for p in items})
        notify(f"{len(items)} PS live ({', '.join(others)}). No high-prep yet - still watching.",
               title="PS release has started", priority=4, tags="information_source",
               click=f"{SITE}/problem-statement")
    state["total_seen"] = len(items)
    state["seen"] = sorted(seen)
    save_state(state)
    log(f"Checked: {len(items)} PS total, {len(high)} high-prep, {new_alerts} new alert(s)")
    return new_alerts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--quant-only", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test", action="store_true", help="send a test notification")
    ap.add_argument("--new-topic", action="store_true", help="generate a private topic name")
    args = ap.parse_args()

    if args.new_topic:
        print("interiit-ps-" + secrets.token_hex(6))
        return
    if args.test:
        ok = notify("If you can see this, alerts will reach your phone.",
                    title="PS watcher test", priority=4, tags="white_check_mark")
        print("Sent." if ok else "Failed - check NTFY_TOPIC / internet.")
        return

    state = load_state()
    if args.once:
        check_once(state, args.quant_only)
        return

    log(f"Watching {API_URL} every ~{args.interval}s (quant_only={args.quant_only})")
    notify("You'll get a push when the High Prep PS drops.",
           title="PS watcher started", priority=2, tags="eyes")
    fails = 0
    while True:
        try:
            check_once(state, args.quant_only)
            fails = 0
        except Exception as e:
            fails += 1
            log(f"Check failed ({fails}): {e}")
            if fails == FAIL_ALERT_AFTER:
                notify(f"{fails} failed checks in a row ({e}). The site/API may have "
                       f"changed - check manually.", title="PS watcher WARNING",
                       priority=4, tags="warning", click=f"{SITE}/problem-statement")
        time.sleep(args.interval + random.uniform(0, 10))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")
