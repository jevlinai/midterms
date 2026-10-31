"""The agent track: "Agent on Jevlin", a pre-registered competitor beside the 7 single-query engines.

Everything around the model is fixed and logged, so the only thing that can vary between runs is
the model's own choices:
  - config: agent/track.json (model, effort, step budget, schedule, Jevlin request); its SHA-256 and
    the prompt's go into every record.
  - prompt: agent/prompts/track.txt (system prompt, then the user-message template).
  - schedule: Senate and Governor races, every 10 minutes at :04, :14, :24 ... (4 minutes after the
    poller's rounds), from poll close + 4 minutes for 3 hours. Round ids are the actual start
    (20261103T1914), and a check that starts late is stamped with the minute after it started.
  - per check (one race, one round): search 1 is the result of the race's pre-registered query
    (data/races.json). With first_search "reuse_poller" (the default) it is the poller's own latest
    record for the race, not a new call: the router cache is shared, so re-sending the poller's exact
    request would replay the poller's results to the agent and give the poller cache hits in its own
    rounds. No recent poller record -> status "no_baseline", no call. With "live" (only once Jevlin's
    cache-skip field is set) the code sends the query itself. Then the model may make up to 2 more
    searches and 2 fetches; the tools refuse past the budget, and refuse a query that normalises to
    the pre-registered one.
  - the model returns pages it would cite. It never declares a winner: detector/detect.py judges
    the pages exactly as it judges the engines'. An item is kept only if its URL came back from this
    check's tools and its snippet appears word for word in what Jevlin returned for that URL; the
    title comes from Jevlin. Dropped items are logged with the reason.

Output: one gzip JSONL record per race per round in raw/agent/YYYY-MM-DD/round-<YYYYMMDDTHHMM>.jsonl.gz
(ET), with every tool call (input, full response, request_id, cost, latency, cache_hit), the
model's messages and token usage. This is public data.

Money: a billed model needs all three LLM caps (MIDTERMS_*_CAP_USD) and every run needs
--budget-usd for Jevlin spend. When either is reached the current checks finish as status
"budget" and the track stops.
"""

import concurrent.futures as cf
import gzip
import hashlib
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from agent import config
from agent.budget import Budget
from agent.call_finder import norm, norm_url
from agent.jevlin import JevlinClient, JevlinError, cost_usd
from agent.models import build_model, message_text, parse_json_object
from agent.runtime import build_agent
from agent.store import Store, now_iso

ET = timezone(timedelta(hours=-5))  # EST: DST ended 1 Nov 2026
CONFIG_PATH = Path(__file__).parent / "track.json"
TEMPLATE_MARK = "=== USER MESSAGE TEMPLATE ==="
FIRST_SEARCH_MODES = ("reuse_poller", "live")


# ---------- config and prompt ----------

def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_config(path: Path | None = None) -> dict:
    p = Path(path or CONFIG_PATH)
    cfg = json.loads(p.read_text())
    cfg["_path"] = str(p)
    cfg["_sha256"] = sha256_file(p)
    prompt_path = config.ROOT / cfg["prompt"]
    system, _, template = prompt_path.read_text().partition(TEMPLATE_MARK)
    if not template:
        raise ValueError(f"{prompt_path} has no '{TEMPLATE_MARK}' line")
    cfg["_system_prompt"], cfg["_user_template"] = system.strip(), template.strip()
    cfg["_prompt_sha256"] = sha256_file(prompt_path)
    if cfg.get("first_search", "reuse_poller") not in FIRST_SEARCH_MODES:
        raise ValueError(f"first_search must be one of {FIRST_SEARCH_MODES}")
    return cfg


