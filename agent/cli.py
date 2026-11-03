"""`uv run midterms <command>`.

  ask "question"                 answer from the live results (read-only tools)
  draft-post RACE_ID             draft a post for a called race -> guard -> queue
  drafts [--status S]            list the draft queue
  publish ID --by NAME           a PERSON posts one draft to X (dry run unless MIDTERMS_PUBLISH_LIVE=true)
  reject ID --by NAME            a person rejects a draft
  call-finder [--once] [--races a,b] [--fetch]
                                 find AP calls for closed, uncalled races -> state/proposals.json
                                 (validated proposals only; every outcome -> state/call_finder.jsonl)
  post-drafter [--once] [--interval 60]
  autopost [--anchor ID] [--once] [--interval 30]
                                 draft posts as races get called (posting set, max MIDTERMS_DRAFTS_PER_HOUR)
  thread                         draft the results thread (the version the leaderboard supports) -> queue,
                                 and both versions -> notes/thread-draft.md (needs MIDTERMS_REPO_URL)
  track [--once] [--races a,b] --budget-usd N [--dry-run]
                                 the pre-registered agent track (agent/track.json) -> raw/agent/

Env: MIDTERMS_MODEL (required for ask/draft-post/call-finder). The approved value is
claude-sonnet-5-5; "offline" is free and calls no model. MIDTERMS_CALL_FINDER_MODEL overrides it
for the call finder only.
MIDTERMS_RUN_CAP_USD / MIDTERMS_DAILY_CAP_USD / MIDTERMS_TOTAL_CAP_USD (required for a billed
model), MIDTERMS_STATE (default state/state.json).
"""

import argparse
import json
import os
import sys
from pathlib import Path

from agent import config
from agent.config import ConfigError


def _state() -> dict:
    from agent.state import load_state

    return load_state()


def cmd_ask(a) -> int:
    from agent.guard import check_answer
    from agent.models import PlannedCall
    from agent.runtime import build_agent
    from agent.state import state_tools

    prompt = (Path(__file__).parent / "prompts" / "ask.txt").read_text()
    agent = build_agent(state_tools(), prompt, plan=[PlannedCall("leaderboard", {})])
    res = agent.run(a.question)
    if res.status != "succeeded":
        print(f"ask failed ({res.status}): {res.error}", file=sys.stderr)
        return 1
    if problems := check_answer(res.output or "", _state()):
        print("The answer was withheld: it named a candidate in a race that has not been called. "
              "Only AP calls confirmed by a person are shown.", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(res.output)
    return 0


def cmd_draft_post(a) -> int:
    from agent.drafts import Drafts
    from agent.post_draft import draft_for_race

    d = draft_for_race(a.race_id, _state(), Drafts())
    print(f"queued {d['id']} ({d['source']}; guard {'pass' if d['guard']['ok'] else 'FAIL'})")
    if d.get("agent_reasons"):
        print("the agent's draft was not used: " + "; ".join(d["agent_reasons"]))
    print("---\n" + d["text"] + "\n---")
    print(f"publish with: uv run midterms publish {d['id']} --by NAME")
    return 0


def cmd_drafts(a) -> int:
    from agent.drafts import Drafts

    ds = Drafts().list(a.status)
    if not ds:
        print("no drafts")
    for d in ds:
        guard = "pass" if d["guard"]["ok"] else "FAIL"
        extra = d["published"]["url"] if d.get("published") else (f"by {d['rejected']['by']}" if d.get("rejected") else
                                                                  (d["stale"]["reason"] if d.get("stale") else ""))
        print(f"{d['id']}  {d['status']:<9}  guard {guard}  {d['source']:<8}  {d['created_at']}  {extra}")
        print("    " + d["text"].replace("\n", "\n    "))
    return 0


def cmd_publish(a) -> int:
    from agent.drafts import Drafts, NotAuthorized

    drafts = Drafts()
    try:
        d = drafts.get(a.id)
        print("---\n" + d["text"] + "\n---")
        out = drafts.publish(a.id, by=a.by, state=_state(), retry_unsafe=a.retry_unsafe)
    except NotAuthorized as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if out.status == "dry_run":
        print(out.detail)
        print(json.dumps(out.plan, indent=2, ensure_ascii=False))
        return 0
    print(f"{out.status}: {out.detail}")
    return 0 if out.status == "posted" else 1


def cmd_reject(a) -> int:
    from agent.drafts import Drafts, NotAuthorized

    try:
        d = Drafts().reject(a.id, a.by, a.reason)
    except NotAuthorized as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"rejected {d['id']} by {d['rejected']['by']}")
    return 0


