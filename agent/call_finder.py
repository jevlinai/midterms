"""Call finder: for races whose polls have closed and that aren't called, find AP's call and write a
PROPOSAL for the person confirming calls. It never writes state/confirmed.json and never decides a
result: a proposal only says "this AP page appears to call it for X; please check".

Per race, per round:
  1. Jevlin /v1/search, domain_filter apnews.com, recency day, tier balanced, merged view.
  2. A cheap text filter: no candidate surname in any AP page -> "not_found", no LLM call.
  3. Optionally Jevlin /v1/fetch for the best AP pages (falls back to the search excerpts).
  4. An LLM extracts {winner, ap_call_time, ap_url, quote}.
  5. The code validates it: the winner must be one of the race's candidates in data/races.json;
     the URL must be an apnews.com page this search returned; the quote must appear word for word
     in that page's text and name the winner; the call time must be after poll close and not in
     the future. Anything else is "rejected", with reasons.
Results go to state/proposals.json (merged with what is there, written atomically).
"""

import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agent import config
from agent.budget import Budget
from agent.guard import ET, STATE_NAMES
from agent.jevlin import JevlinClient, JevlinError, cost_usd
from agent.models import message_text, parse_json_object
from agent.state import is_called
from agent.store import Store, now_iso

PROMPT = (Path(__file__).parent / "prompts" / "call_finder.txt").read_text()
OFFICE_ORDER = {"senate": 0, "governor": 1, "house": 2}
OFFICE_WORD = {"senate": "Senate", "governor": "governor", "house": "House"}
AP_HOSTS = ("apnews.com",)
MAX_PAGE_CHARS = 6000
MAX_PAGES_TO_MODEL = 6


# ---------- races ----------

def load_races(path: Path | None = None) -> dict[str, dict]:
    doc = json.loads(Path(path or config.races_path()).read_text())
    races = doc["races"] if isinstance(doc, dict) else doc
    return {r["id"]: r for r in races}


def display_name(race: dict) -> str:
    if race.get("name") and not race["name"].lower().startswith("test race"):
        return race["name"]
    st = STATE_NAMES.get((race.get("state") or "").upper(), race.get("state") or "")
    if race.get("office") == "house":
        d = race.get("district")
        if d in ("AL", None):
            return f"{st} at-large House"
        return f"{st} District {int(d)} House" if str(d).isdigit() else f"{st} {d} House"
    return f"{st} {OFFICE_WORD.get(race.get('office'), race.get('office') or '')}".strip()


def pending_races(state: dict, now: datetime, race_ids: list[str] | None = None) -> list[dict]:
    """Closed, uncalled races, Senate and Governor first, then by poll close."""
    out = []
    for r in state.get("races", []):
        if race_ids and r["id"] not in race_ids:
            continue
        if is_called(r):
            continue
        close = r.get("poll_close_et")
        if not close or datetime.fromisoformat(close) > now:
            continue  # never search before polls close, even for an explicit --races list
        if not race_ids and r.get("status") != "closed":
            continue
        out.append(r)
    return sorted(out, key=lambda r: (OFFICE_ORDER.get(r.get("office"), 9), r.get("poll_close_et") or ""))


# ---------- text helpers ----------

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def norm_url(u: str) -> str:
    u = re.sub(r"^https?://(www\.)?", "", (u or "").strip(), flags=re.IGNORECASE)
    return u.split("#", 1)[0].rstrip("/").lower()


def is_ap(url: str) -> bool:
    host = norm_url(url).split("/", 1)[0]
    return any(host == h or host.endswith("." + h) for h in AP_HOSTS)


def surname(c: dict) -> str:
    if c.get("surname"):
        return c["surname"]
    parts = [p for p in c["name"].split() if p.rstrip(".") not in ("Jr", "Sr", "II", "III", "IV")]
    return parts[-1]


def pages_from(resp: dict) -> list[dict]:
    """AP pages from a merged search response: url, title and text. No other web text is kept."""
    out, seen = [], set()
    for m in resp.get("merged") or []:
        url = m.get("url") or ""
        if not is_ap(url) or norm_url(url) in seen:
            continue
        seen.add(norm_url(url))
        text = m.get("excerpt") or " ".join(m.get("excerpts") or [])
        out.append({"url": url, "source_id": m.get("source_id"), "title": m.get("title") or "", "text": text})
    return out


# ---------- validation ----------