def norm_query(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


# ---------- the poller's record as search 1 ----------

def read_complete(path: Path) -> list[dict]:
    """Completed JSON lines of a gzip JSONL file that may still be being written."""
    lines = []
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                if line.endswith("\n"):
                    lines.append(line)
    except (EOFError, OSError):
        pass  # the last member is still being written; keep what came before
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def poller_baseline(poller_dir: Path, race_id: str, tick: datetime, max_age_minutes: int) -> dict | None:
    """The latest usable poller record for the race with round start in [tick - max_age, tick]."""
    for back in range(0, max_age_minutes + 1):  # newest first; the poller's file names say the round
        t = tick - timedelta(minutes=back)
        f = poller_dir / t.astimezone(ET).strftime("%Y-%m-%d") / f"round-{round_id(t)}.jsonl.gz"
        if not f.exists():
            continue
        for rec in reversed(read_complete(f)):
            s = rec.get("summary")
            if (rec.get("source") == "agent" or rec.get("race_id") != race_id or not s or s.get("cache_hit")
                    or rec.get("http_status") != 200 or not isinstance(rec.get("response"), dict)):
                continue
            return {"record": rec, "file": str(f), "round": rec.get("round")}
    return None


# ---------- schedule ----------

def round_start(t: datetime, minutes: int, offset: int = 0) -> datetime:
    """The scheduled round start at or before t, in ET: boundaries every `minutes` on the clock,
    shifted by `offset` minutes (10 and 4 give :04, :14, :24 ...)."""
    t = t.astimezone(ET).replace(second=0, microsecond=0)
    return t - timedelta(minutes=(t.minute - offset) % minutes)


def offset(cfg: dict) -> int:
    return int(cfg.get("round_offset_minutes", 0))


def floor_minute(t: datetime) -> datetime:
    return t.astimezone(ET).replace(second=0, microsecond=0)


def stamp(tick: datetime, started: datetime) -> datetime:
    """The `round` a check is credited with: the round start, or, for a check that started more than a
    minute late, the next whole minute after it started. Never earlier than the check by a minute or more."""
    if started - tick < timedelta(minutes=1):
        return tick
    up = floor_minute(started)
    return up if up == started.astimezone(ET) else up + timedelta(minutes=1)


def round_id(tick: datetime) -> str:
    return tick.astimezone(ET).strftime("%Y%m%dT%H%M")


def eligible(races: dict[str, dict], cfg: dict) -> list[dict]:
    return [r for r in races.values() if r.get("office") in cfg["offices"] and not r.get("uncontested")]


def active_races(races: list[dict], tick: datetime, cfg: dict) -> list[dict]:
    """Races to check in the round starting at `tick`: from poll close + offset for active_hours."""
    hours, off = cfg["active_hours"], timedelta(minutes=offset(cfg))
    out = []
    for r in races:
        start = datetime.fromisoformat(r["poll_close_et"]) + off
        if start <= tick < start + timedelta(hours=hours):
            out.append(r)
    return out


def schedule_end(races: list[dict], cfg: dict) -> datetime | None:
    if not races:
        return None
    return max(datetime.fromisoformat(r["poll_close_et"]) for r in races) + timedelta(
        hours=cfg["active_hours"], minutes=offset(cfg))


# ---------- spend ----------

class JevlinSpend:
    """Jevlin dollars for this run, against --budget-usd. Thread-safe; checked before every call."""

    def __init__(self, store: Store, run_id: str, budget_usd: float):
        self.store, self.run_id, self.budget_usd = store, run_id, budget_usd
        self.lock = threading.Lock()

    def spent(self) -> float:
        return self.store.jevlin_spend_for_run(self.run_id)

    def exceeded(self) -> bool:
        return self.spent() >= self.budget_usd

    def add(self, step: str, usd: float) -> None:
        with self.lock:
            self.store.record_spend(self.run_id, "jevlin", step, {}, usd)


class BudgetSpent(Exception):
    pass


class _Skip(Exception):
    """End a check early without a model call (e.g. no poller baseline)."""


# ---------- one check ----------

@dataclass
class Check:
    """The state of one check (one race, one round): the step budget and everything Jevlin returned."""

    race: dict
    round: str
    cfg: dict
    client: JevlinClient
    jevlin: JevlinSpend
    steps: list[dict] = field(default_factory=list)
    texts: dict[str, dict] = field(default_factory=dict)  # norm_url -> {"url", "title", "texts": [...]}
    searches: int = 0
    fetches: int = 0
    budget_hit: str | None = None

    @property
    def b(self) -> dict:
        return self.cfg["budget"]

    def remaining(self) -> dict:
        return {"searches_left": self.b["max_searches"] - self.searches, "fetches_left": self.b["max_fetches"] - self.fetches}

    def _remember(self, url: str, title: str | None, text: str | None) -> None:
        if not url:
            return
        e = self.texts.setdefault(norm_url(url), {"url": url, "title": title or "", "texts": []})
        if title and not e["title"]:
            e["title"] = title
        if text and text not in e["texts"]:
            e["texts"].append(text)

    def search_body(self, query: str) -> dict:
        j = self.cfg["jevlin"]
        body = {"query": query, "providers": list(j["providers"]), "tier": j["tier"], "recency": j["recency"],
                "view": j["view"], "merged_limit": j["merged_limit"]}
        body.update(self.cfg.get("jevlin_extra_request_fields") or {})
        return body

    def _shown(self, resp: dict) -> list[dict]:
        show = self.cfg["shown_to_model"]
        results = []
        for m in resp.get("merged") or []:
            text = m.get("excerpt") or " ".join(m.get("excerpts") or [])
            self._remember(m.get("url"), m.get("title"), text)
            if len(results) < show["results_per_search"]:
                results.append({"url": m.get("url"), "title": m.get("title") or "",
                                "text": (text or "")[:show["chars_per_result"]]})
        return results

    def reuse_poller(self, base: dict) -> dict:
        """Search 1 from the poller's record: counted, $0 to the track, logged by round and request_id."""
        rec = base["record"]
        resp = rec["response"]
        self.searches += 1
        self.steps.append({"n": len(self.steps) + 1, "tool": "jevlin_search", "by": "poller_reuse",
                           "input": rec.get("request"), "started_at": now_iso(), "cost_usd": 0.0,
                           "reused": {"poller_round": base["round"], "request_id": resp.get("request_id"),
                                      "file": base["file"],
                                      "poller_cost_usd": cost_usd(resp)},
                           "request_id": resp.get("request_id"), "cache_hit": False, "error": None,
                           "response": None, "_note": "full response is in the poller's file"})
        return {"search": self.searches, "query": (rec.get("request") or {}).get("query") or self.race["query"],
                "results": self._shown(resp), **self.remaining()}

    def search(self, query: str, *, by: str) -> dict:
        """One search, counted. Returns what the model sees (compact; the full response is in the step log)."""
        query = (query or "").strip()
        if not query:
            return {"error": "empty query", **self.remaining()}
        if by == "model" and norm_query(query) == norm_query(self.race["query"]):
            self._refused("jevlin_search", {"query": query}, "same as the pre-registered query (search 1)", by)
            return {"error": "that is the query of search 1, whose results you already have; reformulate it",
                    **self.remaining()}
        if self.searches >= self.b["max_searches"]:
            self._refused("jevlin_search", {"query": query}, "search budget spent", by)
            return {"error": f"search budget spent ({self.b['max_searches']} of {self.b['max_searches']} used); "
                             "give your answer now", **self.remaining()}
        if self.jevlin.exceeded():
            self.budget_hit = "jevlin --budget-usd reached"
            self._refused("jevlin_search", {"query": query}, self.budget_hit, by)
            raise BudgetSpent(self.budget_hit)
        self.searches += 1
        body = self.search_body(query)
        key = f"midterms-agent-{self.race['id']}-{self.round}-s{self.searches}"
        resp, step = self._call("jevlin_search", body, lambda: self.client.search(body, idempotency_key=key), by)
        step["idempotency_key"] = key
        if resp is None:
            return {"error": step["error"], **self.remaining()}
        return {"search": self.searches, "query": query, "results": self._shown(resp), **self.remaining()}

    def fetch(self, urls: list[str], *, by: str) -> dict:
        urls = [u for u in (urls or []) if isinstance(u, str) and u.strip()][: self.b["max_pages_per_fetch"]]
        if not urls:
            return {"error": "no urls", **self.remaining()}
        if self.fetches >= self.b["max_fetches"]:
            self._refused("jevlin_fetch", {"urls": urls}, "fetch budget spent", by)
            return {"error": f"fetch budget spent ({self.b['max_fetches']} of {self.b['max_fetches']} used); "
                             "give your answer now", **self.remaining()}
        unknown = [u for u in urls if norm_url(u) not in self.texts]
        if unknown:
            return {"error": f"only pages a search in this task returned can be fetched: {unknown}", **self.remaining()}
        if self.jevlin.exceeded():
            self.budget_hit = "jevlin --budget-usd reached"
            self._refused("jevlin_fetch", {"urls": urls}, self.budget_hit, by)
            raise BudgetSpent(self.budget_hit)
        self.fetches += 1
        body = {"pages": [{"url": u} for u in urls], "max_chars": self.cfg["jevlin"]["fetch_max_chars"]}
        resp, step = self._call("jevlin_fetch", body, lambda: self.client.fetch(
            urls, max_chars=self.cfg["jevlin"]["fetch_max_chars"]), by)
        if resp is None:
            return {"error": step["error"], **self.remaining()}
        cap = self.cfg["shown_to_model"]["chars_per_fetched_page"]
        pages = []
        for p in resp.get("pages") or []:
            if p.get("status") == "ok" and p.get("content"):
                self._remember(p.get("url"), p.get("title"), p["content"])
                pages.append({"url": p.get("url"), "status": "ok", "title": p.get("title") or "",
                              "text": p["content"][:cap]})
            else:
                pages.append({"url": p.get("url"), "status": p.get("status"), "reason": p.get("reason")})
        return {"fetch": self.fetches, "pages": pages, **self.remaining()}

    def _call(self, tool: str, body: dict, fn, by: str) -> tuple[dict | None, dict]:
        step = {"n": len(self.steps) + 1, "tool": tool, "by": by, "input": body, "started_at": now_iso()}
        t0 = time.monotonic()
        try:
            resp = fn()
            err = None
        except JevlinError as e:
            resp, err = None, str(e)
        step["latency_ms"] = round((time.monotonic() - t0) * 1000)
        usd = cost_usd(resp) if resp else 0.0
        usage = (resp or {}).get("usage") or {}
        step.update({"request_id": (resp or {}).get("request_id"), "cost_usd": usd, "error": err,
                     "cache_hit": bool(usage.get("cache_hit")),
                     "router_latency_ms": usage.get("latency_ms"),
                     "trimmed": ((resp or {}).get("decision") or {}).get("trimmed") or [],
                     "response": resp})
        self.steps.append(step)
        if usd:
            self.jevlin.add(f"track.{tool}", usd)
        return resp, step

    def _refused(self, tool: str, inp: dict, why: str, by: str) -> None:
        self.steps.append({"n": len(self.steps) + 1, "tool": tool, "by": by, "input": inp, "refused": why,
                           "started_at": now_iso(), "cost_usd": 0.0})

    def tools(self) -> list:
        def jevlin_search(query: str) -> str:
            return json.dumps(self.search(query, by="model"), ensure_ascii=False)

        def jevlin_fetch(urls: list[str]) -> str:
            return json.dumps(self.fetch(urls, by="model"), ensure_ascii=False)

        return [
            StructuredTool.from_function(
                func=jevlin_search, name="jevlin_search",
                description="Search across several search engines at once through Jevlin. Counts against the "
                            "search budget; refused when it is spent.",
                args_schema={"type": "object", "properties": {"query": {"type": "string", "description": "the search query"}},
                             "required": ["query"]}, infer_schema=False),
            StructuredTool.from_function(
                func=jevlin_fetch, name="jevlin_fetch",
                description="Full text of up to 2 pages that a search in this task returned. Counts against the "
                            "fetch budget; refused when it is spent.",
                args_schema={"type": "object", "properties": {"urls": {"type": "array", "items": {"type": "string"},
                                                                       "maxItems": 2}}, "required": ["urls"]},
                infer_schema=False),
        ]

    # ---------- the model's answer -> items ----------

    def verify_items(self, raw_items: Any) -> tuple[list[dict], list[dict]]:
        out = self.cfg["output"]
        kept, dropped = [], []
        if not isinstance(raw_items, list):
            return [], [{"reason": "items is not a list"}]
        seen = set()
        for it in raw_items:
            if not isinstance(it, dict):
                dropped.append({"item": str(it)[:200], "reason": "not an object"})
                continue
            url, text = str(it.get("url") or ""), str(it.get("text") or "")
            page = self.texts.get(norm_url(url))
            why = None
            if page is None:
                why = "url did not come back from this check's searches or fetches"
            elif not text.strip():
                why = "no text"
            elif len(text) > out["max_snippet_chars"]:
                why = f"text longer than {out['max_snippet_chars']} characters"
            elif not any(norm(text) in norm(t) for t in page["texts"] + [page["title"]]):
                why = "text does not appear word for word in what Jevlin returned for this url"
            elif norm_url(url) in seen:
                why = "duplicate url"
            elif len(kept) >= out["max_items"]:
                why = f"more than {out['max_items']} items"
            if why:
                dropped.append({"url": url[:300], "text": text[:300], "reason": why})
                continue
            seen.add(norm_url(url))
            kept.append({"url": page["url"], "title": page["title"], "snippet": text.strip()})
        return kept, dropped


def render_user_message(cfg: dict, race: dict, first: dict) -> str:
    cands = sorted((c["name"] for c in race.get("candidates") or []), key=lambda n: (n.split()[-1], n))
    if "results" in first:
        lines = [f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['text']}" for i, r in enumerate(first["results"])]
        results = "\n".join(lines) or "(no results)"
    else:
        results = f"(the search failed: {first.get('error')})"
    values = {"race_name": race["name"], "election_date": cfg["election_date"], "candidates": ", ".join(cands),
              "max_searches": str(cfg["budget"]["max_searches"]), "max_fetches": str(cfg["budget"]["max_fetches"]),
              "query": race["query"], "first_results": results}
    msg = cfg["_user_template"]
    for k, v in values.items():
        msg = msg.replace(f"<<{k}>>", v)
    return msg


def transcript(messages: list | None) -> list[dict]:
    out = []
    for m in messages or []:
        if isinstance(m, AIMessage):
            out.append({"role": "assistant", "text": message_text(m),
                        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in m.tool_calls or []],
                        "model": (m.response_metadata or {}).get("model_name"),
                        "stop_reason": (m.response_metadata or {}).get("stop_reason")})
    return out


