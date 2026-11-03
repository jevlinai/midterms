"""autopost: the night's race posts, published automatically as one live thread on @jevlinai.

Decided 3 Nov (Hammad): every confirmed Senate and Governor call goes out as a reply under the
pre-registration post on @jevlinai (an account labelled "Automated" on X), each reply answering the
previous one, so the night reads as one thread.

What stays human, and what doesn't:
  - The trigger is human. A race is posted only after a person confirmed AP's call (confirm/), the
    same rule as the page. Search results never call a race.
  - The words are not a model's. The text is the fixed template (guard.template_post, without the
    "Live:" line, which only the anchor post carries), filled from state.json. It goes through the same
    publish gate as a person's post: the guard against the current state, the posted-once ledger,
    and MIDTERMS_PUBLISH_LIVE=true.

Each pass:
  1. stop if the stop file exists (state/STOP_POSTING; `touch` it to stop at once) or the anchor
     post's id (MIDTERMS_AUTOPOST_ANCHOR) is unset;
  2. warn about any posted race whose call has since been withdrawn (a person deletes it on X);
  3. candidates: called races in the posting offices, not yet posted, where at least one engine has
     found the winner, skipping races where every engine was first together (nothing to tell);
  4. post the earliest-called candidate, if MIDTERMS_AUTOPOST_GAP_SECONDS (default 150) have passed
     since the last post, and fewer than MIDTERMS_AUTOPOST_MAX (default 40) have gone out.
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from agent import config
from agent.drafts import Drafts, NotAuthorized
from agent.guard import LIVE_URL, single_engines, template_post
from agent.state import is_called

SOURCE = "autopost"
OPENER_ID = "live-thread-opener"
# The first reply, posted as soon as the anchor is set: it tells readers what the thread is and that the
# agent posts it, and it proves the X keys work hours before the first race.
OPENER = ("Every Senate and Governor race tonight lands in this thread once AP calls it and a person has "
          "checked the call: which engine found the result first, and how many minutes after polls closed.\n\n"
          "Posted by the agent in the repo, not by hand.")
DEFAULT_GAP_S = 150
DEFAULT_MAX = 40
DEFAULT_OFFICES = ("senate", "governor")


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v else default


def stop_file() -> Path:
    return Path(os.environ.get("MIDTERMS_AUTOPOST_STOP") or config.ROOT / "state" / "STOP_POSTING")


def last_try_file() -> Path:
    return Path(os.environ.get("MIDTERMS_AUTOPOST_LAST_TRY") or config.ROOT / "state" / "autopost-last-try")


def _last_try() -> float:
    try:
        return float(last_try_file().read_text().strip())
    except (OSError, ValueError):
        return 0.0


def offices() -> tuple[str, ...]:
    v = os.environ.get("MIDTERMS_AUTOPOST_OFFICES", "").strip()
    return tuple(x.strip() for x in v.split(",") if x.strip()) if v else DEFAULT_OFFICES


def reply_text(race_id: str, state: dict) -> str:
    """The template post without the trailing "Live:" line: the anchor post carries the link once,
    so 40 replies don't repeat it (a repeated link reads as spam)."""
    text = template_post(race_id, state)
    lines = [ln for ln in text.splitlines() if ln.strip() != f"Live: {LIVE_URL}"]
    return "\n".join(lines).strip()


def worth_posting(race: dict, state: dict) -> tuple[bool, str]:
    first = [e for e in (race.get("first") or []) if e != "jevlin"]
    if not first:
        return False, "no engine has found the winner yet"
    ran = [e for e in single_engines(state) if e in (race.get("engines") or {})]
    if ran and set(ran) <= set(first):
        return False, "every engine was first together"
    return True, ""


def log(*parts) -> None:
    print(datetime.now(timezone.utc).isoformat(timespec="seconds"), "autopost:", *parts, file=sys.stderr, flush=True)


