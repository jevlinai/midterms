"""Turn a SEPARATE checkout of this repo into an NFL dry run (Sun 1 Nov 2026).

Never run this in the real checkout: it overwrites data/races.json and the configs. Usage:

  git clone <repo> ~/midterms-dryrun && cd ~/midterms-dryrun
  python3 scripts/make_dryrun.py --i-am-in-a-scratch-clone
  scripts/dryrun.sh start            # see that script

Each game is a "race" and its two teams are the "candidates". poll_close_et is set to kickoff + 2h40,
about half an hour before a typical game ends, so polling starts before the result exists and the
pre-close sweeps (-60 and -15 min) run while the game is still being played.
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ET = dt.timezone(dt.timedelta(hours=-5))

# (away, home, kickoff ET, tracked by the agent?). Kickoffs are the standard slots; check them on the day.
GAMES = [
    ("Baltimore Ravens", "Buffalo Bills", "13:00", True),
    ("Minnesota Vikings", "Detroit Lions", "13:00", True),
    ("Cleveland Browns", "Pittsburgh Steelers", "13:00", False),
    ("Atlanta Falcons", "Tampa Bay Buccaneers", "13:00", False),
    ("Arizona Cardinals", "Dallas Cowboys", "13:00", False),
    ("Las Vegas Raiders", "New York Jets", "13:00", False),
    ("Philadelphia Eagles", "Washington Commanders", "20:20", True),  # Sunday Night Football, NBC
]
STATE_OF = {"Buffalo Bills": "NY", "Detroit Lions": "MI", "Pittsburgh Steelers": "PA", "Tampa Bay Buccaneers": "FL",
            "Dallas Cowboys": "TX", "New York Jets": "NJ", "Washington Commanders": "MD"}


def slug(team):
    return team.split()[-1].lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--i-am-in-a-scratch-clone", action="store_true", required=True)
    ap.parse_args()
    if (ROOT / "raw").exists() and any((ROOT / "raw").iterdir()):
        sys.exit("raw/ already has data here; this must be a fresh scratch clone")

    races = []
    for away, home, ko, tracked in GAMES:
        h, m = map(int, ko.split(":"))
        kickoff = dt.datetime(2026, 11, 1, h, m, tzinfo=ET)
        races.append({
            "id": f"nfl-{slug(away)}-{slug(home)}", "office": "nfl-track" if tracked else "nfl",
            "state": STATE_OF[home], "district": None, "special": False, "uncontested": False,
            "name": f"{away} at {home} (NFL)",
            "poll_close_et": (kickoff + dt.timedelta(hours=2, minutes=40)).isoformat(),
            "candidates": [{"name": away, "surname": away.split()[-1], "party": None, "incumbent": False},
                           {"name": home, "surname": home.split()[-1], "party": None, "incumbent": False}],
            "query": f"{away} vs {home} final score",
            "sources": ["https://nflschedules.com/weeks/2026/week-8"], "notes": f"kickoff {ko} ET (standard slot; verify)",
            "review": {"status": "dryrun", "checked_by": []},
        })
    (ROOT / "data" / "races.json").write_text(json.dumps({"status": "NFL DRY RUN, not election data", "races": races}, indent=1) + "\n")

    cfg = json.loads((ROOT / "poller" / "config.json").read_text())
    cfg["night"]["active_hours"] = 3
    cfg["followup"]["ends_at"] = "2026-11-02T03:00:00-05:00"   # the poller exits after this
    (ROOT / "poller" / "config.json").write_text(json.dumps(cfg, indent=1) + "\n")

    q = json.loads((ROOT / "detector" / "questions.json").read_text())
    q["election"] = "the NFL regular-season games played on Sunday 1 November 2026"
    q["election_date"] = "2026-11-01"
    (ROOT / "detector" / "questions.json").write_text(json.dumps(q, indent=1, ensure_ascii=False) + "\n")

    t = json.loads((ROOT / "agent" / "track.json").read_text())
    t["offices"] = ["nfl-track"]
    (ROOT / "agent" / "track.json").write_text(json.dumps(t, indent=1) + "\n")

    marker = ROOT / "DRYRUN"
    marker.write_text("This checkout is the NFL dry run. Nothing here is election data. Do not push.\n")
    print(f"dry run ready: {len(races)} games; polling windows (ET):")
    for r in races:
        c = dt.datetime.fromisoformat(r["poll_close_et"])
        print(f"  {r['id']:28} {c - dt.timedelta(minutes=60):%H:%M} sweep, {c:%H:%M}-{c + dt.timedelta(hours=3):%H:%M} rounds"
              f"{'  + agent track' if r['office'] == 'nfl-track' else ''}")


if __name__ == "__main__":
    main()