def run_check(race: dict, tick: datetime, *, cfg: dict, client: JevlinClient, jevlin: JevlinSpend, budget: Budget,
              store: Store, run_id: str, model: str | Any, poller_dir: Path | None = None,
              now_fn=lambda: datetime.now(ET)) -> dict:
    rid = round_id(tick)  # the scheduled start: file name, idempotency keys, poller look-back
    credited = stamp(tick, now_fn())
    chk = Check(race, rid, cfg, client, jevlin)
    step_label = f"track:{race['id']}:{rid}"
    rec: dict[str, Any] = {"source": "agent", "round": round_id(credited), "scheduled_round": rid,
                           "race_id": race["id"], "status": "ok", "items": [],
                           "query": race["query"], "started_at": now_iso(),
                           "model": model if isinstance(model, str) else getattr(model, "model", "offline"),
                           "config_sha256": cfg["_sha256"], "prompt_sha256": cfg["_prompt_sha256"], "run_id": run_id,
                           "first_search": cfg.get("first_search", "reuse_poller")}
    output_text = None
    messages = None
    error = None
    user_message = None
    try:
        if rec["first_search"] == "reuse_poller":
            opts = cfg["first_search_options"]
            base = poller_baseline(Path(poller_dir or config.ROOT / opts["poller_raw_dir"]), race["id"], tick,
                                   opts["max_age_minutes"])
            if base is None:
                rec.update(status="no_baseline", error=f"no usable poller record for {race['id']} in the "
                           f"{opts['max_age_minutes']} minutes up to round {rid}; no search made")
                raise _Skip()
            first = chk.reuse_poller(base)
        else:
            first = chk.search(race["query"], by="code")
        user_message = render_user_message(cfg, race, first)
        agent = build_agent(chk.tools(), cfg["_system_prompt"], model=model if not isinstance(model, str) else
                            build_model(model, max_tokens=cfg["max_tokens"], effort=cfg["effort"]),
                            budget=budget, store=store, max_calls=cfg["budget"]["max_model_calls"],
                            budget_step=step_label)
        res = agent.run(user_message, run_id=run_id)
        messages, output_text = res.messages, res.output
        if chk.budget_hit or res.status == "over_budget":
            rec["status"] = "budget"
            error = chk.budget_hit or res.error
        elif res.status != "succeeded":
            rec["status"] = "error"
            error = f"agent {res.status}: {res.error}"
        else:
            try:
                parsed = parse_json_object(output_text or "")
                rec["items"], rec["dropped_items"] = chk.verify_items(parsed.get("items"))
            except ValueError as e:
                rec["status"], error = "error", f"answer is not the JSON object asked for: {e}"
    except BudgetSpent as e:
        rec["status"], error = "budget", str(e)
    except _Skip:
        error = rec["error"]
    usage = store.spend_by_step(run_id, step_label)
    jev_usd = round(sum(s.get("cost_usd") or 0 for s in chk.steps), 6)
    llm_usd = round(usage["usd"], 6)
    cache_hits = sum(1 for s in chk.steps if s.get("cache_hit"))
    rec.update({
        "error": error,
        "steps": chk.steps,
        "searches": chk.searches, "fetches": chk.fetches,
        "user_message": user_message,
        "transcript": transcript(messages),
        "model_output": output_text,
        "llm_usage": {"model": rec["model"], "calls": usage["calls"], "input_tokens": usage["input"],
                      "cache_read_tokens": usage["cache_read"], "cache_write_tokens": usage["cache_write"],
                      "output_tokens": usage["output"], "cost_usd": llm_usd},
        "jevlin_cost_usd": jev_usd, "llm_cost_usd": llm_usd, "cost_usd": round(jev_usd + llm_usd, 6),
        "cache_hits": cache_hits,
        "summary": {"cache_hit": bool(cache_hits) and bool(cfg["output"]["cache_hit_voids_record"]),
                    "request_ids": [s.get("request_id") for s in chk.steps if s.get("request_id")]},
        "finished_at": now_iso(),
    })
    rec.setdefault("dropped_items", [])
    return rec