def run_pass(state: dict, drafts: Drafts, *, anchor: str | None, client=None, live: bool | None = None,
             now: float | None = None, gap_s: int | None = None, max_posts: int | None = None) -> dict:
    now = time.time() if now is None else now
    gap_s = _env_int("MIDTERMS_AUTOPOST_GAP_SECONDS", DEFAULT_GAP_S) if gap_s is None else gap_s
    max_posts = _env_int("MIDTERMS_AUTOPOST_MAX", DEFAULT_MAX) if max_posts is None else max_posts
    out = {"posted": None, "waiting": 0, "skipped": {}, "withdrawn": [], "stopped": None}
    if stop_file().exists():
        out["stopped"] = f"stop file {stop_file()} exists"
        return out
    if not anchor:
        out["stopped"] = "MIDTERMS_AUTOPOST_ANCHOR (the anchor post's id) is not set"
        return out

    races = {r["id"]: r for r in state.get("races", [])}
    mine = [d for d in drafts.list() if d.get("source") == SOURCE]
    # X post ids grow with time, so they order the thread even within one second
    published = sorted((d for d in mine if d["status"] == "published"),
                       key=lambda d: (int(d["published"]["tweet_id"]) if str(d["published"]["tweet_id"]).isdigit() else 0,
                                      d["published"]["at"]))
    # a race already posted by anyone (a person from the hand queue, or this job) is never posted again
    posted_races = {d["race_id"] for d in drafts.list() if d["status"] == "published" and d.get("kind", "race") == "race"}

    for d in published:  # a call withdrawn after posting: a person deletes the reply on X
        if d.get("kind") == "thread":
            continue
        r = races.get(d["race_id"])
        if r is None or not is_called(r):
            out["withdrawn"].append({"race_id": d["race_id"], "url": d["published"]["url"]})
            log(f"WITHDRAWN: {d['race_id']} is no longer called; delete {d['published']['url']} on X")

    if len(published) >= max_posts:
        out["stopped"] = f"reached the cap of {max_posts} posts"
        return out

    if not published:  # the opener first, right away
        d = drafts.add(OPENER_ID, OPENER, state, source=SOURCE, kind="thread", thread_id=OPENER_ID,
                       position=1, of=1, reply_to_tweet_id=str(anchor))
        if live if live is not None else config.publish_live():
            last_try_file().parent.mkdir(parents=True, exist_ok=True)
            last_try_file().write_text(str(now))
        try:
            res = drafts.publish(d["id"], by="autopost (opening reply)", state=state, live=live, client=client)
        except NotAuthorized as e:
            out["stopped"] = f"opening reply not posted: {e}"
            return out
        out["posted"] = {"race_id": OPENER_ID, "status": res.status, "url": res.url, "detail": res.detail}
        log(f"{res.status} opening reply: {res.detail}")
        return out

    todo = []
    for r in races.values():
        if r.get("office") not in offices() or r["id"] in posted_races or not is_called(r):
            continue
        ok, why = worth_posting(r, state)
        if not ok:
            out["skipped"][r["id"]] = why
            continue
        todo.append(r)
    todo.sort(key=lambda r: (r["call"]["ap_call_time"], r["id"]))
    out["waiting"] = len(todo)
    if not todo:
        return out

    last = _last_try()  # every attempt, posted or failed, so a persistent X error is not retried every pass
    if published:
        last = max(last, datetime.fromisoformat(published[-1]["published"]["at"].replace("Z", "+00:00")).timestamp())
    if now - last < gap_s:
        return out

    race = todo[0]
    reply_to = published[-1]["published"]["tweet_id"] if published else str(anchor)
    d = drafts.add(race["id"], reply_text(race["id"], state), state, source=SOURCE, reply_to_tweet_id=reply_to)
    if d["source"] != SOURCE:  # the same text already queued by hand: leave it to the person
        out["skipped"][race["id"]] = f"draft {d['id']} is already in the hand queue"
        return out
    by = "autopost (call confirmed by " + ", ".join(race["call"].get("confirmed_by") or ["?"]) + ")"
    if live if live is not None else config.publish_live():
        last_try_file().parent.mkdir(parents=True, exist_ok=True)
        last_try_file().write_text(str(now))
    try:
        res = drafts.publish(d["id"], by=by, state=state, live=live, client=client)
    except NotAuthorized as e:
        out["skipped"][race["id"]] = str(e)
        log(f"not posted {race['id']}: {e}")
        return out
    out["posted"] = {"race_id": race["id"], "status": res.status, "url": res.url, "detail": res.detail}
    log(f"{res.status} {race['id']}: {res.detail}")
    return out


def loop(state_loader, drafts: Drafts, *, anchor: str | None, interval_s: int = 30, **kw) -> None:  # pragma: no cover
    said = None
    while True:
        started = time.monotonic()
        try:
            res = run_pass(state_loader(), drafts, anchor=anchor, **kw)
            if res["stopped"] != said:  # say it once, not every pass
                log("stopped:" if res["stopped"] else "running", res["stopped"] or "")
                said = res["stopped"]
        except (OSError, ValueError) as e:  # a half-written state.json: try again next pass
            log(f"pass failed: {type(e).__name__}: {e}")
        time.sleep(max(1, interval_s - (time.monotonic() - started)))