def validate(raw: dict, race_entry: dict, pages: list[dict], *, poll_close: str | None, now: datetime,
             ) -> tuple[dict | None, list[str]]:
    """Check an LLM extraction. Returns (proposal, []) or (None, reasons)."""
    reasons: list[str] = []
    winner_raw = raw.get("winner")
    if winner_raw in (None, "", "null"):
        return None, []
    cands = race_entry.get("candidates") or []
    match = [c for c in cands if norm(c["name"]) == norm(str(winner_raw))]
    if len(match) != 1:
        return None, [f"proposed winner {str(winner_raw)[:80]!r} is not one of this race's candidates "
                      f"({', '.join(c['name'] for c in cands)})"]
    cand = match[0]

    url = str(raw.get("ap_url") or "")
    page = next((p for p in pages if norm_url(p["url"]) == norm_url(url)), None)
    if not is_ap(url):
        reasons.append(f"ap_url {url[:120]!r} is not an apnews.com page")
    elif page is None:
        reasons.append("ap_url is not one of the pages this search returned")

    quote = str(raw.get("quote") or "")
    if not quote.strip():
        reasons.append("no quote")
    elif len(quote) > 500:
        reasons.append("quote is too long to be one sentence")
    elif page is not None:
        if norm(quote) not in norm(page["title"] + " " + page["text"]):
            reasons.append("quote does not appear word for word on that page")
        if norm(surname(cand)) not in norm(quote):
            reasons.append(f"quote does not name {cand['name']}")

    call_time = raw.get("ap_call_time")
    if call_time in (None, "", "null"):
        call_time = None
    else:
        try:
            t = datetime.fromisoformat(str(call_time))
            if t.tzinfo is None:
                raise ValueError("no UTC offset")
            if poll_close and t < datetime.fromisoformat(poll_close):
                reasons.append(f"ap_call_time {call_time} is before poll close {poll_close}")
            if t > now + timedelta(minutes=5):
                reasons.append(f"ap_call_time {call_time} is in the future")
            call_time = t.astimezone(ET).isoformat()
        except ValueError as e:
            reasons.append(f"ap_call_time {str(call_time)[:40]!r} is not ISO 8601 with an offset ({e})")
    if reasons:
        return None, reasons
    return {"winner": cand["name"], "party": cand.get("party"), "ap_call_time": call_time,
            "ap_url": page["url"], "quote": quote.strip()}, []


# ---------- one race ----------