# ---------- rounds ----------

def round_path(raw_dir: Path, tick: datetime) -> Path:
    return raw_dir / tick.astimezone(ET).strftime("%Y-%m-%d") / f"round-{round_id(tick)}.jsonl.gz"


def run_round(tick: datetime, races: list[dict], *, cfg: dict, raw_dir: Path, **kw) -> dict:
    path = round_path(raw_dir, tick)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    stats = {"ok": 0, "error": 0, "budget": 0, "no_baseline": 0, "items": 0, "cache_hits": 0, "cost_usd": 0.0}
    with gzip.open(path, "at") as fh, cf.ThreadPoolExecutor(max(1, cfg.get("max_workers", 1))) as pool:
        futs = {}
        for race in races:
            if kw["jevlin"].exceeded():
                stats["budget"] += 1
                log(f"Jevlin budget reached; not starting {race['id']}")
                continue
            futs[pool.submit(run_check, race, tick, cfg=cfg, **kw)] = race
        for fut in cf.as_completed(futs):
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001 - a crash in one check is logged as that race's record
                race = futs[fut]
                rec = {"source": "agent", "round": round_id(tick), "race_id": race["id"], "status": "error",
                       "items": [], "steps": [], "error": f"{type(e).__name__}: {e}", "cost_usd": 0.0,
                       "llm_usage": None, "summary": {"cache_hit": False}}
            with lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
            stats[rec["status"]] = stats.get(rec["status"], 0) + 1
            stats["items"] += len(rec.get("items") or [])
            stats["cache_hits"] += rec.get("cache_hits") or 0
            stats["cost_usd"] += rec.get("cost_usd") or 0
            log(f"{rec['race_id']} {rec['round']}: {rec['status']} items={len(rec.get('items') or [])} "
                f"steps={len(rec.get('steps') or [])} ${rec.get('cost_usd') or 0:.4f}"
                + (f" error={rec['error']}" if rec.get("error") else ""))
    stats["path"] = str(path)
    return stats


