"""Read-only tools over state.json (docs/STATE.md), per the shared contract in agent/tools/state_tools.json.

The website's Ask function implements the same four tools in JS; names, inputs and outputs match.

Hard rules, enforced here and not left to state.json being right:
- No `call`, winner, AP link or per-engine find for a race whose status is not "called". Engine
  URLs are dropped too for such races: a page slug can name a candidate.
- No raw web text (titles, snippets, excerpts). Only ids, numbers, names from the race list, and URLs.
"""

import json
import statistics
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from agent import config

CONTRACT_PATH = Path(__file__).parent / "tools" / "state_tools.json"
OFFICES = ("senate", "governor", "house")
STATUSES = ("open", "closed", "called")
ENGINE_FIELDS = ("found_at", "minutes_after_close", "minutes_vs_call", "lead_vs_google", "wrong_at", "url",
                 "failed_rounds")
WITHIN = ("30", "60", "180")


class ToolError(ValueError):
    pass


def load_state(path: Path | None = None) -> dict:
    p = Path(path or config.state_path())
    if not p.exists():
        raise FileNotFoundError(f"no state file at {p} (set MIDTERMS_STATE, e.g. state/example-state.json)")
    return json.loads(p.read_text())


def contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text())


# ---------- helpers ----------

def engines(state: dict) -> dict[str, dict]:
    return {e["id"]: e for e in state.get("engines", [])}


def race_by_id(state: dict, race_id: str) -> dict | None:
    return next((r for r in state.get("races", []) if r.get("id") == race_id), None)


def is_called(race: dict) -> bool:
    c = race.get("call")
    return race.get("status") == "called" and isinstance(c, dict) and bool(c.get("winner"))


def _check_engine(state: dict, engine: str | None, field: str) -> None:
    if engine is not None and engine not in engines(state):
        raise ToolError(f"unknown {field} {engine!r}; known: {', '.join(engines(state))}")


def _check_enum(value: Any, allowed: tuple, field: str) -> None:
    if value is not None and value not in allowed:
        raise ToolError(f"{field} must be one of {', '.join(allowed)}")


def _num(v: Any) -> int | float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    m = statistics.median(xs)
    return int(m) if float(m).is_integer() else round(m, 1)


def _engine_row(e: dict, called: bool) -> dict:
    """One engine's numbers for a race. Uncalled: only the count of failed rounds survives."""
    if not called:
        return {"found_at": None, "minutes_after_close": None, "minutes_vs_call": None, "lead_vs_google": None,
                "wrong_at": None, "url": None, "failed_rounds": _num(e.get("failed_rounds")) or 0}
    url = e.get("url")
    return {"found_at": e.get("found_at") if isinstance(e.get("found_at"), str) else None,
            "minutes_after_close": _num(e.get("minutes_after_close")),
            "minutes_vs_call": _num(e.get("minutes_vs_call")),
            "lead_vs_google": _num(e.get("lead_vs_google")),
            "wrong_at": e.get("wrong_at") if isinstance(e.get("wrong_at"), str) else None,
            "url": url if isinstance(url, str) and url.startswith(("https://", "http://")) else None,
            "failed_rounds": _num(e.get("failed_rounds")) or 0}


def _candidates(race: dict) -> list[dict]:
    return [{"name": c.get("name"), "party": c.get("party"), "incumbent": bool(c.get("incumbent"))}
            for c in race.get("candidates", [])]


# ---------- the four tools ----------

def get_race(state: dict, race_id: str) -> dict:
    race = race_by_id(state, race_id)
    if race is None:
        raise ToolError(f"no race {race_id!r}; ids look like senate-ga, governor-az, house-ny-17")
    called = is_called(race)
    labels = {k: v.get("label", k) for k, v in engines(state).items()}
    out = {
        "id": race["id"], "office": race.get("office"), "state": race.get("state"), "district": race.get("district"),
        "name": race.get("name"), "poll_close_et": race.get("poll_close_et"),
        "status": race.get("status") if race.get("status") in STATUSES else "open",
        "pending_reason": race.get("pending_reason") if not called else None,
        "candidates": _candidates(race),
        "call": None,
        "engines": {},
        "first": [],
    }
    if out["status"] == "called" and not called:
        out["status"] = "closed"  # malformed call: never show it
    if called:
        c = race["call"]
        out["call"] = {"winner": c.get("winner"), "party": c.get("party"), "ap_call_time": c.get("ap_call_time"),
                       "ap_url": c.get("ap_url"), "confirmations": len(c.get("confirmed_by") or [])}
        out["first"] = [e for e in race.get("first", []) if e in labels]
    for eid, e in (race.get("engines") or {}).items():
        if eid in labels and isinstance(e, dict):
            out["engines"][eid] = {"label": labels[eid], **_engine_row(e, called)}
    return out


def list_races(state: dict, office: str | None = None, state_code: str | None = None, status: str | None = None,
               first_engine: str | None = None, limit: int | None = None) -> dict:
    _check_enum(office, OFFICES, "office")
    _check_enum(status, STATUSES, "status")
    _check_engine(state, first_engine, "first_engine")
    limit = max(1, min(int(limit or 20), 60))
    rows = []
    for race in state.get("races", []):
        if office and race.get("office") != office:
            continue
        if state_code and (race.get("state") or "").upper() != state_code.upper():
            continue
        r = get_race(state, race["id"])
        if status and r["status"] != status:
            continue
        if first_engine and first_engine not in r["first"]:
            continue
        lead = r["engines"].get(r["first"][0]) if r["first"] else None
        rows.append({"id": r["id"], "name": r["name"], "office": r["office"], "state": r["state"],
                     "district": r["district"], "status": r["status"], "pending_reason": r["pending_reason"],
                     "poll_close_et": r["poll_close_et"], "first": r["first"],
                     "minutes_after_close": lead["minutes_after_close"] if lead else None,
                     "lead_vs_google": lead["lead_vs_google"] if lead else None})
    return {"count": len(rows), "races": rows[:limit], "truncated": len(rows) > limit}


