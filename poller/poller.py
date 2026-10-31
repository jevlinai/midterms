"""Election-night poller: one Jevlin search per active race per round, every engine in the same call.

Every response is stored verbatim (gzip JSONL, one file per round) under raw/. The poller
never decides a result; the detector and the confirm tool read what it stores.

  python3 -I poller/poller.py --dry-run                      # print the schedule and cost, no calls
  python3 -I poller/poller.py --once --races senate-ga --budget-usd 0.10   # one round, smoke test
  python3 -I poller/poller.py --budget-usd 300               # election night

A live run needs --budget-usd: the poller stops itself when Jevlin's reported spend reaches it.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import gzip
import json
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
ET = dt.timezone(dt.timedelta(hours=-5))  # EST; DST ends 1 Nov, before the election
USER_AGENT = "jevlin-midterms-poller/1"


def log(*parts):
    stamp = dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    print(stamp, *parts, file=sys.stderr, flush=True)


def alert(msg):
    """Log loudly, and post to ALERT_WEBHOOK (Slack/Discord-style JSON) if set."""
    log("ALERT", msg)
    url = os.environ.get("ALERT_WEBHOOK")
    if not url:
        return
    body = json.dumps({"text": f"[midterms poller] {msg}", "content": f"[midterms poller] {msg}"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, body, {"content-type": "application/json"}), timeout=10)
    except Exception as e:  # an alert failure must never stop polling
        log("alert webhook failed:", type(e).__name__)


def load_key():
    key = os.environ.get("JEVLIN_API_KEY")
    if key:
        return key
    path = pathlib.Path(os.environ.get("JEVLIN_CREDENTIALS", "~/.jevlin/credentials.json")).expanduser()
    return json.loads(path.read_text())["api_key"]


def parse_time(s):
    return dt.datetime.fromisoformat(s)


# ---------- schedule ----------

def round_start(t, minutes):
    """The round boundary at or before t, aligned to the clock (e.g. :00, :05, :10)."""
    t = t.astimezone(ET).replace(second=0, microsecond=0)
    return t - dt.timedelta(minutes=t.minute % minutes)


def active_races(races, tick, cfg, inactive):
    """Races to search in the round starting at `tick`.

    Night cadence: from poll close until close + active_hours.
    After that: only on follow-up boundaries (hourly), until followup.ends_at.
    A race the detector has marked inactive (every engine found the confirmed winner in two
    consecutive rounds) is never searched again.
    """
    night_h = cfg["night"]["active_hours"]
    fu_min = cfg["followup"]["round_minutes"]
    fu_end = parse_time(cfg["followup"]["ends_at"])
    out = []
    for r in races:
        if r.get("uncontested") or r["id"] in inactive:
            continue
        close = parse_time(r["poll_close_et"])
        if tick < close:
            # Pre-close sweeps: pages that already name a winner before polls close can't be
            # reporting a result, so build_state excludes their sites (docs/SCORING.md).
            if any(tick == round_start(close - dt.timedelta(minutes=m), cfg["night"]["round_minutes"])
                   for m in cfg.get("preclose_sweep_minutes", [])):
                out.append(r)
            continue
        if tick < close + dt.timedelta(hours=night_h):
            out.append(r)
        elif tick < fu_end and tick.astimezone(ET).minute % fu_min == 0:
            out.append(r)
    return out


def build_request(race, cfg):
    return {
        "query": race["query"],
        "providers": cfg["providers"],
        "tier": cfg["tier"],
        "recency": cfg["recency"],
        "view": cfg["view"],
        "merged_limit": cfg["merged_limit"],
    }


# ---------- one search ----------

def search(race, round_id, cfg, key):
    """One call for one race. Retries once on 429 or 5xx with the same Idempotency-Key,
    so a retry that reaches a request which already ran is replayed, not billed again."""
    body = build_request(race, cfg)
    data = json.dumps(body).encode()
    headers = {
        "authorization": f"Bearer {key}",
        "content-type": "application/json",
        "user-agent": USER_AGENT,
        "idempotency-key": f"midterms-{race['id']}-{round_id}",
    }
    rec = {"round": round_id, "race_id": race["id"], "request": body, "attempts": []}
    for attempt in (1, 2):
        sent = dt.datetime.now(dt.timezone.utc)
        status, resp, err, hdrs = None, None, None, {}
        try:
            req = urllib.request.Request(cfg["endpoint"], data, headers, method="POST")
            with urllib.request.urlopen(req, timeout=cfg["timeout_seconds"]) as r:
                status, raw, hdrs = r.status, r.read(), dict(r.headers)
            resp = json.loads(raw)
        except urllib.error.HTTPError as e:
            status, hdrs = e.code, dict(e.headers or {})
            try:
                resp = json.loads(e.read())
            except Exception:
                resp = None
            err = f"http {e.code}"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:300]
        got = dt.datetime.now(dt.timezone.utc)
        rec["attempts"].append({
            "attempt": attempt, "sent_at": sent.isoformat(), "received_at": got.isoformat(),
            "http_status": status, "error": err,
            "rate_limit": {k: v for k, v in hdrs.items() if k.lower().startswith(("x-ratelimit", "retry-after", "x-request-id", "idempotent"))},
        })
        retryable = status is None or status == 429 or (status is not None and status >= 500)
        if status == 200 or not retryable or attempt == 2:
            break
        wait = min(float(hdrs.get("Retry-After") or hdrs.get("retry-after") or 5), 30)
        log(f"retry {race['id']} after {err}, waiting {wait:.0f}s")
        time.sleep(wait)
    rec["http_status"] = status
    rec["response"] = resp
    rec["summary"] = summarize(resp, cfg) if status == 200 and isinstance(resp, dict) else None
    return rec


def summarize(resp, cfg):
    """Per-engine status, latency, cost and result count, plus the two things that would
    invalidate a round: a trimmed engine, and a cache hit (results replayed from an earlier call)."""
    engines = {}
    for c in resp.get("candidates", []):
        engines[c.get("provider")] = {
            "status": c.get("status"),
            "latency_ms": (c.get("latency") or {}).get("total_ms"),
            "cost_micros": c.get("cost_micros"),
            "results": len(c.get("citations") or []),
            "error": c.get("error"),
        }
    usage = resp.get("usage") or {}
    decision = resp.get("decision") or {}
    return {
        "request_id": resp.get("request_id"),
        "cache_hit": bool(usage.get("cache_hit")),
        "deadline_hit": bool(usage.get("deadline_hit")),
        "cost_micros": usage.get("cost_micros"),
        "latency_ms": usage.get("latency_ms"),
        "trimmed": decision.get("trimmed") or [],
        "missing": sorted(set(cfg["providers"]) - set(engines)),
        "engines": engines,
    }


# ---------- one round ----------

class Spend:
    def __init__(self, budget_usd):
        self.budget_micros = int(budget_usd * 1e6) if budget_usd is not None else None
        self.micros = 0
        self.lock = threading.Lock()

    def add(self, micros):
        with self.lock:
            self.micros += micros or 0

    def exceeded(self):
        return self.budget_micros is not None and self.micros >= self.budget_micros


def run_round(tick, races, cfg, key, spend, raw_dir):
    """Spread the round's calls evenly over spread_fraction of the round, so the router sees a
    steady rate instead of a burst. Each race still gets exactly one call, with every engine in it."""
    round_id = tick.astimezone(ET).strftime("%Y%m%dT%H%M")
    minutes = cfg["night"]["round_minutes"]
    window = minutes * 60 * cfg["spread_fraction"]
    gap = window / max(len(races), 1)
    out_path = raw_dir / tick.astimezone(ET).strftime("%Y-%m-%d") / f"round-{round_id}.jsonl.gz"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    log(f"round {round_id}: {len(races)} races, one call every {gap:.1f}s")

    stats = {"ok": 0, "failed": 0, "cache_hits": 0, "trimmed": 0}
    with gzip.open(out_path, "at") as fh, cf.ThreadPoolExecutor(cfg["max_workers"]) as pool:
        def done(fut):
            rec = fut.result()
            s = rec["summary"]
            with lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if s is None:
                    stats["failed"] += 1
                    alert(f"{rec['race_id']} round {round_id}: request failed ({rec['attempts'][-1]['error']})")
                    return
                stats["ok"] += 1
                spend.add(s["cost_micros"])
                if s["cache_hit"]:
                    stats["cache_hits"] += 1
                if s["trimmed"] or s["missing"]:
                    stats["trimmed"] += 1
                    alert(f"{rec['race_id']} round {round_id}: engines dropped trimmed={s['trimmed']} missing={s['missing']}")

        start = time.monotonic()
        for i, race in enumerate(races):
            if spend.exceeded():
                alert(f"budget reached (${spend.micros/1e6:.2f}); stopping before {race['id']}")
                break
            delay = start + i * gap - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            pool.submit(search, race, round_id, cfg, key).add_done_callback(done)

    if stats["cache_hits"]:
        alert(f"round {round_id}: {stats['cache_hits']} cache hits; those calls replayed earlier results and do not count")
    log(f"round {round_id} done: {stats}, spend so far ${spend.micros/1e6:.3f}")
    return stats


def load_inactive():
    p = ROOT / "state" / "inactive.json"
    if not p.exists():
        return set()
    return set(json.loads(p.read_text()))


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--races-file", default=str(ROOT / "data" / "races.json"))
    ap.add_argument("--config", default=str(ROOT / "poller" / "config.json"))
    ap.add_argument("--raw-dir", default=str(ROOT / "raw"))
    ap.add_argument("--races", help="comma-separated race ids to limit to (smoke tests)")
    ap.add_argument("--once", action="store_true", help="run one round now, for the given races, ignoring poll close times")
    ap.add_argument("--dry-run", action="store_true", help="print the schedule and estimated cost; no calls")
    ap.add_argument("--budget-usd", type=float, help="required for live runs: stop when Jevlin's reported spend reaches this")
    args = ap.parse_args()

    cfg = json.loads(pathlib.Path(args.config).read_text())
    races = json.loads(pathlib.Path(args.races_file).read_text())["races"]
    if args.races:
        want = set(args.races.split(","))
        unknown = want - {r["id"] for r in races}
        if unknown:
            sys.exit(f"unknown race ids: {sorted(unknown)}")
        races = [r for r in races if r["id"] in want]

    if args.dry_run:
        return dry_run(races, cfg)
    if args.budget_usd is None:
        sys.exit("a live run needs --budget-usd (see CLAUDE.md rule 6)")

    key = load_key()
    spend = Spend(args.budget_usd)
    raw_dir = pathlib.Path(args.raw_dir)
    minutes = cfg["night"]["round_minutes"]

    if args.once:
        tick = round_start(dt.datetime.now(ET), minutes)
        run_round(tick, races, cfg, key, spend, raw_dir)
        return

    fu_end = parse_time(cfg["followup"]["ends_at"])
    last = None
    while True:
        now = dt.datetime.now(ET)
        tick = round_start(now, minutes)
        if tick >= fu_end:
            log("follow-up window over; exiting")
            return
        if last is not None and tick > last + dt.timedelta(minutes=minutes):
            alert(f"missed round(s) between {last:%H:%M} and {tick:%H:%M}")
        if tick != last:
            last = tick
            todo = active_races(races, tick, cfg, load_inactive())
            if todo:
                run_round(tick, todo, cfg, key, spend, raw_dir)
            if spend.exceeded():
                alert(f"budget of ${args.budget_usd} reached; poller stopped")
                return
        nxt = tick + dt.timedelta(minutes=minutes)
        time.sleep(max(1.0, (nxt - dt.datetime.now(ET)).total_seconds()))


def dry_run(races, cfg):
    """Simulate the night with no stop rule (every race runs its full window): an upper bound."""
    minutes = cfg["night"]["round_minutes"]
    first = min(parse_time(r["poll_close_et"]) for r in races)
    tick = round_start(first, minutes)
    end = parse_time(cfg["followup"]["ends_at"])
    checks, peak, rounds = 0, 0, 0
    while tick < end:
        n = len(active_races(races, tick, cfg, set()))
        if n:
            rounds += 1
            checks += n
            peak = max(peak, n)
        tick += dt.timedelta(minutes=minutes)
    per = cfg["est_cost_per_check_usd"]
    print(f"races: {len(races)} (uncontested skipped: {sum(1 for r in races if r.get('uncontested'))})")
    print(f"first round: {round_start(first, minutes):%a %d %b %H:%M} ET; follow-up ends {end:%a %d %b %H:%M} ET")
    print(f"rounds with work: {rounds}; peak races in one round: {peak} "
          f"({peak * len(cfg['providers']) / (minutes * 60 * cfg['spread_fraction']):.1f} engine calls/s)")
    print(f"checks with NO stop rule: {checks} = ${checks * per:,.0f} at ${per}/check (upper bound; "
          f"the stop rule ends a race once every engine has shown the confirmed winner twice)")


if __name__ == "__main__":
    main()
