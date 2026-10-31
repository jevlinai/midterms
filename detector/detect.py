"""Detector: for every stored search response, which candidate (if any) does each engine's results
name as the winner? It never sees AP's call; build_state compares its output with the confirmed
winner afterwards.

Reads raw/**/round-*.jsonl.gz (poller and agent track), writes one line per (round, race) to
state/findings.jsonl. Incremental: remembers how many lines of each raw file it has processed.

Scoring mode (pre-registered, docs/SCORING.md):
  page   (primary) a page is judged on all the text any engine returned for that URL in that round,
         and every engine that returned the URL gets the credit. Engines return snippets of very
         different lengths (Google via Serper ~120 chars, Exa ~1,000), so judging each engine on its
         own snippet would reward snippet length, not finding the page.
  engine (secondary) each engine judged on its own title + snippet only.

  python3 -I detector/detect.py [--mode page|engine] [--once]
"""
import argparse
import concurrent.futures as cf
import glob
import gzip
import json
import os
import pathlib
import sys
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from detector import textmatch  # noqa: E402
from detector.jev import Jev  # noqa: E402
from detector.races import ROOT, load_races  # noqa: E402

PAGE_TEXT_CAP = 4000


def canon(url):
    """Canonical page key: scheme-less, lowercased host without www, path without trailing slash, no query/fragment."""
    p = urllib.parse.urlsplit(url or "")
    host = p.netloc.lower().removeprefix("www.")
    return host + p.path.rstrip("/")


def item_text(c):
    parts = [c.get("title") or "", c.get("snippet") or ""] + list(c.get("excerpts") or [])
    return "\n".join(x.strip() for x in parts if x and x.strip())


def engine_items(rec):
    """{engine: {"status": ..., "items": [citation dicts]}} from one stored record (poller or agent)."""
    if rec.get("source") == "agent":
        return {"agent": {"status": rec.get("status", "ok"), "items": rec.get("items") or []}}
    resp = rec.get("response") or {}
    out = {}
    for c in resp.get("candidates") or []:
        out[c.get("provider")] = {"status": c.get("status"), "items": c.get("citations") or []}
    return out


def judge_record(rec, race, jev, mode, pool):
    """One stored response → finding: per engine, {candidate: [urls]} named as winner."""
    patterns = textmatch.name_patterns(race["candidates"])
    engines = engine_items(rec)
    finding = {"round": rec["round"], "race_id": rec["race_id"], "source": rec.get("source", "poller"),
               "mode": mode, "cache_hit": bool((rec.get("summary") or {}).get("cache_hit")), "engines": {}}
    for e, v in engines.items():
        finding["engines"][e] = {"status": v["status"], "results": len(v["items"]), "named": {}, "filter_hits": 0}

    # Units to judge: (text, [(engine, url)]).
    units = {}
    if mode == "page":
        pages = {}
        for e, v in engines.items():
            if v["status"] != "ok":
                continue
            for c in v["items"]:
                key = canon(c.get("url"))
                pg = pages.setdefault(key, {"url": c.get("url"), "texts": [], "engines": set()})
                t = item_text(c)
                if t and t not in pg["texts"]:
                    pg["texts"].append(t)
                pg["engines"].add(e)
        for pg in pages.values():
            text = "\n---\n".join(pg["texts"])[:PAGE_TEXT_CAP]
            units.setdefault(text, []).extend((e, pg["url"]) for e in pg["engines"])
    else:
        for e, v in engines.items():
            if v["status"] != "ok":
                continue
            for c in v["items"]:
                units.setdefault(item_text(c)[:PAGE_TEXT_CAP], []).append((e, c.get("url")))

    todo = [(text, who) for text, who in units.items() if text and textmatch.filter_hit(text, patterns)]
    for _, who in todo:
        for e, _ in who:
            finding["engines"][e]["filter_hits"] += 1
    futs = {pool.submit(jev.check, race, race["candidates"], text): who for text, who in todo}
    for fut in cf.as_completed(futs):
        try:
            named, _, _ = fut.result()
        except Exception as ex:  # a model failure is recorded, never guessed
            for e, _ in futs[fut]:
                finding["engines"][e].setdefault("errors", 0)
                finding["engines"][e]["errors"] += 1
            print("detector: model check failed:", ex, file=sys.stderr)
            continue
        if named:
            for e, url in futs[fut]:
                finding["engines"][e]["named"].setdefault(named, []).append(url)
    return finding


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["page", "engine"], default="page")
    ap.add_argument("--raw-dir", default=str(ROOT / "raw"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--once", action="store_true", help="process what's there and exit")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()

    races = load_races()
    out = pathlib.Path(args.out or ROOT / "state" / f"findings-{args.mode}.jsonl")
    progress_path = out.with_suffix(".progress.json")
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    jev = Jev()

    with cf.ThreadPoolExecutor(int(os.environ.get("DETECTOR_WORKERS", "16"))) as pool:
        while True:
            files = sorted(glob.glob(os.path.join(args.raw_dir, "**", "round-*.jsonl.gz"), recursive=True))
            new = 0
            for f in files:
                done = progress.get(f, 0)
                try:
                    with gzip.open(f, "rt") as fh:
                        lines = fh.readlines()
                except (EOFError, OSError):
                    continue  # still being written; next pass
                # The last line may be partial while the poller is writing.
                complete = [l for l in lines if l.endswith("\n")]
                with out.open("a") as fo:
                    for line in complete[done:]:
                        rec = json.loads(line)
                        race = races.get(rec["race_id"])
                        if race is None or (rec.get("summary") is None and rec.get("source") != "agent"):
                            continue
                        fo.write(json.dumps(judge_record(rec, race, jev, args.mode, pool)) + "\n")
                        new += 1
                progress[f] = len(complete)
                progress_path.write_text(json.dumps(progress))
            if new:
                print(f"detector[{args.mode}]: {new} new records, model spend ${jev.spent_usd:.4f}", file=sys.stderr)
            if args.once:
                return
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