class CallFinder:
    def __init__(self, *, client: JevlinClient, model: BaseChatModel, budget: Budget, store: Store,
                 races: dict[str, dict], run_id: str, fetch: bool = False, jevlin_cap_usd: float = 1.0,
                 year: int = 2026, recency: str | None = "day", round_minutes: int = 5):
        self.client, self.model, self.budget, self.store = client, model, budget, store
        self.races, self.run_id, self.fetch = races, run_id, fetch
        self.jevlin_cap_usd, self.year, self.recency, self.round_minutes = jevlin_cap_usd, year, recency, round_minutes

    def query(self, race: dict) -> str:
        return f"AP race call {display_name(race)} {self.year}"

    def search_body(self, race: dict) -> dict:
        body: dict[str, Any] = {"query": self.query(race), "tier": "balanced", "domain_filter": list(AP_HOSTS),
                                "view": "merged", "merged_limit": 10}
        if self.recency:
            body["recency"] = self.recency
        return body

    def _spend(self, step: str, usd: float) -> None:
        self.store.record_spend(self.run_id, "jevlin", step, {}, usd)

    def find(self, race: dict, now: datetime) -> dict:
        base = {"race_id": race["id"], "checked_at": now_iso(), "unconfirmed": True,
                "note": "A proposal, not a call. A person must confirm it against AP before it is shown."}
        entry = self.races.get(race["id"])
        if entry is None:
            return {**base, "status": "error", "reasons": ["race is not in data/races.json"]}
        if (spent := self.store.jevlin_spend_for_run(self.run_id)) >= self.jevlin_cap_usd:
            return {**base, "status": "skipped", "reasons": [f"Jevlin cap for this run reached (${spent:.3f})"]}

        body = self.search_body({**entry, "name": race.get("name")})
        rnd = now.astimezone(timezone.utc)
        rnd = rnd - timedelta(minutes=rnd.minute % self.round_minutes, seconds=rnd.second, microseconds=rnd.microsecond)
        try:
            resp = self.client.search(body, idempotency_key=f"midterms-cf-{race['id']}-{rnd:%Y%m%dT%H%M}")
        except JevlinError as e:
            return {**base, "status": "error", "query": body["query"], "reasons": [str(e)]}
        self._spend("call_finder.search", cost_usd(resp))
        pages = pages_from(resp)
        base = {**base, "query": body["query"], "search_request_id": resp.get("request_id"),
                "ap_pages": [p["url"] for p in pages]}

        surnames = [norm(surname(c)) for c in entry.get("candidates") or []]
        relevant = [p for p in pages if any(s in norm(p["title"] + " " + p["text"]) for s in surnames)]
        if not relevant:
            return {**base, "status": "not_found", "reasons": ["no AP page names a candidate"]}

        if self.fetch:
            self._fetch_into(relevant[:2])

        self.budget.check(self.run_id)
        msg = self.model.invoke([SystemMessage(PROMPT), HumanMessage(self._prompt(race, entry, relevant))])
        self.budget.record(self.run_id, msg, "call_finder.extract")
        try:
            raw = parse_json_object(message_text(msg))
        except ValueError as e:
            return {**base, "status": "rejected", "reasons": [str(e)]}
        proposal, reasons = validate(raw, entry, relevant, poll_close=race.get("poll_close_et") or
                                     entry.get("poll_close_et"), now=now)
        if reasons:
            return {**base, "status": "rejected", "reasons": reasons,
                    "model_output": {k: str(raw.get(k))[:300] for k in ("winner", "ap_call_time", "ap_url", "quote")}}
        if proposal is None:
            return {**base, "status": "not_found", "reasons": ["the model found no AP call on these pages"]}
        return {**base, "status": "proposed", **proposal}

    def _fetch_into(self, pages: list[dict]) -> None:
        try:
            resp = self.client.fetch([p["url"] for p in pages], max_chars=MAX_PAGE_CHARS, query="AP race call")
        except JevlinError:
            return  # the search excerpts are still there
        self._spend("call_finder.fetch", cost_usd(resp))
        for got in resp.get("pages") or []:
            if got.get("status") != "ok" or not got.get("content"):
                continue
            for p in pages:
                if norm_url(p["url"]) == norm_url(got.get("url") or ""):
                    p["text"] = got["content"][:MAX_PAGE_CHARS]

    def _prompt(self, race: dict, entry: dict, pages: list[dict]) -> str:
        cands = "\n".join(f"- {c['name']}" for c in entry.get("candidates") or [])
        blocks = "\n\n".join(f'<page url="{p["url"]}">\n<title>{p["title"]}</title>\n{p["text"][:MAX_PAGE_CHARS]}\n</page>'
                             for p in pages[:MAX_PAGES_TO_MODEL])
        return (f"Race: {display_name({**entry, 'name': race.get('name')})} (id {race['id']}), election year "
                f"{self.year}.\nPolls closed: {race.get('poll_close_et') or entry.get('poll_close_et')}\n"
                f"Candidates (write the winner exactly as one of these):\n{cands}\n\nPages:\n{blocks}")


# ---------- proposals file ----------

def write_proposals(path: Path, results: list[dict]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "proposals": {}}
    if path.exists():
        try:
            doc = json.loads(path.read_text())
        except ValueError:
            pass
    doc.setdefault("proposals", {})
    for r in results:
        prev = doc["proposals"].get(r["race_id"])
        # A good proposal is not replaced by a later miss; a person may be checking it right now.
        if prev and prev.get("status") == "proposed" and r["status"] != "proposed":
            prev["last_checked_at"] = r["checked_at"]
            continue
        doc["proposals"][r["race_id"]] = r
    doc["generated_at"] = now_iso()
    doc["_doc"] = ("Agent proposals for AP race calls. NOT calls: a person confirms each against AP in "
                   "confirm/ before anything appears. This file is never read as a result.")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, path)
    return doc


def run_once(finder: CallFinder, state: dict, *, race_ids: list[str] | None = None, now: datetime | None = None,
             proposals_path: Path | None = None, refresh: bool = False) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    path = Path(proposals_path or config.proposals_path())
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text()).get("proposals", {})
        except ValueError:
            existing = {}
    results = []
    for race in pending_races(state, now, race_ids):
        if not refresh and not race_ids and (existing.get(race["id"]) or {}).get("status") == "proposed":
            continue  # waiting for a person
        results.append(finder.find(race, now))
    if results:
        write_proposals(path, results)
    return results


def loop(finder: CallFinder, state_loader, *, interval_s: int = 300, **kw) -> None:  # pragma: no cover
    while True:
        started = time.monotonic()
        for r in run_once(finder, state_loader(), **kw):
            print(f"{r['race_id']}: {r['status']} {r.get('winner') or ''} {'; '.join(r.get('reasons') or [])}",
                  flush=True)
        time.sleep(max(5, interval_s - (time.monotonic() - started)))