def cmd_call_finder(a) -> int:
    from agent.budget import Budget
    from agent.call_finder import CallFinder, load_races, loop, run_once
    from agent.jevlin import JevlinClient
    from agent.models import build_model
    from agent.runtime import new_run_id
    from agent.store import Store

    model_id = config.model_name("MIDTERMS_CALL_FINDER_MODEL")
    store = Store()
    from agent.jevlin import budget_since

    since = budget_since(a.budget_scope, a.budget_since)
    finder = CallFinder(client=JevlinClient(), model=build_model(model_id, max_tokens=1500),
                        budget=Budget.from_env(store, model_id), store=store, races=load_races(),
                        run_id=new_run_id("callfinder"), fetch=a.fetch, jevlin_cap_usd=a.jevlin_cap_usd,
                        budget_since=since)
    if since:
        print(f"Jevlin cap ${a.jevlin_cap_usd} for all call-finder runs since {since:%Y-%m-%d} ET; "
              f"spent so far ${finder.jevlin_spent():.3f}", file=sys.stderr)
    race_ids = [r.strip() for r in a.races.split(",")] if a.races else None
    if a.once:
        results = run_once(finder, _state(), race_ids=race_ids, refresh=a.refresh)
        for r in results:
            print(f"{r['race_id']}: {r['status']} {r.get('winner') or ''} {'; '.join(r.get('reasons') or [])}")
        print(f"{len(results)} races checked; Jevlin spend this run ${store.jevlin_spend_for_run(finder.run_id):.3f}; "
              f"proposals in {config.proposals_path()}")
        return 0
    loop(finder, _state, interval_s=a.interval, race_ids=race_ids, refresh=a.refresh)
    return 0


def cmd_post_drafter(a) -> int:
    from agent import post_drafter
    from agent.budget import Budget
    from agent.call_finder import load_races
    from agent.drafts import Drafts
    from agent.store import Store

    model = config.model_name()
    store = Store()
    budget = Budget.from_env(store, model)  # a billed model needs the three caps
    drafts, races = Drafts(store=store), load_races()
    kw = dict(races=races, model=model, budget=budget)
    if a.once:
        print(json.dumps(post_drafter.run_pass(_state(), drafts, store, **kw)))
        return 0
    post_drafter.loop(_state, drafts, store, interval_s=a.interval, **kw)
    return 0


def cmd_autopost(a) -> int:
    from agent import autopost
    from agent.drafts import Drafts
    from agent.store import Store

    drafts = Drafts(store=Store())
    anchor = (a.anchor or os.environ.get("MIDTERMS_AUTOPOST_ANCHOR", "")).strip() or None
    if a.once:
        print(json.dumps(autopost.run_pass(_state(), drafts, anchor=anchor)))
        return 0
    autopost.loop(_state, drafts, anchor=anchor, interval_s=a.interval)
    return 0


def cmd_thread(a) -> int:
    from agent import thread
    from agent.drafts import Drafts

    state, repo = _state(), thread.repo_url()
    path = thread.notes_path()
    thread.write_notes(path, state, repo)
    queued = thread.queue_thread(state, Drafts(), repo)
    print(f"thread written to {path}")
    for d in queued:
        print(f"queued {d['id']} ({d['position']}/{d['of']}){' reply to ' + d['reply_to_draft'] if d['reply_to_draft'] else ''}")
    print("publish them in order: uv run midterms publish <id> --by NAME (each part replies to the previous one)")
    return 0


