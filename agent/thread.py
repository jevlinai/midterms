"""The results thread (Wed morning): drafted from the final state.json, never from search results.

Two versions are written by structure, and the leaderboard picks one (guard.results_outcome):
  ai_faster      "AI search was faster than Google": the fastest AI engine, or the agent, has a
                 strictly lower median minutes-after-close than Google (via Serper).
  google_faster  "Google was faster" (or "as fast", on a tie). Hard rule 4: said plainly.
Every post goes through guard.check_summary_post (numbers per engine, wrong answers beside speed,
the pre-registration hash, known links only, no candidates). The supported version is queued as a
reply chain (thread_id + position; `publish` posts each part as a reply to the previous part).
Both versions go to notes/thread-draft.md for Gaurav to edit.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from agent import config
from agent.config import ConfigError
from agent.drafts import Drafts
from agent.guard import (MAX_WEIGHTED, board_rows, check_summary_post, engine_map, results_outcome, single_engines,
                         weighted_length)

VERSIONS = ("ai_faster", "google_faster")


def repo_url() -> str:
    url = os.environ.get("MIDTERMS_REPO_URL", "").strip()
    if not url.startswith("https://"):
        raise ConfigError("set MIDTERMS_REPO_URL to the public repo (https://...) before drafting the thread")
    return url.rstrip("/")


def _m(row: dict | None) -> str:
    v = (row or {}).get("median_minutes_after_close")
    return "not found" if v is None else f"{v:g} min"


def _w(row: dict | None) -> str:
    return f"{(row or {}).get('wrong') or 0} wrong"


def compose(state: dict, version: str, repo: str) -> list[str]:
    if version not in VERSIONS:
        raise ValueError(f"version must be one of {VERSIONS}")
    out = results_outcome(state)
    rows, emap = board_rows(state), engine_map(state)
    label = lambda e: emap.get(e, {}).get("label", e)
    best, g = out["best"], out["baseline"]
    called = (state.get("summary") or {}).get("called")
    n = len(single_engines(state))
    intro = (f"Which search engine found the 2026 US midterm results first? We timed {n} search engines "
             f"through Jevlin on the {called} races AP called.")
    if version == "ai_faster":
        lead = out["lead"]  # the fastest AI engine, or the agent if only the agent beat Google
        head = (f"{intro}\nAI search was faster than Google.\n{label(lead)}: {_m(rows[lead])} median from poll "
                f"close ({_w(rows[lead])}).\n{label(g)}: {_m(rows[g])} ({_w(rows[g])}).")
    else:
        verb = "as fast as" if out["tie"] else "faster than"
        head = (f"{intro}\nGoogle was {verb} AI search.\n{label(g)}: {_m(rows[g])} median from poll close "
                f"({_w(rows[g])}).\nFastest AI engine, {label(best)}: {_m(rows[best])} ({_w(rows[best])}).")

    order = sorted((e for e in single_engines(state) if e in rows),
                   key=lambda e: (rows[e].get("median_minutes_after_close") is None,
                                  rows[e].get("median_minutes_after_close") or 0, e))
    board = _pack("Median minutes from poll close to a page naming AP's winner (wrong answers):",
                  [f"{label(e)}: {_m(rows[e])} ({_w(rows[e])})" for e in order])

    extra = [f"One Jevlin call asks all {n} engines at once, Google included."]
    if "jevlin" in rows:
        extra[0] += " The row below is the earliest of them for each race."
    if "jevlin" in rows:
        extra.append(f"Jevlin, all engines: {_m(rows['jevlin'])} median ({_w(rows['jevlin'])}).")
    if "agent" in rows:
        extra.append(f"Agent on Jevlin, Senate and Governor races only: {_m(rows['agent'])} ({_w(rows['agent'])}).")
    merged = "\n".join(extra)

    sha = ((state.get("prereg") or {}).get("sha256") or "")[:16]
    method = ("The race list, queries, engines, schedule and scoring rule were committed before polls closed"
              + (f", SHA-256 {sha}" if sha else "") + ". Race calls are AP's, each checked by a person before "
              f"it was shown.\nEvery query and response: {repo}")
    close = ("Jevlin is search for coding agents: one call searches every engine and returns one merged list."
             "\nFull results: jevlin.ai/midterms")
    return [head, *board, merged, method, close]


def _pack(header: str, lines: list[str]) -> list[str]:
    """The header and lines in as few posts as fit in 280."""
    posts, cur = [], header
    for line in lines:
        if weighted_length(cur + "\n" + line) > MAX_WEIGHTED:
            posts.append(cur)
            cur = line
        else:
            cur += "\n" + line
    posts.append(cur)
    return posts


def check_thread(posts: list[str], state: dict, repo: str) -> list[tuple[bool, list[str]]]:
    return [check_summary_post(p, state, repo_url=repo) for p in posts]


def write_notes(path: Path, state: dict, repo: str) -> str:
    out = results_outcome(state)
    lines = ["# Results thread draft", "",
             f"Generated {datetime.now(timezone.utc).isoformat(timespec='minutes')} from state.json "
             f"(generated_at {state.get('generated_at')}).",
             f"The leaderboard supports: **{out['version']}** (fastest AI engine: {out['best']}). "
             "Only that version is in the draft queue.",
             "Edit freely, but keep every number as it is, or run the guard again: a changed number must "
             "still match state.json.", ""]
    for v in VERSIONS:
        posts = compose(state, v, repo)
        results = check_thread(posts, state, repo)
        mark = "SUPPORTED by the data" if v == out["version"] else "NOT supported by the data: do not post"
        lines += [f"## Version: {v} ({mark})", ""]
        for i, (p, (ok, why)) in enumerate(zip(posts, results), 1):
            lines += [f"### {i}/{len(posts)}  ({weighted_length(p)}/280, guard {'pass' if ok else 'FAIL'})", "",
                      "```", p, "```", ""]
            lines += [f"- guard: {r}" for r in why] + ([""] if why else [])
    text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def queue_thread(state: dict, drafts: Drafts, repo: str) -> list[dict]:
    """Queue the supported version as a reply chain. Refuses if any part fails the guard."""
    out = results_outcome(state)
    posts = compose(state, out["version"], repo)
    bad = [(i + 1, why) for i, (ok, why) in enumerate(check_thread(posts, state, repo)) if not ok]
    if bad:
        raise ValueError("the thread fails the guard: " + "; ".join(f"part {i}: {', '.join(w)}" for i, w in bad))
    thread_id = f"thread-{out['version'].replace('_', '-')}"
    queued, prev = [], None
    for i, text in enumerate(posts, 1):
        d = drafts.add(thread_id, text, state, source="thread", kind="thread", thread_id=thread_id, position=i,
                       of=len(posts), repo_url=repo, reply_to_draft=prev["id"] if prev else None)
        queued.append(d)
        prev = d
    return queued


def notes_path() -> Path:
    return Path(os.environ.get("MIDTERMS_THREAD_NOTES") or config.ROOT / "notes" / "thread-draft.md")
