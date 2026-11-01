"""Sign off races in data/races.json after checking them against the state's certified candidate list.

  python3 -I scripts/review.py todo                       # races still to check (Senate/Governor need 2 people)
  python3 -I scripts/review.py show senate-ga             # candidates, sources and notes for one race
  python3 -I scripts/review.py ok senate-ga --by Hammad   # this person checked it
  python3 -I scripts/review.py fix senate-ga --by Hammad --note "added X (L), on the ballot per SOS list"
        (edit data/races.json by hand first, then record what you changed)

Every sign-off is appended to data/review-log.jsonl so the record is public with the repo.
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RACES = ROOT / "data" / "races.json"
LOG = ROOT / "data" / "review-log.jsonl"
ET = dt.timezone(dt.timedelta(hours=-5))


def need(r):
    return 2 if r["office"] in ("senate", "governor") else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("todo")
    s = sub.add_parser("show"); s.add_argument("race_id")
    for name in ("ok", "fix"):
        s = sub.add_parser(name); s.add_argument("race_id"); s.add_argument("--by", required=True)
        s.add_argument("--note", required=(name == "fix"))
    a = ap.parse_args()
    data = json.loads(RACES.read_text())
    races = {r["id"]: r for r in data["races"]}

    if a.cmd == "todo":
        left = [(r["id"], len(set(r.get("review", {}).get("checked_by", []))), need(r)) for r in data["races"]
                if len(set(r.get("review", {}).get("checked_by", []))) < need(r)]
        for rid, have, n in left:
            print(f"{rid:24} {have}/{n}")
        print(f"{len(left)} races still need checking")
        return
    r = races.get(a.race_id) or sys.exit(f"unknown race {a.race_id}")
    if a.cmd == "show":
        print(json.dumps({k: r[k] for k in ("id", "candidates", "poll_close_et", "query", "sources", "notes", "review") if k in r},
                         indent=1, ensure_ascii=False))
        return
    rev = r.setdefault("review", {"status": "draft", "checked_by": []})
    if a.by in rev["checked_by"]:
        sys.exit(f"{a.by} already signed off {a.race_id}; a different person is needed" if need(r) == 2 else "already signed off")
    rev["checked_by"].append(a.by)
    rev["status"] = "checked" if len(set(rev["checked_by"])) >= need(r) else "partly checked"
    RACES.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    with LOG.open("a") as f:
        f.write(json.dumps({"at": dt.datetime.now(ET).isoformat(timespec="seconds"), "race_id": a.race_id, "by": a.by,
                            "action": a.cmd, "note": a.note}, ensure_ascii=False) + "\n")
    print(f"{a.race_id}: {rev['status']} ({', '.join(rev['checked_by'])})")


if __name__ == "__main__":
    main()
