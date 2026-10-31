"""Race list helpers shared by the detector, confirm tool and state builder."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

STATE_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}


def ordinal(n):
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def display_name(r):
    st = STATE_NAME[r["state"]]
    if r["office"] == "senate":
        return f"{st} U.S. Senate{' special election' if r.get('special') else ''}"
    if r["office"] == "governor":
        return f"{st} governor"
    if r["district"] == "AL":
        return f"{st} at-large congressional district (U.S. House)"
    return f"{st} {ordinal(r['district'])} congressional district (U.S. House)"


def load_races(path=None):
    path = path or ROOT / "data" / "races.json"
    races = json.loads(pathlib.Path(path).read_text())["races"]
    for r in races:
        r.setdefault("name", display_name(r))
    return {r["id"]: r for r in races}
