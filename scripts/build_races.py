"""Merge the House drafts into data/races.json, adding poll close times and queries.

Poll close rule (AP practice): the LATEST scheduled close in the race's state, or in the
district for House races. AP "will not declare winners in statewide races before the last
polls have closed" (AP Decision Notes, 2026). District times: 270toWin's per-district table.

Sources:
  https://270towin.com/poll-closing-times
  https://thehill.com/homenews/campaign/6175621-midterm-elections-polls-close-results
  https://wtop.com/us-politics/2026/10/ap-decision-notes-what-to-expect-in-alaska-on-election-day

Usage: python3 -I scripts/build_races.py
"""
import json
import pathlib

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

# Latest close per state, ET, 3 Nov 2026 ("+1" = 4 Nov). Statewide races.
STATE_CLOSE = {
    "AK": "01:00+1", "AL": "20:00", "AR": "20:30", "AZ": "21:00", "CA": "23:00", "CO": "21:00",
    "CT": "20:00", "DE": "20:00", "FL": "20:00", "GA": "19:00", "HI": "00:00+1", "IA": "21:00",
    "ID": "23:00", "IL": "20:00", "IN": "19:00", "KS": "21:00", "KY": "19:00", "LA": "21:00",
    "MA": "20:00", "MD": "20:00", "ME": "20:00", "MI": "21:00", "MN": "21:00", "MO": "20:00",
    "MS": "20:00", "MT": "22:00", "NC": "19:30", "NE": "21:00", "NH": "20:00", "NJ": "20:00",
    "NM": "21:00", "NV": "22:00", "NY": "21:00", "OH": "19:30", "OK": "20:00", "OR": "23:00",
    "PA": "20:00", "RI": "20:00", "SC": "19:00", "SD": "21:00", "TN": "20:00", "TX": "21:00",
    "VA": "19:00", "VT": "19:00", "WA": "23:00", "WI": "21:00", "WV": "19:30", "WY": "21:00",
}

# Split-time-zone states where House districts close earlier than the state's latest close
# (270toWin). Districts not listed use STATE_CLOSE.
DISTRICT_CLOSE = {
    "IN": ({"01": "19:00", "08": "19:00"}, "18:00"),
    "KY": ({"01": "19:00", "02": "19:00"}, "18:00"),
    "FL": ({"01": "20:00", "02": "20:00"}, "19:00"),
    "KS": ({"01": "21:00"}, "20:00"),
    "MI": ({"01": "21:00"}, "20:00"),
    "TX": ({"16": "21:00", "23": "21:00"}, "20:00"),
    "ID": ({"01": "23:00", "02": "22:00"}, "23:00"),
}

STATE_NAME = {
    "AK": "Alaska", "AL": "Alabama", "AR": "Arkansas", "AZ": "Arizona", "CA": "California",
    "CO": "Colorado", "FL": "Florida", "IA": "Iowa", "IN": "Indiana", "KS": "Kansas",
    "KY": "Kentucky", "ME": "Maine", "MI": "Michigan", "MN": "Minnesota", "MO": "Missouri",
    "MT": "Montana", "NC": "North Carolina", "NE": "Nebraska", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NV": "Nevada", "NY": "New York", "OH": "Ohio",
    "PA": "Pennsylvania", "SC": "South Carolina", "TX": "Texas", "VA": "Virginia",
    "WA": "Washington", "WI": "Wisconsin",
}


def iso(t):
    day = "04" if t.endswith("+1") else "03"
    return f"2026-11-{day}T{t[:5]}:00-05:00"


def ordinal(n):
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def house_close(state, district):
    if state in DISTRICT_CLOSE:
        special, default = DISTRICT_CLOSE[state]
        return special.get(district, default)
    return STATE_CLOSE[state]


def house_query(state, district):
    if district == "AL":
        return f"{STATE_NAME[state]} at-large congressional district election results 2026"
    return f"{STATE_NAME[state]} {ordinal(district)} congressional district election results 2026"


def main():
    races = json.loads((DATA / "races.json").read_text())
    keep = [r for r in races["races"] if r["office"] != "house"]
    cook = {r["id"]: r for r in json.loads((DATA / "house-competitive.json").read_text())["races"]}

    house = []
    for path in sorted(DATA.glob("draft-house-*.json")):
        house += json.loads(path.read_text())["races"]
    missing = set(cook) - {r["id"] for r in house}
    extra = {r["id"] for r in house} - set(cook)
    if missing or extra:
        raise SystemExit(f"House drafts don't match the Cook list. missing={sorted(missing)} extra={sorted(extra)}")

    for r in house:
        r["poll_close_et"] = iso(house_close(r["state"], r["district"]))
        r["query"] = house_query(r["state"], r["district"])
        r["cook_rating"] = cook[r["id"]]["cook_rating"]
        r.setdefault("notes", "")
        r.setdefault("review", {"status": "draft", "checked_by": []})

    order = {"senate": 0, "governor": 1, "house": 2}
    races["races"] = sorted(keep + house, key=lambda r: (order[r["office"]], r["state"], r["id"]))
    races["status"] = "draft: every Senate and Governor entry and every competitive House entry needs a person to check it"
    (DATA / "races.json").write_text(json.dumps(races, indent=1, ensure_ascii=False) + "\n")
    print(len(races["races"]), "races")


if __name__ == "__main__":
    main()