def log(*parts) -> None:
    print(datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET"), *parts, file=sys.stderr, flush=True)


def dry_run(races: list[dict], cfg: dict) -> dict:
    """Upper bound of checks and Jevlin spend, assuming every check uses its whole step budget."""
    m = cfg["round_minutes"]
    if not races:
        return {"checks": 0}
    first = min(datetime.fromisoformat(r["poll_close_et"]) for r in races) + timedelta(minutes=offset(cfg))
    tick = round_start(first, m, offset(cfg))
    end = schedule_end(races, cfg)
    checks = 0
    while tick < end:
        checks += len(active_races(races, tick, cfg))
        tick += timedelta(minutes=m)
    per_search, per_fetch_page = 0.027, 0.002  # measured poller check; Jevlin fetch per ok page
    b = cfg["budget"]
    paid = b["max_searches"] - (1 if cfg.get("first_search", "reuse_poller") == "reuse_poller" else 0)
    jev = checks * (paid * per_search + b["max_fetches"] * b["max_pages_per_fetch"] * per_fetch_page)
    return {"races": len(races), "checks": checks, "paid_searches_per_check": paid,
            "jevlin_upper_bound_usd": round(jev, 2),
            "note": "LLM spend is extra; capped by MIDTERMS_RUN/DAILY/TOTAL_CAP_USD"}


def run(*, cfg: dict, races: list[dict], once: bool, budget_usd: float, model: str | Any,
        client: JevlinClient | None = None, store: Store | None = None, raw_dir: Path | None = None,
        poller_dir: Path | None = None, run_id: str | None = None, now_fn=lambda: datetime.now(ET), sleep_fn=time.sleep) -> list[dict]:
    from agent.runtime import new_run_id

    store = store or Store()
    run_id = run_id or new_run_id("track")
    label = model if isinstance(model, str) else "offline"
    budget = Budget.from_env(store, label)
    jevlin = JevlinSpend(store, run_id, budget_usd)
    client = client or JevlinClient(base_url=cfg["jevlin"]["base_url"], timeout=cfg["jevlin"]["timeout_seconds"])
    raw_dir = Path(raw_dir or config.ROOT / cfg["output"]["raw_dir"])
    if label != cfg["model"]:
        log(f"WARNING: model {label!r} is not the pre-registered {cfg['model']!r}; these records are not the "
            "pre-registered track")
    kw = dict(cfg=cfg, raw_dir=raw_dir, client=client, jevlin=jevlin, budget=budget, store=store, run_id=run_id,
              model=model, poller_dir=poller_dir, now_fn=now_fn)
    results = []
    m, off = cfg["round_minutes"], offset(cfg)
    if once:
        results.append(run_round(floor_minute(now_fn()), races, **kw))  # the actual minute, not a boundary
        return results
    end = schedule_end(races, cfg)
    last = None
    while True:
        tick = round_start(now_fn(), m, off)
        if end is None or tick >= end:
            log("schedule over; track stopped")
            return results
        if tick != last:
            if last is not None and tick > last + timedelta(minutes=m):
                log(f"ALERT missed round(s) between {last:%H:%M} and {tick:%H:%M}")
            last = tick
            todo = active_races(races, tick, cfg)
            if todo:
                stats = run_round(tick, todo, **kw)
                results.append(stats)
                if stats.get("budget") or jevlin.exceeded():
                    log(f"budget reached (Jevlin ${jevlin.spent():.3f} of ${budget_usd}); track stopped")
                    return results
        nxt = tick + timedelta(minutes=m)
        sleep_fn(max(1.0, (nxt - now_fn()).total_seconds()))
