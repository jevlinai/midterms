"""The results thread (Wed morning): drafted from the final state.json, never from search results.

Jevlin is the layer above the engines (as Cursor is above models), so there is no baseline: engines are
ranked, never set against each other, and there is one version of the thread.
Post 1: every race ran through Jevlin, and we watched which engine inside it found each result first (decided 3 Nov; the agent track was dropped). Jevlin is never called an agent: it is the search agents use.
Post 2 leads the results with what holds whatever they are (guard.first_coverage): no single engine was first on
every race, and asking every engine at once had each result as soon as the fastest one did. If one engine
was first everywhere, post 2 says that instead.
Post 3 names the fastest and slowest single engines; then the full ranking, the Jevlin and agent rows,
the method and the close.
Every post goes through guard.check_summary_post (numbers per engine, wrong answers beside speed,
the pre-registration hash, known links only, no candidates). The thread is queued as a reply chain
(thread_id + position; `publish` posts each part as a reply to the previous part) and written to
notes/thread-draft.md for Gaurav to edit.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from agent import config
from agent.config import ConfigError
from agent.drafts import Drafts
from agent.guard import (MAX_WEIGHTED, board_rows, check_summary_post, engine_map, first_coverage,
                         single_engines, weighted_length)

VERSIONS = ("results",)  # one version: engines are ranked, with no baseline to win or lose against


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
    """The thread as in the agreed draft (notes/thread-draft.md mirrors it): 1 the race, 2 the result,
    3 the provider ranking, 4 all engines at once, 5 how it was kept honest,
    6 superintelligence, 7 the close."""
    if version not in VERSIONS:
        raise ValueError(f"version must be one of {VERSIONS}")
    rows, emap = board_rows(state), engine_map(state)
    label = lambda e: emap.get(e, {}).get("label", e)
    cov = first_coverage(state)
    order = sorted((e for e in single_engines(state) if e in rows),
                   key=lambda e: (rows[e].get("median_minutes_after_close") is None,
                                  rows[e].get("median_minutes_after_close") or 0, e))
    board = _pack(f"The provider ranking, {cov['called']} races. Median minutes from poll close to the winner (wrong answers):",
                  [f"{label(e)}: {_m(rows[e])} ({_w(rows[e])})" for e in order])

    merged = []
    if "jevlin" in rows:
        merged.append(f"Jevlin, all engines at once: {_m(rows['jevlin'])} median ({_w(rows['jevlin'])}).")
    merged.append("Asking every engine gets every right answer first, and every wrong one too. That's why nothing "
                  "showed until AP called it.")

    sha = ((state.get("prereg") or {}).get("sha256") or "")[:16]
    method = ("The race list, queries, engines and scoring rule were locked before the first poll closed"
              + (f". SHA-256 {sha}" if sha else "") + ".\nNo winner was shown until AP called the race and a person "
              "checked the call.")
    why = ("Superintelligence needs the last mile of knowledge: whatever the world learns, reachable minutes after it "
           "happens, wherever it sits.\nNo single provider gets there alone. Supersearch is how an agent reaches all of it.")
    close = ("Jevlin is supersearch for coding agents: one call, every engine, one merged list.\n"
             f"Code, raw data and every query: {repo}\nResults: jevlin.ai/midterms")
    return [compose_opening(state), compose_lead(state), *board, "\n".join(merged), method, why, close]


def _names(ids: list[str], label) -> str:
    names = [label(e) for e in ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def compose_lead(state: dict) -> str:
    """Post 1: true whatever the ranking. Numbers come from guard.first_coverage, which the guard knows."""
    cov = first_coverage(state)
    if not cov["found"]:
        raise ValueError("no engine found any called race; there is no thread to write")
    emap = engine_map(state)
    label = lambda e: emap.get(e, {}).get("label", e)
    n = len(single_engines(state))
    if cov["one_engine_everywhere"]:
        return (f"One engine was first on every race any engine found: {_names(cov['best'], label)}.\n"
                f"One Jevlin call matched it without knowing in advance which engine it would be. That's supersearch.")
    return (f"No single engine was first on every race. {cov['engines_first']} different engines were first at least "
            f"once, and the best was first on {cov['best_first']} of {cov['found']}: {_names(cov['best'], label)}.\n"
            f"One Jevlin call asks all of them, so you don't have to guess which. That's supersearch.")


def compose_opening(state: dict) -> str:
    """Post 1: every race through Jevlin, and which engine inside it found each result first."""
    n = len(single_engines(state))
    return (f"Last night we ran every US midterm race through Jevlin: one call, {n} search engines, every few minutes.\n"
            "Every Senate, Governor and competitive House race. Then we watched which engine inside it found each "
            "result first.")


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
    lines = ["# Results thread draft", "",
             f"Generated {datetime.now(timezone.utc).isoformat(timespec='minutes')} from state.json "
             f"(generated_at {state.get('generated_at')}).",
             "Edit freely, but keep every number as it is, or run the guard again: a changed number must "
             "still match state.json.", ""]
    posts = compose(state, "results", repo)
    for i, (p, (ok, why)) in enumerate(zip(posts, check_thread(posts, state, repo)), 1):
        lines += [f"### {i}/{len(posts)}  ({weighted_length(p)}/280, guard {'pass' if ok else 'FAIL'})", "",
                  "```", p, "```", ""]
        lines += [f"- guard: {r}" for r in why] + ([""] if why else [])
    text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text


def queue_thread(state: dict, drafts: Drafts, repo: str) -> list[dict]:
    """Queue the thread as a reply chain. Refuses if any part fails the guard."""
    posts = compose(state, "results", repo)
    bad = [(i + 1, why) for i, (ok, why) in enumerate(check_thread(posts, state, repo)) if not ok]
    if bad:
        raise ValueError("the thread fails the guard: " + "; ".join(f"part {i}: {', '.join(w)}" for i, w in bad))
    thread_id = "thread-results"
    queued, prev = [], None
    for i, text in enumerate(posts, 1):
        d = drafts.add(thread_id, text, state, source="thread", kind="thread", thread_id=thread_id, position=i,
                       of=len(posts), repo_url=repo, reply_to_draft=prev["id"] if prev else None)
        queued.append(d)
        prev = d
    return queued


def notes_path() -> Path:
    return Path(os.environ.get("MIDTERMS_THREAD_NOTES") or config.ROOT / "notes" / "thread-draft.md")
