"""TypeSafe Jev client for the detector's model check (POST https://api.typesafe.ai/v1/systemone).

Stdlib only, like the poller. One request per (race, page text): three questions (winner choice,
this_election noul, is_call noul) from detector/questions.json. Verdicts are cached in SQLite by a
hash of (model, questions, race, text), so a page seen again in a later round costs nothing and gets
the same verdict.

Key: env TYPESAFE_API_KEY (never logged).
"""
import hashlib
import json
import os
import pathlib
import sqlite3
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUESTIONS = json.loads((ROOT / "detector" / "questions.json").read_text())
ENDPOINT = os.environ.get("TYPESAFE_ENDPOINT", "https://api.typesafe.ai") + "/v1/systemone"
PRICE_PER_MTOK_IN = 0.042  # Jev rate card: $42 per billion input tokens; output free


def build_request(race, candidates, page_text, election=QUESTIONS["election"]):
    qs = QUESTIONS["questions"]
    tmpl = qs["winner"]["criteria_template"]
    criteria = {c["name"]: tmpl["candidate"].format(name=c["name"], party=c.get("party") or "no party") for c in candidates}
    criteria["none"] = tmpl["none"]
    return {
        "model": QUESTIONS["model"],
        "state": {
            "race": {"name": race["name"], "office": race["office"], "state": race["state"],
                     "district": race.get("district"), "election": election},
            "page": page_text,
        },
        "questions": {
            "winner": {"type": "choice", "instructions": qs["winner"]["instructions"], "criteria": criteria},
            "this_election": {"type": "noul", "instructions": qs["this_election"]["instructions"]},
            "is_call": {"type": "noul", "instructions": qs["is_call"]["instructions"]},
        },
    }


def decide(resp, rule=QUESTIONS["rule"]):
    """Apply the pre-registered rule. Returns the candidate name the page names as winner, or None."""
    a = resp["answers"]
    w = a["winner"]
    choice = w.get("choice")
    if not choice or choice == "none":
        return None
    if (w.get("probabilities") or {}).get(choice, 0) < rule["winner_probability_min"]:
        return None
    if a["this_election"].get("noul", 0) < rule["this_election_min"]:
        return None
    if a["is_call"].get("noul", 0) < rule["is_call_min"]:
        return None
    return choice


class Jev:
    def __init__(self, cache_path=ROOT / "state" / "detector.sqlite", key=None, transport=None):
        self.key = key or os.environ.get("TYPESAFE_API_KEY")
        self.transport = transport  # tests inject a function(request_dict) -> response_dict
        cache_path = pathlib.Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache_path, check_same_thread=False)
        self.db.execute("create table if not exists verdicts (k text primary key, race_id text, request text, response text, model text, input_tokens int, at real)")
        self.lock = threading.Lock()
        self.spent_usd = 0.0

    @staticmethod
    def cache_key(req):
        return hashlib.sha256(json.dumps(req, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def check(self, race, candidates, page_text):
        """Returns (named_winner_or_None, response, cached)."""
        req = build_request(race, candidates, page_text)
        k = self.cache_key(req)
        with self.lock:
            row = self.db.execute("select response from verdicts where k=?", (k,)).fetchone()
        if row:
            resp = json.loads(row[0])
            return decide(resp), resp, True
        resp = self._call(req)
        tokens = (resp.get("usage") or {}).get("input_tokens") or 0
        with self.lock:
            self.spent_usd += tokens * PRICE_PER_MTOK_IN / 1e6
            self.db.execute("insert or replace into verdicts values (?,?,?,?,?,?,?)",
                            (k, race["id"], json.dumps(req), json.dumps(resp), resp.get("model"), tokens, time.time()))
            self.db.commit()
        return decide(resp), resp, False

    def _call(self, req):
        if self.transport:
            return self.transport(req)
        if not self.key:
            raise RuntimeError("TYPESAFE_API_KEY is not set")
        data = json.dumps(req).encode()
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.key}"}
        last = None
        for attempt in (1, 2, 3):
            try:
                r = urllib.request.urlopen(urllib.request.Request(ENDPOINT, data, headers, method="POST"), timeout=30)
                resp = json.loads(r.read())
                if not resp.get("answers"):
                    raise RuntimeError("jev returned no answers")
                return resp
            except urllib.error.HTTPError as e:
                last = f"http {e.code}"
                if e.code < 500 and e.code != 429:
                    break
            except Exception as e:
                last = f"{type(e).__name__}"
            time.sleep(attempt)
        raise RuntimeError(f"jev call failed: {last}")
