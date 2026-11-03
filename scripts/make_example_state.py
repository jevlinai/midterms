"""Write state/example-state.json: SYNTHETIC data for building the page and testing the agent.

Race ids, offices and states follow the real list so the map and tabs have something to draw,
but every candidate name is fake, and the file carries "synthetic": true. The page must show a
TEST DATA banner when it sees that flag. Never put real candidates in this file.

Usage: python3 -I scripts/make_example_state.py
"""
import datetime as dt
import json
import pathlib
import random
import statistics

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINES = [
    ("exa", "Exa", 0.007), ("brave", "Brave", 0.005), ("parallel-search", "Parallel", 0.005),
    ("perplexity-search", "Perplexity", 0.005), ("serper", "Serper", 0.0003),
    ("tinyfish", "TinyFish", 0.0),
]
FAKE = ["Alpha", "Bravo", "Cedar", "Delta", "Ember", "Flint", "Grove", "Harbor", "Iris", "Juniper"]
NOW = dt.datetime(2026, 11, 3, 23, 30, tzinfo=dt.timezone(dt.timedelta(hours=-5)))


def iso(t):
    return t.isoformat() if t else None


def mins(a, b):
    return None if a is None or b is None else round((a - b).total_seconds() / 60)


def main():
    rnd = random.Random(20261103)
    races = json.loads((ROOT / "data" / "races.json").read_text())["races"]
    out = []
    for i, r in enumerate(races):
        close = dt.datetime.fromisoformat(r["poll_close_et"])
        cands = [{"name": f"{['Ada','Ben','Cy','Dee'][k]} {rnd.choice(FAKE)}", "party": p, "incumbent": k == 0}
                 for k, p in enumerate(["D", "R"])]
        status = "open" if close > NOW else rnd.choice(["closed", "called", "called", "called"])
        race = {
            "id": r["id"], "office": r["office"], "state": r["state"], "district": r["district"],
            "name": f"Test race {i + 1}", "poll_close_et": r["poll_close_et"], "status": status,
            "pending_reason": None, "candidates": cands, "call": None, "engines": {}, "first": [],
        }
        if status == "closed" and rnd.random() < 0.2:
            race["pending_reason"] = rnd.choice(["runoff", "ranked_choice", "slow_count"])
        if status == "called":
            w = rnd.choice(cands)
            call_t = close + dt.timedelta(minutes=rnd.choice([0, 0, 5, 20, 45, 90]))
            race["call"] = {"winner": w["name"], "party": w["party"], "ap_call_time": iso(call_t),
                            "ap_url": "https://example.com/synthetic-ap-call", "confirmed_by": ["test", "test"]}
            ids = [e[0] for e in ENGINES]
            found = {}
            for e in ids:
                if rnd.random() < 0.1:
                    found[e] = None
                    continue
                t = call_t + dt.timedelta(minutes=5 * rnd.randint(-2, 8))
                found[e] = max(t, close)
            found["jevlin"] = min((t for e, t in found.items() if e != "agent" and t), default=None)
            g = found.get("serper")
            for e, t in found.items():
                race["engines"][e] = {
                    "found_at": iso(t), "minutes_after_close": mins(t, close), "minutes_vs_call": mins(t, call_t),
                    "lead_vs_serper": mins(g, t) if e != "serper" else 0,
                    "wrong_at": iso(close + dt.timedelta(minutes=10)) if rnd.random() < 0.03 else None,
                    "url": "https://example.com/synthetic-result" if t else None, "failed_rounds": rnd.randint(0, 1),
                }
            best = min((t for e, t in found.items() if t and e not in ("jevlin",)), default=None)
            race["first"] = sorted(e for e, t in found.items() if t == best and e != "jevlin")
        out.append(race)

    called = [r for r in out if r["status"] == "called"]
    board = []
    for e, label, cost in ENGINES + [("jevlin", "", 0.02735)]:
        rows = [r["engines"][e] for r in called if e in r["engines"]]
        got = [x["minutes_after_close"] for x in rows if x["found_at"]]
        leads = [x["lead_vs_serper"] for x in rows if x["lead_vs_serper"] is not None]
        board.append({
            "engine": e, "races_scored": len(rows),
            "median_minutes_after_close": statistics.median(got) if got else None,
            "found_within": {k: sum(1 for m in got if m <= int(k)) for k in ("30", "60", "180")},
            "found_first": sum(1 for r in called if e in r["first"]),
            "median_lead_vs_serper": statistics.median(leads) if leads else None,
            "never_found": sum(1 for x in rows if not x["found_at"]),
            "wrong": sum(1 for x in rows if x["wrong_at"]),
            "failed_rounds": sum(x["failed_rounds"] for x in rows), "cost_per_check_usd": cost,
        })
    board.sort(key=lambda b: (b["median_minutes_after_close"] is None, b["median_minutes_after_close"] or 0))
    engines = [{"id": e, "label": l, **({"baseline": True} if e == "serper" else {})} for e, l, _ in ENGINES]
    engines += [{"id": "jevlin", "label": "Jevlin, all engines", "computed": True}]
    state = {
        "version": 1, "synthetic": True, "generated_at": iso(NOW), "round_minutes": 5,
        "prereg": {"sha256": "0" * 64, "committed_at": "2026-11-03T17:00:00-05:00", "url": None},
        "engines": engines,
        "summary": {"races": len(out), "polls_closed": sum(r["status"] != "open" for r in out),
                    "called": len(called), "leader": board[0]["engine"]},
        "races": out, "leaderboard": board,
    }
    p = ROOT / "state" / "example-state.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(state, indent=1) + "\n")
    print(p, len(out), "races,", len(called), "called (synthetic)")


if __name__ == "__main__":
    main()