def cmd_track(a) -> int:
    from agent import track
    from agent.call_finder import load_races

    cfg = track.load_config()
    races = track.eligible(load_races(), cfg)
    if a.races:
        want = [r.strip() for r in a.races.split(",") if r.strip()]
        by_id = {r["id"]: r for r in races}
        if bad := [r for r in want if r not in by_id]:
            raise ValueError(f"not in the track (Senate and Governor races in data/races.json): {', '.join(bad)}")
        races = [by_id[r] for r in want]
    if a.dry_run:
        print(json.dumps(track.dry_run(races, cfg), indent=2))
        return 0
    if a.budget_usd is None:
        raise ConfigError("a live run needs --budget-usd (Jevlin spend cap for this run; CLAUDE.md rule 6)")
    from agent.jevlin import budget_since

    since = budget_since(a.budget_scope, a.budget_since)
    model = a.model or cfg["model"]
    results = track.run(cfg=cfg, races=races, once=a.once, budget_usd=a.budget_usd, model=model, budget_since=since)
    for r in results:
        print(json.dumps(r))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="midterms", description="Jevlin midterms agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ask", help="ask about the results")
    s.add_argument("question")
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("draft-post", help="draft a post for a called race")
    s.add_argument("race_id")
    s.set_defaults(fn=cmd_draft_post)
    s = sub.add_parser("drafts", help="list the draft queue")
    s.add_argument("--status", choices=["queued", "published", "rejected", "stale"])
    s.set_defaults(fn=cmd_drafts)
    s = sub.add_parser("publish", help="a person posts one draft to X")
    s.add_argument("id")
    s.add_argument("--by", help="who is publishing (required)")
    s.add_argument("--retry-unsafe", action="store_true",
                   help="post even though an earlier attempt may have gone out (check X first)")
    s.set_defaults(fn=cmd_publish)
    s = sub.add_parser("reject", help="a person rejects a draft")
    s.add_argument("id")
    s.add_argument("--by", help="who is rejecting (required)")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_reject)
    s = sub.add_parser("call-finder", help="find AP calls -> state/proposals.json")
    s.add_argument("--once", action="store_true")
    s.add_argument("--races", help="comma-separated race ids (default: every closed, uncalled race)")
    s.add_argument("--fetch", action="store_true", help="also read the best AP pages in full (Jevlin fetch)")
    s.add_argument("--refresh", action="store_true", help="search again even where a proposal is waiting")
    s.add_argument("--jevlin-cap-usd", type=float, default=float(os.environ.get("MIDTERMS_CALL_FINDER_CAP_USD", "1.0")),
                   help="stop searching when this run's Jevlin spend reaches this (default 1.00)")
    s.add_argument("--interval", type=int, default=300, help="seconds between rounds (default 300)")
    s.add_argument("--budget-scope", choices=["run", "day"],
                   help="run: this process only (default); day: all of this command's Jevlin spend in the ledger "
                        "since 00:00 ET on --budget-since (or today), across restarts")
    s.add_argument("--budget-since", metavar="YYYY-MM-DD", help="ET date the day-scoped budget counts from "
                   "(implies --budget-scope day)")
    s.set_defaults(fn=cmd_call_finder)
    s = sub.add_parser("post-drafter", help="draft posts automatically as races get called (never publishes)")
    s.add_argument("--once", action="store_true")
    s.add_argument("--interval", type=int, default=60, help="seconds between passes (default 60)")
    s.set_defaults(fn=cmd_post_drafter)
    s = sub.add_parser("autopost", help="post each confirmed Senate/Governor call as a reply in @jevlinai's live thread")
    s.add_argument("--anchor", help="id of the post the thread hangs under (default MIDTERMS_AUTOPOST_ANCHOR)")
    s.add_argument("--once", action="store_true")
    s.add_argument("--interval", type=int, default=30, help="seconds between passes (default 30)")
    s.set_defaults(fn=cmd_autopost)
    s = sub.add_parser("thread", help="draft the results thread from the final state -> queue + notes/thread-draft.md")
    s.set_defaults(fn=cmd_thread)
    s = sub.add_parser("track", help="the pre-registered agent track (Agent on Jevlin) -> raw/agent/")
    s.add_argument("--once", action="store_true", help="one round now for the given races, ignoring poll close")
    s.add_argument("--races", help="comma-separated race ids (Senate and Governor only)")
    s.add_argument("--budget-usd", type=float, help="required: stop when this run's Jevlin spend reaches it")
    s.add_argument("--dry-run", action="store_true", help="print the schedule's checks and Jevlin upper bound; no calls")
    s.add_argument("--model", help="override the pre-registered model (rehearsals only; e.g. offline)")
    s.add_argument("--budget-scope", choices=["run", "day"],
                   help="run: this process only (default); day: all of this command's Jevlin spend in the ledger "
                        "since 00:00 ET on --budget-since (or today), across restarts")
    s.add_argument("--budget-since", metavar="YYYY-MM-DD", help="ET date the day-scoped budget counts from "
                   "(implies --budget-scope day)")
    s.set_defaults(fn=cmd_track)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (ConfigError, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
