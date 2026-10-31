"""Collect detector test pages: run each case's queries through Jevlin (same engines and tier as the
poller, no recency filter since most cases are past elections), keep results that pass the cheap
filter for that case's candidates, and write them for labelling.

  python3 -I detector/eval/collect.py --budget-usd 0.60 > detector/eval/candidates.jsonl
"""
import argparse
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "poller"))
import poller  # noqa: E402
from detector import textmatch  # noqa: E402
from detector.detect import canon, item_text  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-usd", type=float, required=True)
    args = ap.parse_args()
    cfg = json.loads((ROOT / "poller" / "config.json").read_text())
    cases = json.loads((pathlib.Path(__file__).parent / "cases.json").read_text())["cases"]
    key = poller.load_key()
    spent, seen = 0.0, set()
    for case in cases:
        pats = textmatch.name_patterns(case["candidates"])
        for q in case["queries"]:
            if spent >= args.budget_usd:
                print(f"budget reached at ${spent:.3f}", file=sys.stderr)
                return
            body = {"query": q, "providers": cfg["providers"], "tier": cfg["tier"], "view": "merged", "merged_limit": 50}
            req = urllib.request.Request(cfg["endpoint"], json.dumps(body).encode(), method="POST",
                                         headers={"authorization": f"Bearer {key}", "content-type": "application/json", "user-agent": poller.USER_AGENT})
            resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
            spent += (resp.get("usage") or {}).get("cost_micros", 0) / 1e6
            n = 0
            for c in resp.get("candidates") or []:
                for it in c.get("citations") or []:
                    text = item_text(it)
                    k = (case["id"], canon(it.get("url")), text)
                    if k in seen or not textmatch.filter_hit(text, pats):
                        continue
                    seen.add(k)
                    n += 1
                    print(json.dumps({"case": case["id"], "engine": c.get("provider"), "query": q, "url": it.get("url"),
                                      "published": it.get("published"), "text": text}, ensure_ascii=False))
            print(f"{case['id']} | {q!r}: {n} filter hits, spent ${spent:.3f}", file=sys.stderr)


if __name__ == "__main__":
    main()
