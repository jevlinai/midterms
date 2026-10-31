"""Run the pre-registered Jev questions on every collected test page, with the case's election
string (the proxy cases ask about November 2025). Writes verdicts.jsonl: one line per page with the
raw answers, so thresholds can be studied without new calls.

  uv run python detector/eval/run_jev.py
"""
import concurrent.futures as cf
import os
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from detector.jev import QUESTIONS, Jev, build_request, dated_before  # noqa: E402


def main():
    cases = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())["cases"]}
    pages = [json.loads(l) for l in (HERE / "candidates.jsonl").open()]
    jev = Jev(cache_path=HERE / "jev-cache.sqlite")

    def one(i, p):
        case = cases[p["case"]]
        if os.environ.get("DATE_RULE", "1") == "1" and dated_before([p.get("published")], case["election_date"]):
            return i, {"i": i, "case": p["case"], "url": p["url"], "engine": p["engine"], "model": None, "winner": "none",
                       "p_winner": 0, "this_election": 0, "is_call": 0, "other_opponent": 0, "tokens": 0, "dated_before": True}
        req = build_request(case["race"], case["candidates"], p["text"], election=case["election"] or QUESTIONS["election"],
                            published=p.get("published"))
        k = jev.cache_key(req)
        with jev.lock:
            row = jev.db.execute("select response from verdicts where k=?", (k,)).fetchone()
        resp = json.loads(row[0]) if row else jev._call(req)
        if not row:
            with jev.lock:
                jev.db.execute("insert or replace into verdicts values (?,?,?,?,?,?,?)",
                               (k, case["race"]["id"], json.dumps(req), json.dumps(resp), resp.get("model"),
                                (resp.get("usage") or {}).get("input_tokens", 0), 0))
                jev.db.commit()
        a = resp["answers"]
        return i, {"i": i, "case": p["case"], "url": p["url"], "engine": p["engine"], "model": resp.get("model"),
                   "winner": a["winner"].get("choice"), "p_winner": (a["winner"].get("probabilities") or {}).get(a["winner"].get("choice"), 0),
                   "this_election": a["this_election"].get("noul"), "is_call": a["is_call"].get("noul"), "other_opponent": a["other_opponent"].get("noul"),
                   "tokens": (resp.get("usage") or {}).get("input_tokens", 0)}

    out = [None] * len(pages)
    with cf.ThreadPoolExecutor(12) as pool:
        for fut in cf.as_completed([pool.submit(one, i, p) for i, p in enumerate(pages)]):
            i, v = fut.result()
            out[i] = v
    with (HERE / os.environ.get("VERDICTS_OUT", "verdicts.jsonl")).open("w") as f:
        for v in out:
            f.write(json.dumps(v) + "\n")
    tok = sum(v["tokens"] for v in out)
    print(f"{len(out)} verdicts, {tok} input tokens, ${tok * 0.042 / 1e6:.4f}")


if __name__ == "__main__":
    main()
