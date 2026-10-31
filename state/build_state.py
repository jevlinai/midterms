"""Build state/state.json (docs/STATE.md) from the race list, the detector's findings and the
confirmed calls; optionally push it to Workers KV for the page.

  python3 -I state/build_state.py [--findings state/findings-page.jsonl] [--push]

Rules carried into the file (docs/SCORING.md):
- A race shows a winner only when its call is confirmed (two people for Senate and Governor).
- found_at / wrong_at are measured against the confirmed winner, so they exist only for called races.
- Rounds that were cache hits don't count, for any engine.
- "jevlin" = the earliest of the 7 engines; "agent" = the agent track, Senate and Governor only.

Also writes state/inactive.json for the poller: called races where every one of the 7 engines has
named the confirmed winner in two consecutive rounds.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import statistics
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from confirm.confirm import is_called, load, save_atomic  # noqa: E402
from detector.races import ROOT, load_races  # noqa: E402

ET = dt.timezone(dt.timedelta(hours=-5))
ENGINES = [
    ("exa", "Exa", 0.007), ("brave", "Brave", 0.005), ("parallel-search", "Parallel", 0.005),
    ("perplexity-search", "Perplexity", 0.005), ("serper", "Google (via Serper)", 0.0003),
    ("tinyfish", "TinyFish", 0.0), ("telem", "Telem", 0.00505),
]
SEVEN = [e for e, _, _ in ENGINES]
COMPETITORS = SEVEN + ["agent"]


def round_time(round_id):
    return dt.datetime.strptime(round_id, "%Y%m%dT%H%M").replace(tzinfo=ET)


def minutes(a, b):
    return None if a is None or b is None else round((a - b).total_seconds() / 60)


def iso(t):
    return t.isoformat() if t else None


def read_findings(path):
    """{race_id: {engine: [(round_time, named_set, status, url_by_candidate)]}} sorted by round."""
    out = {}
    if not path.exists():
        return out
    for line in path.open():
        f = json.loads(line)
        if f.get("cache_hit"):
            continue
        t = round_time(f["round"])
        for e, v in f["engines"].items():
            out.setdefault(f["race_id"], {}).setdefault(e, []).append(
                (t, set(v.get("named") or {}), v.get("status"), v.get("named") or {}))
    for r in out.values():
        for e in r:
            r[e].sort(key=lambda x: x[0])
    return out


def engine_result(rows, winner, close, call_t, others):
    found = next(((t, urls) for t, named, st, urls in rows if st == "ok" and winner in named), None)
    wrong = next((t for t, named, st, _ in rows if st == "ok" and named & others), None)
    t = found[0] if found else None
    return {
        "found_at": iso(t), "minutes_after_close": minutes(t, close), "minutes_vs_call": minutes(t, call_t),
        "lead_vs_google": None, "wrong_at": iso(wrong),
        "url": (found[1].get(winner) or [None])[0] if found else None,
        "failed_rounds": sum(1 for _, _, st, _ in rows if st != "ok"),
        "_t": t, "_wrong": wrong,
    }


def twice_in_a_row(rows, winner):
    ok = [winner in named for _, named, st, _ in rows if st == "ok"]
    return any(a and b for a, b in zip(ok, ok[1:]))


def build(races, findings, confirmed, pending, prereg, now):
    out_races, inactive = [], []
    for r in races.values():
        close = dt.datetime.fromisoformat(r["poll_close_et"])
        c = confirmed.get(r["id"])
        called = is_called(c, r)
        status = "called" if called else ("closed" if now >= close else "open")
        race = {
            "id": r["id"], "office": r["office"], "state": r["state"], "district": r["district"],
            "name": r["name"], "poll_close_et": r["poll_close_et"], "status": status,
            "pending_reason": pending.get(r["id"]) if status == "closed" else None,
            "candidates": [{"name": x["name"], "party": x["party"], "incumbent": x["incumbent"]} for x in r["candidates"]],
            "call": None, "engines": {}, "first": [],
        }
        if called:
            winner = c["winner"]
            call_t = dt.datetime.fromisoformat(c["ap_call_time"])
            race["call"] = {"winner": winner, "party": c["party"], "ap_call_time": c["ap_call_time"],
                            "ap_url": c["ap_url"], "confirmed_by": c["confirmed_by"]}
            others = {x["name"] for x in r["candidates"]} - {winner}
            rows = findings.get(r["id"], {})
            res = {e: engine_result(rows[e], winner, close, call_t, others) for e in COMPETITORS if e in rows}
            seven = [res[e] for e in SEVEN if e in res]
            if seven:
                ts = [x["_t"] for x in seven if x["_t"]]
                ws = [x["_wrong"] for x in seven if x["_wrong"]]
                t, w = (min(ts) if ts else None), (min(ws) if ws else None)
                res["jevlin"] = {"found_at": iso(t), "minutes_after_close": minutes(t, close),
                                 "minutes_vs_call": minutes(t, call_t), "lead_vs_google": None, "wrong_at": iso(w),
                                 "url": next((x["url"] for x in seven if x["_t"] == t), None) if t else None,
                                 "failed_rounds": 0, "_t": t, "_wrong": w}
            g = res.get("serper", {}).get("_t")
            for e, x in res.items():
                if e != "serper" and g and x["_t"]:
                    x["lead_vs_google"] = minutes(g, x["_t"])
                elif e == "serper" and g:
                    x["lead_vs_google"] = 0
            best = min((x["_t"] for e, x in res.items() if e in COMPETITORS and x["_t"]), default=None)
            race["first"] = sorted(e for e, x in res.items() if e in COMPETITORS and best and x["_t"] == best)
            race["engines"] = {e: {k: v for k, v in x.items() if not k.startswith("_")} for e, x in res.items()}
            if all(e in rows and twice_in_a_row(rows[e], winner) for e in SEVEN):
                inactive.append(r["id"])
        out_races.append(race)

    called = [x for x in out_races if x["status"] == "called"]
    board = []
    costs = {e: c for e, _, c in ENGINES} | {"jevlin": sum(c for _, _, c in ENGINES), "agent": None}
    for e in SEVEN + ["jevlin", "agent"]:
        rows = [x["engines"][e] for x in called if e in x["engines"]]
        got = [y["minutes_after_close"] for y in rows if y["found_at"]]
        leads = [y["lead_vs_google"] for y in rows if y["lead_vs_google"] is not None]
        board.append({
            "engine": e, "races_scored": len(rows),
            "median_minutes_after_close": statistics.median(got) if got else None,
            "found_within": {k: sum(1 for m in got if m <= int(k)) for k in ("30", "60", "180")},
            "found_first": sum(1 for x in called if e in x["first"]),
            "median_lead_vs_google": statistics.median(leads) if leads else None,
            "never_found": sum(1 for y in rows if not y["found_at"]),
            "wrong": sum(1 for y in rows if y["wrong_at"]),
            "failed_rounds": sum(y["failed_rounds"] for y in rows),
            "cost_per_check_usd": costs[e],
        })
    ranked = sorted((b for b in board if b["median_minutes_after_close"] is not None and b["engine"] != "jevlin"),
                    key=lambda b: b["median_minutes_after_close"])
    engines = [{"id": e, "label": l, **({"baseline": True} if e == "serper" else {})} for e, l, _ in ENGINES]
    engines += [{"id": "jevlin", "label": "Jevlin, all engines", "computed": True},
                {"id": "agent", "label": "Agent on Jevlin", "agent": True}]
    state = {
        "version": 1, "generated_at": now.isoformat(), "round_minutes": 5, "prereg": prereg,
        "engines": engines,
        "summary": {"races": len(out_races), "polls_closed": sum(x["status"] != "open" for x in out_races),
                    "called": len(called), "leader": ranked[0]["engine"] if ranked else None},
        "races": out_races, "leaderboard": board,
    }
    return state, inactive


def read_prereg():
    p = ROOT / "PREREGISTRATION.md"
    if not p.exists():
        return {"sha256": None, "committed_at": None, "url": None}
    text = p.read_text()
    sha = re.search(r"\b[0-9a-f]{64}\b", text)
    at = re.search(r"committed_at:\s*(\S+)", text)
    return {"sha256": sha.group(0) if sha else None, "committed_at": at.group(1) if at else None,
            "url": os.environ.get("PREREG_URL")}


def push_kv(state):
    acct, ns, token = (os.environ.get(k) for k in ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN"))
    if not all((acct, ns, token)):
        sys.exit("--push needs CF_ACCOUNT_ID, CF_KV_NAMESPACE_ID and CF_API_TOKEN")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces/{ns}/values/state"
    req = urllib.request.Request(url, json.dumps(state).encode(), method="PUT",
                                 headers={"authorization": f"Bearer {token}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            sys.exit(f"KV push failed: {r.status}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", default=str(ROOT / "state" / "findings-page.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "state" / "state.json"))
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()
    state, inactive = build(load_races(), read_findings(pathlib.Path(args.findings)),
                            load(ROOT / "state" / "confirmed.json", {}), load(ROOT / "state" / "pending.json", {}),
                            read_prereg(), dt.datetime.now(ET))
    save_atomic(pathlib.Path(args.out), state)
    save_atomic(ROOT / "state" / "inactive.json", inactive)
    if args.push:
        push_kv(state)
    s = state["summary"]
    print(f"state: {s['races']} races, {s['polls_closed']} closed, {s['called']} called, leader {s['leader']}; "
          f"{len(inactive)} inactive{'; pushed to KV' if args.push else ''}")


if __name__ == "__main__":
    main()