def _scored(state: dict, office: str | None) -> list[dict]:
    return [get_race(state, r["id"]) for r in state.get("races", [])
            if (office is None or r.get("office") == office) and is_called(r)]


def leaderboard(state: dict, office: str | None = None) -> dict:
    _check_enum(office, OFFICES, "office")
    labels = {k: v.get("label", k) for k, v in engines(state).items()}
    published = {row.get("engine"): row for row in state.get("leaderboard", [])}
    races = _scored(state, office)
    rows = []
    for eid, label in labels.items():
        covered = [r for r in races if eid in r["engines"]]
        found = [r["engines"][eid] for r in covered if r["engines"][eid]["found_at"]]
        mins = [e["minutes_after_close"] for e in found if e["minutes_after_close"] is not None]
        leads = [e["lead_vs_google"] for e in found if e["lead_vs_google"] is not None]
        rows.append({
            "engine": eid, "label": label,
            "races_scored": len(covered),
            "median_minutes_after_close": _median(mins),
            "found_within": {w: sum(1 for m in mins if m <= int(w)) for w in WITHIN},
            "found_first": sum(1 for r in covered if eid in r["first"]),
            "median_lead_vs_google": _median(leads),
            "never_found": len(covered) - len(found),
            "wrong": sum(1 for r in covered if r["engines"][eid]["wrong_at"]),
            "failed_rounds": sum(r["engines"][eid]["failed_rounds"] for r in covered),
            "cost_per_check_usd": _num((published.get(eid) or {}).get("cost_per_check_usd")),
        })
    rows = [r for r in rows if r["races_scored"]]
    rows.sort(key=lambda r: (r["median_minutes_after_close"] is None, r["median_minutes_after_close"] or 0))
    return {"office": office, "races_called": len(races), "baseline": next(
        (k for k, v in engines(state).items() if v.get("baseline")), None), "rows": rows}


def compare(state: dict, engine_a: str, engine_b: str, office: str | None = None) -> dict:
    _check_engine(state, engine_a, "engine_a")
    _check_engine(state, engine_b, "engine_b")
    _check_enum(office, OFFICES, "office")
    if engine_a == engine_b:
        raise ToolError("engine_a and engine_b are the same engine")
    a_ahead = b_ahead = ties = a_only = b_only = neither = 0
    gaps = []
    races = [r for r in _scored(state, office) if engine_a in r["engines"] and engine_b in r["engines"]]
    for r in races:
        ea, eb = r["engines"][engine_a], r["engines"][engine_b]
        ma, mb = ea["minutes_after_close"], eb["minutes_after_close"]
        if ea["found_at"] and eb["found_at"] and ma is not None and mb is not None:
            gaps.append(mb - ma)
            if ma < mb:
                a_ahead += 1
            elif mb < ma:
                b_ahead += 1
            else:
                ties += 1
        elif ea["found_at"]:
            a_only += 1
        elif eb["found_at"]:
            b_only += 1
        else:
            neither += 1
    return {"engine_a": engine_a, "engine_b": engine_b, "office": office, "races_compared": len(races),
            "a_ahead": a_ahead, "b_ahead": b_ahead, "ties": ties, "only_a_found": a_only, "only_b_found": b_only,
            "neither_found": neither,
            "median_gap_minutes": _median(gaps),  # b minus a; positive = engine_a found it first
            "gap_note": "median_gap_minutes is engine_b minus engine_a over races both found; positive means engine_a was ahead"}


# ---------- dispatch and LangChain tools ----------

def call_tool(state: dict, name: str, args: dict) -> dict:
    """Run one contract tool by name. Errors come back as {"error": ...}, never raised to the model."""
    try:
        if name == "get_race":
            return get_race(state, args["race_id"])
        if name == "list_races":
            return list_races(state, args.get("office"), args.get("state"), args.get("status"),
                              args.get("first_engine"), args.get("limit"))
        if name == "leaderboard":
            return leaderboard(state, args.get("office"))
        if name == "compare":
            return compare(state, args["engine_a"], args["engine_b"], args.get("office"))
        return {"error": f"unknown tool {name!r}"}
    except (ToolError, KeyError, TypeError, ValueError) as e:
        return {"error": str(e) if not isinstance(e, KeyError) else f"missing argument {e}"}


def state_tools(state_loader=load_state, names: tuple[str, ...] | None = None) -> list[BaseTool]:
    """The contract's tools as LangChain tools. State is re-read on every call, so answers track the
    latest state.json. Schemas come straight from state_tools.json."""
    out = []
    for spec in contract()["tools"]:
        if names and spec["name"] not in names:
            continue

        def run(_name=spec["name"], **kwargs):
            return json.dumps(call_tool(state_loader(), _name, kwargs), ensure_ascii=False)

        out.append(StructuredTool.from_function(func=run, name=spec["name"], description=spec["description"],
                                                args_schema=spec["input_schema"], infer_schema=False))
    return out
