"""Export the night's data for the Hugging Face dataset.

  python3 scripts/export_hf.py <snapshot dir> <out dir>

Per search: the query, timings, each engine's status, result count, latency and cost, and every
result page (URL, domain, title, publish date) with the engines that returned it and their ranks.
Page text (snippets, excerpts) is left out: it belongs to the publishers. Routing internals too.
Plus the race list, AP calls (no names of who checked them), per-engine findings and the leaderboard
from the snapshot's state.json.
"""
import glob, gzip, json, os, sys
from collections import defaultdict

snap, out = sys.argv[1], sys.argv[2]
os.makedirs(f"{out}/data", exist_ok=True)

def search_row(x):
    r = x.get("response") or {}
    att = (x.get("attempts") or [{}])[-1]
    engines, results = [], {}
    for c in r.get("candidates") or []:
        e = c["provider"]
        engines.append({"engine": e, "status": c.get("status"), "results": len(c.get("citations") or []),
                        "latency_ms": (c.get("latency") or {}).get("ms") if isinstance(c.get("latency"), dict) else c.get("latency"),
                        "cost_usd": round((c.get("cost_micros") or 0) / 1e6, 6)})
        for z in c.get("citations") or []:
            u = z.get("url")
            if not u:
                continue
            row = results.setdefault(u, {"url": u, "domain": z.get("domain"), "title": z.get("title"),
                                         "published": z.get("published"), "found_by": []})
            row["found_by"].append({"engine": e, "rank": z.get("rank")})
    by = {b["provider"]: b for b in (r.get("indexes") or {}).get("by_provider") or []}
    for en in engines:  # the router's own per-engine status and latency, where it reports them
        b = by.get(en["engine"])
        if b:
            en["status"], en["latency_ms"] = b.get("status", en["status"]), b.get("latency_ms", en["latency_ms"])
    return {"round": x["round"], "race_id": x["race_id"], "query": (x.get("request") or {}).get("query"),
            "sent_at": att.get("sent_at"), "received_at": att.get("received_at"), "http_status": x.get("http_status"),
            "engines": engines, "results": sorted(results.values(), key=lambda z: min(f["rank"] or 99 for f in z["found_by"]))}

n = 0
for day in sorted(glob.glob("raw/*")):
    d = os.path.basename(day)
    with gzip.open(f"{out}/data/searches-{d}.jsonl.gz", "wt") as fo:
        for f in sorted(glob.glob(f"{day}/round-*.jsonl.gz")):
            for line in gzip.open(f, "rt"):
                x = json.loads(line)
                if not (x.get("response") or {}).get("candidates"):
                    continue
                fo.write(json.dumps(search_row(x), ensure_ascii=False) + "\n"); n += 1

s = json.load(open(f"{snap}/state.json"))
with open(f"{out}/races.jsonl", "w") as f:
    for r in s["races"]:
        f.write(json.dumps({k: r.get(k) for k in ("id", "office", "state", "district", "name", "poll_close_et", "candidates")}, ensure_ascii=False) + "\n")
with open(f"{out}/ap_calls.jsonl", "w") as f:
    for r in s["races"]:
        c = r.get("call")
        if c:
            f.write(json.dumps({"race_id": r["id"], "winner": c.get("winner"), "party": c.get("party"),
                                "ap_call_time": c.get("ap_call_time"), "ap_url": c.get("ap_url")}, ensure_ascii=False) + "\n")
with open(f"{out}/findings.jsonl", "w") as f:
    for r in s["races"]:
        for e, v in (r.get("engines") or {}).items():
            f.write(json.dumps({"race_id": r["id"], "engine": e, **{k: v.get(k) for k in ("found_at", "minutes_after_close", "minutes_vs_call", "url", "wrong_at", "failed_rounds")}}, ensure_ascii=False) + "\n")
json.dump({k: s[k] for k in ("generated_at", "prereg", "engines", "summary", "leaderboard")}, open(f"{out}/leaderboard.json", "w"), indent=1)
print("searches", n)
