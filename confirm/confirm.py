"""Confirm tool: a person marks a race as called by AP. Nothing else can.

The agent's call finder may leave a proposal in state/proposals.json (winner, AP call time, AP URL,
the sentence it quoted). This tool shows it; the person checks AP's page themselves and types their
name. One person's confirmation counts (decided 3 Nov: election night is staffed by one person).
CONFIRM_TWO_PEOPLE=senate,governor brings back the two-person rule for those offices.

  python3 -I confirm/confirm.py list                      # races with polls closed and no confirmed call
  python3 -I confirm/confirm.py show senate-ga            # candidates, proposal, current confirmations
  python3 -I confirm/confirm.py confirm senate-ga --winner "Full Name" --time 2026-11-03T21:42 \
        --url https://apnews.com/... --by Hammad
  python3 -I confirm/confirm.py retract senate-ga --by Hammad --reason "AP withdrew the call"

Writes state/confirmed.json (atomically) and appends every action to state/confirm-log.jsonl.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from detector.races import ROOT, load_races  # noqa: E402

ET = dt.timezone(dt.timedelta(hours=-5))
STATE = ROOT / "state"
CONFIRMED = STATE / "confirmed.json"
LOG = STATE / "confirm-log.jsonl"
PROPOSALS = STATE / "proposals.json"
TWO_PEOPLE = {o.strip() for o in os.environ.get("CONFIRM_TWO_PEOPLE", "").split(",") if o.strip()}
# Election night: AP only. CONFIRM_HOSTS exists for dry runs on other events (e.g. "espn.com,nfl.com").
AP_HOSTS = tuple(h.strip() for h in os.environ.get("CONFIRM_HOSTS", "apnews.com").split(",") if h.strip())


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def log(action, **kw):
    STATE.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"at": dt.datetime.now(ET).isoformat(), "action": action, **kw}, ensure_ascii=False) + "\n")


def is_called(entry, race):
    """A call counts once enough distinct people agree on the same winner, time and link."""
    need = 2 if race["office"] in TWO_PEOPLE else 1
    return entry is not None and not entry.get("retracted") and len(set(entry["confirmed_by"])) >= need


def now_et():
    return dt.datetime.now(ET)


def parse_time(s):
    """A naive time is ET; a time with an offset (the confirm page sends the viewer's local time
    converted to UTC) is converted to ET."""
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t.astimezone(ET) if t.tzinfo else t.replace(tzinfo=ET)


def cmd_list(races, confirmed, proposals):
    now = dt.datetime.now(ET)
    rows = []
    for r in races.values():
        if dt.datetime.fromisoformat(r["poll_close_et"]) > now:
            continue
        c = confirmed.get(r["id"])
        if is_called(c, r):
            continue
        state = "needs 2nd person" if c and not c.get("retracted") else ("proposal waiting" if r["id"] in proposals else "")
        rows.append((r["poll_close_et"][11:16], r["id"], r["name"], state))
    for row in sorted(rows):
        print("  ".join(row))
    print(f"{len(rows)} closed races without a confirmed call")


def cmd_show(race, confirmed, proposals):
    print(race["name"], f"({race['id']}), polls close {race['poll_close_et']}")
    for c in race["candidates"]:
        print(f"  - {c['name']} ({c['party']}){' incumbent' if c['incumbent'] else ''}")
    p = proposals.get(race["id"])
    if p:
        print("\nAgent proposal (NOT a confirmation; open the AP link and check it yourself):")
        for k in ("winner", "ap_call_time", "ap_url", "quote"):
            print(f"  {k}: {p.get(k)}")
    c = confirmed.get(race["id"])
    if c:
        print("\nConfirmations:", json.dumps(c, indent=1, ensure_ascii=False))


def cmd_confirm(race, confirmed, args):
    names = {c["name"] for c in race["candidates"]}
    if args.winner not in names:
        sys.exit(f"winner must be exactly one of: {sorted(names)}")
    host = urllib.parse.urlsplit(args.url).netloc.lower().removeprefix("www.")
    if not any(host == h or host.endswith("." + h) for h in AP_HOSTS):
        sys.exit(f"the link must be AP's own call ({', '.join(AP_HOSTS)})")
    t = parse_time(args.time)
    if t < dt.datetime.fromisoformat(race["poll_close_et"]):
        sys.exit("AP does not call a race before its polls close; check the time")
    if t > now_et() + dt.timedelta(minutes=5):
        sys.exit("that call time is in the future: check the date and time against AP's page (reload the confirm page if it doesn't show the ET equivalent under the time box)")
    by = args.by.strip()
    if not by:
        sys.exit("--by is required")

    entry = confirmed.get(race["id"])
    if entry and not entry.get("retracted"):
        same = (entry["winner"] == args.winner and entry["ap_url"] == args.url)
        if not same:
            sys.exit(f"an earlier confirmation names {entry['winner']} / {entry['ap_url']}. "
                     "If that was wrong, retract it first; two people must agree on the same call.")
        if by in entry["confirmed_by"]:
            sys.exit(f"{by} already confirmed this race; a second, different person must confirm")
        entry["confirmed_by"].append(by)
        entry["confirmed_at"].append(dt.datetime.now(ET).isoformat())
        # Keep the earliest AP time anyone entered; exact times are backfilled from AP after the night.
        entry["ap_call_time"] = min(entry["ap_call_time"], t.isoformat())
    else:
        entry = {"race_id": race["id"], "winner": args.winner,
                 "party": next(c["party"] for c in race["candidates"] if c["name"] == args.winner),
                 "ap_call_time": t.isoformat(), "ap_url": args.url,
                 "confirmed_by": [by], "confirmed_at": [dt.datetime.now(ET).isoformat()]}
    confirmed[race["id"]] = entry
    save_atomic(CONFIRMED, confirmed)
    log("confirm", race_id=race["id"], by=by, winner=args.winner, ap_url=args.url, ap_call_time=t.isoformat())
    if is_called(entry, race):
        print(f"CALLED: {race['name']}: {args.winner}. It will show on the page after the next state build.")
    else:
        print(f"Recorded. {race['name']} needs a second, different person to confirm before it shows.")


def cmd_accept(race, confirmed, proposals, args):
    """Confirm the agent's proposal as it stands, after opening its link and checking it.
    --time is needed when the proposal has no call time (or to correct it)."""
    p = proposals.get(race["id"])
    if not p:
        sys.exit(f"no proposal for {race['id']}; use `confirm` with --winner/--time/--url")
    t = args.time or p.get("ap_call_time")
    if not t:
        sys.exit("the proposal has no call time: add --time (ET, e.g. 2026-11-03T21:42) from the page")
    print(f"Accepting the agent's proposal for {race['name']}: {p['winner']}\n  link:  {p['ap_url']}\n  quote: {p.get('quote')}")
    # The whole string: parse_time keeps an offset (the page sends UTC) and reads a naive time as ET.
    cmd_confirm(race, confirmed, argparse.Namespace(winner=p["winner"], time=str(t), url=p["ap_url"], by=args.by))


def cmd_retract(race, confirmed, args):
    entry = confirmed.get(race["id"])
    if not entry:
        sys.exit("nothing to retract")
    entry["retracted"] = {"by": args.by, "reason": args.reason, "at": dt.datetime.now(ET).isoformat()}
    save_atomic(CONFIRMED, confirmed)
    log("retract", race_id=race["id"], by=args.by, reason=args.reason)
    print(f"Retracted {race['id']}. Re-confirm with the correct call if there is one.")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("race_id")
    c = sub.add_parser("confirm"); c.add_argument("race_id")
    c.add_argument("--winner", required=True); c.add_argument("--time", required=True, help="AP call time, ET, e.g. 2026-11-03T21:42")
    c.add_argument("--url", required=True); c.add_argument("--by", required=True)
    a = sub.add_parser("accept", help="confirm the agent's proposal after checking its link")
    a.add_argument("race_id"); a.add_argument("--by", required=True); a.add_argument("--time", help="call time, ET, if the proposal has none")
    r = sub.add_parser("retract"); r.add_argument("race_id"); r.add_argument("--by", required=True); r.add_argument("--reason", required=True)
    args = ap.parse_args(argv)

    races = load_races()
    confirmed = load(CONFIRMED, {})
    proposals = load(PROPOSALS, {})
    if args.cmd == "list":
        return cmd_list(races, confirmed, proposals)
    race = races.get(args.race_id) or sys.exit(f"unknown race {args.race_id}")
    if args.cmd == "show":
        cmd_show(race, confirmed, proposals)
    elif args.cmd == "confirm":
        cmd_confirm(race, confirmed, args)
    elif args.cmd == "accept":
        cmd_accept(race, confirmed, proposals, args)
    else:
        cmd_retract(race, confirmed, args)


if __name__ == "__main__":
    main()
