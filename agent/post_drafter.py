"""post-drafter: drafts live posts automatically as races get called. It never publishes.

Each pass:
  1. reload state.json;
  2. retractions first: for every race that is not "called" (or whose confirmed call changed since
     it was drafted), mark its queued drafts `stale`, so `publish` refuses them;
  3. for each called race in the posting set (every Senate and Governor race, plus House races whose
     cook_rating in data/races.json starts with "toss_up") with no draft yet, run the draft-post
     flow (agent draft -> guard -> template fallback -> queue), earliest AP call first;
  4. at most MIDTERMS_DRAFTS_PER_HOUR (default 10) drafts in any rolling hour: over the cap, it
     drafts nothing more until a later pass.
"Already drafted" is the `drafted` table in state/agent.sqlite, keyed by race_id, so a restart never
drafts a race twice. A race re-drafted after a retraction is one whose new call differs from the one
drafted (call_key = winner + AP link + call time) and whose old draft went stale.
"""

import hashlib
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from agent.drafts import Drafts
from agent.post_draft import draft_for_race
from agent.state import is_called
from agent.store import Store

DEFAULT_PER_HOUR = 10


def per_hour_cap() -> int:
    v = os.environ.get("MIDTERMS_DRAFTS_PER_HOUR", "").strip()
    return int(v) if v else DEFAULT_PER_HOUR


def posting_set(races: dict[str, dict]) -> set[str]:
    out = set()
    for rid, r in races.items():
        if r.get("office") in ("senate", "governor"):
            out.add(rid)
        elif r.get("office") == "house" and str(r.get("cook_rating") or "").startswith("toss_up"):
            out.add(rid)
    return out


def call_key(race: dict) -> str:
    c = race.get("call") or {}
    raw = "|".join(str(c.get(k) or "") for k in ("winner", "ap_url", "ap_call_time"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def log(*parts) -> None:
    print(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"), *parts, file=sys.stderr, flush=True)


def run_pass(state: dict, drafts: Drafts, store: Store, *, races: dict[str, dict], model, budget=None,
             final_text: str | None = None, per_hour: int | None = None, now: datetime | None = None,
             log=log) -> dict:
    now = now or datetime.now(timezone.utc)
    per_hour = per_hour_cap() if per_hour is None else per_hour
    allowed = posting_set(races)
    stats = {"drafted": 0, "stale": 0, "capped": 0}
    by_race: dict[str, list[dict]] = {}
    for d in drafts.list():
        if d.get("kind", "race") == "race":
            by_race.setdefault(d["race_id"], []).append(d)

    # 1. retractions and changed calls: no queued draft may outlive the call it was written for
    for race in state.get("races", []):
        rid = race["id"]
        queued = [d for d in by_race.get(rid, []) if d["status"] == "queued"]
        if not queued:
            continue
        rec = store.drafted(rid)
        if not is_called(race):
            reason = f"the call for {rid} was retracted (status {race.get('status')})"
        elif rec and rec["call_key"] != call_key(race):
            reason = f"the confirmed call for {rid} changed after this draft was written"
        else:
            continue
        for d in queued:
            drafts.mark_stale(d["id"], reason)
            stats["stale"] += 1
            log(f"stale {d['id']}: {reason}")

    # 2. new calls
    todo = []
    for race in state.get("races", []):
        rid = race["id"]
        if rid not in allowed or not is_called(race):
            continue
        rec = store.drafted(rid)
        if rec and rec["call_key"] == call_key(race):
            continue  # drafted for this call already (survives restarts)
        live = [d for d in by_race.get(rid, []) if d["status"] in ("queued", "published")]
        if rec is None and live:
            store.record_drafted(rid, live[0]["id"], call_key(race), made=False)  # drafted by hand earlier
            log(f"skip {rid}: already has draft {live[0]['id']} ({live[0]['status']})")
            continue
        todo.append(race)
    todo.sort(key=lambda r: (r["call"].get("ap_call_time") or "", r["id"]))

    for race in todo:
        made = store.drafts_made_since(now - timedelta(hours=1))
        if made >= per_hour:
            stats["capped"] = len(todo) - stats["drafted"]
            log(f"cap reached: {made} drafts in the last hour (MIDTERMS_DRAFTS_PER_HOUR={per_hour}); "
                f"{stats['capped']} races wait for a later pass")
            break
        d = draft_for_race(race["id"], state, drafts, model=model, final_text=final_text, budget=budget, store=store)
        store.record_drafted(race["id"], d["id"], call_key(race))
        stats["drafted"] += 1
        why = f"; agent draft not used: {'; '.join(d['agent_reasons'])}" if d.get("agent_reasons") else ""
        log(f"drafted {race['id']} -> {d['id']} ({d['source']}, guard {'pass' if d['guard']['ok'] else 'FAIL'}){why}")
    return stats


def loop(state_loader, drafts: Drafts, store: Store, *, interval_s: int = 60, **kw) -> None:  # pragma: no cover
    while True:
        started = time.monotonic()
        try:
            run_pass(state_loader(), drafts, store, **kw)
        except (OSError, ValueError) as e:  # a half-written state.json: try again next pass
            log(f"pass failed: {type(e).__name__}: {e}")
        time.sleep(max(1, interval_s - (time.monotonic() - started)))
