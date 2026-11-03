"""The call finder, on a recorded Jevlin response for a PAST race (Virginia governor, 4 Nov 2025).
Jevlin is a MockTransport serving the fixture; the model is OfflinePlanModel returning fixed JSON."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from agent import config
from agent.budget import Budget
from agent.call_finder import CallFinder, load_races, pending_races, run_once, validate, write_proposals
from agent.jevlin import JevlinClient
from agent.models import OfflinePlanModel
from conftest import FIXTURES

AP_URL = "https://apnews.com/article/virginia-governor-election-e965bd7b3081016e2e43bf107b236b82"
QUOTE = "The Associated Press declared Spanberger the winner at 7:58 p.m. EST."
NOW = datetime(2025, 11, 5, 3, 0, tzinfo=timezone.utc)
GOOD = {"winner": "Abigail Spanberger", "ap_call_time": "2025-11-04T19:58:00-05:00", "ap_url": AP_URL, "quote": QUOTE}


class FakeJevlin:
    def __init__(self, search="jevlin-search-va-gov-2025.json", fetch="jevlin-fetch-va-gov-2025-failed.json"):
        self.search = json.loads((FIXTURES / search).read_text())
        self.fetch = json.loads((FIXTURES / fetch).read_text())
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert req.headers["authorization"] == "Bearer sr-test"
        body = json.loads(req.content)
        if req.url.path == "/v1/search":
            assert req.headers["idempotency-key"].startswith("midterms-cf-governor-va-2025-")
            assert body["domain_filter"] == ["apnews.com"] and body["tier"] == "balanced" and body["view"] == "merged"
            return httpx.Response(200, json=self.search["response"])
        if req.url.path == "/v1/fetch":
            assert 1 <= len(body["pages"]) <= 5
            return httpx.Response(200, json=self.fetch["response"])
        return httpx.Response(404, json={"error": "unknown route", "code": "unknown_route"})

    @property
    def paths(self):
        return [r.url.path for r in self.requests]


class CountingModel(OfflinePlanModel):
    calls: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        type(self).calls.append(messages)
        return super()._generate(messages, stop, run_manager, **kw)


@pytest.fixture
def races():
    return load_races(FIXTURES / "races-va-2025.json")


@pytest.fixture
def va_state():
    """A synthetic state.json with only the past race in it, polls closed, not called."""
    return {"version": 1, "engines": [], "races": [{"id": "governor-va-2025", "office": "governor", "state": "VA",
                                                    "district": None, "name": "Virginia governor",
                                                    "poll_close_et": "2025-11-04T19:00:00-05:00", "status": "closed",
                                                    "candidates": [], "call": None, "engines": {}, "first": []}]}


@pytest.fixture
def fake_jevlin():
    return FakeJevlin()


def make_finder(store, races, fake_jevlin, answer, **kw):
    CountingModel.calls = []
    model = CountingModel(plan=[], final_text=answer if isinstance(answer, str) else json.dumps(answer))
    return CallFinder(client=JevlinClient("sr-test", transport=httpx.MockTransport(fake_jevlin), max_wait_s=0),
                      model=model, budget=Budget(store, "offline", run_cap_usd=0, daily_cap_usd=0, total_cap_usd=0),
                      store=store, races=races, run_id="cf_test", year=2025, recency=None, **kw)


def test_finds_the_ap_call_on_a_past_race(store, races, fake_jevlin, va_state):
    finder = make_finder(store, races, fake_jevlin, GOOD)
    [r] = run_once(finder, va_state, now=NOW)
    assert r["status"] == "proposed" and r["unconfirmed"] is True
    assert r["winner"] == "Abigail Spanberger" and r["party"] == "D"
    assert r["ap_url"] == AP_URL and r["ap_call_time"] == "2025-11-04T19:58:00-05:00" and r["quote"] == QUOTE
    assert r["query"] == "AP race call Virginia governor 2025"
    assert fake_jevlin.paths == ["/v1/search"]
    assert store.jevlin_spend_for_run("cf_test") == pytest.approx(0.022)
    # The contract confirm/confirm.py reads: keyed by race_id, exactly these fields, nothing else.
    doc = json.loads(config.proposals_path().read_text())
    assert list(doc) == ["governor-va-2025"]
    p = doc["governor-va-2025"]
    assert set(p) == {"race_id", "winner", "ap_call_time", "ap_url", "quote", "found_at", "search_request_id",
                      "call_time_missing"}
    assert p["call_time_missing"] is False
    assert p["winner"] == "Abigail Spanberger" and p["ap_call_time"].endswith("-05:00") and p["ap_url"] == AP_URL
    assert p["search_request_id"] == fake_jevlin.search["response"]["request_id"] and len(p["quote"]) <= 300
    datetime.fromisoformat(p["found_at"])
    assert winner_is_a_listed_name(p, races)
    assert not list(config.proposals_path().parent.glob("*.tmp"))  # atomic write left nothing behind


def winner_is_a_listed_name(p, races):
    return p["winner"] in {c["name"] for c in races[p["race_id"]]["candidates"]}


def test_race_names_come_from_the_shared_loader(races):
    from detector.races import display_name

    assert races["governor-va-2025"]["name"] == display_name(races["governor-va-2025"]) == "Virginia governor"


def test_only_valid_proposals_reach_proposals_json_and_every_outcome_is_logged(store, races, fake_jevlin, va_state):
    run_once(make_finder(store, races, fake_jevlin, {**GOOD, "winner": "Glenn Youngkin"}), va_state, now=NOW)
    assert json.loads(config.proposals_path().read_text()) == {}
    [line] = config.call_finder_log_path().read_text().splitlines()
    assert json.loads(line)["status"] == "rejected"


def test_the_confirm_tool_reads_the_file(store, races, fake_jevlin, va_state, capsys):
    from confirm import confirm

    run_once(make_finder(store, races, fake_jevlin, GOOD), va_state, now=NOW)
    proposals = json.loads(config.proposals_path().read_text())
    confirm.cmd_show(races["governor-va-2025"], {}, proposals)
    out = capsys.readouterr().out
    assert "Agent proposal (NOT a confirmation" in out and "winner: Abigail Spanberger" in out


def test_the_model_sees_only_ap_pages_and_the_candidate_list(store, races, fake_jevlin, va_state):
    run_once(make_finder(store, races, fake_jevlin, GOOD), va_state, now=NOW)
    prompt = CountingModel.calls[0][-1].content
    assert "- Abigail Spanberger\n- Winsome Earle-Sears" in prompt
    assert "untrusted" in CountingModel.calls[0][0].content
    urls = [line.split('"')[1] for line in prompt.splitlines() if line.startswith("<page url=")]
    assert urls and all(u.startswith("https://apnews.com/") for u in urls)


def test_rejects_a_winner_who_is_not_a_candidate(store, races, fake_jevlin, va_state):
    [r] = run_once(make_finder(store, races, fake_jevlin, {**GOOD, "winner": "Glenn Youngkin"}), va_state, now=NOW)
    assert r["status"] == "rejected" and "not one of this race's candidates" in r["reasons"][0]
    assert "winner" not in r  # a rejected proposal never carries a winner field


@pytest.mark.parametrize("bad,why", [
    ({"ap_url": "https://apnews.com/article/made-up-page"}, "not one of the pages this search returned"),
    ({"ap_url": "https://example.com/spanberger-wins"}, "not an apnews.com page"),
    ({"quote": "Spanberger won in a landslide, AP said."}, "word for word"),
    ({"quote": "The Associated Press declared"}, "does not name"),
    ({"ap_call_time": "2025-11-04T18:00:00-05:00"}, "before poll close"),
    ({"ap_call_time": "2025-11-06T12:00:00-05:00"}, "in the future"),
    ({"ap_call_time": "7:58pm"}, "not ISO 8601"),
    ({"quote": QUOTE + " " + "x" * 300}, "longer than 300"),
])
def test_rejects_unsupported_details(store, races, fake_jevlin, va_state, bad, why):
    [r] = run_once(make_finder(store, races, fake_jevlin, {**GOOD, **bad}), va_state, now=NOW)
    assert r["status"] == "rejected" and any(why in x for x in r["reasons"]), r["reasons"]


def test_a_missing_call_time_is_still_proposed_and_flagged(store, races, fake_jevlin, va_state):
    [r] = run_once(make_finder(store, races, fake_jevlin, {**GOOD, "ap_call_time": None}), va_state, now=NOW)
    assert r["status"] == "proposed"
    p = json.loads(config.proposals_path().read_text())["governor-va-2025"]
    assert p["ap_call_time"] is None and p["call_time_missing"] is True and p["winner"] == "Abigail Spanberger"


def test_no_winner_from_the_model_is_not_found(store, races, fake_jevlin, va_state):
    [r] = run_once(make_finder(store, races, fake_jevlin, {"winner": None, "ap_call_time": None, "ap_url": None,
                                                          "quote": None}), va_state, now=NOW)
    assert r["status"] == "not_found"


def test_garbage_from_the_model_is_rejected(store, races, fake_jevlin, va_state):
    [r] = run_once(make_finder(store, races, fake_jevlin, "I think Spanberger won."), va_state, now=NOW)
    assert r["status"] == "rejected" and "no JSON" in r["reasons"][0]


def test_no_candidate_on_any_page_skips_the_model(store, races, fake_jevlin, va_state):
    fake_jevlin.search["response"]["merged"] = [{**m, "title": "Weather", "excerpt": "Rain."}
                                                for m in fake_jevlin.search["response"]["merged"]]
    [r] = run_once(make_finder(store, races, fake_jevlin, GOOD), va_state, now=NOW)
    assert r["status"] == "not_found" and CountingModel.calls == []


def test_a_failed_fetch_falls_back_to_the_search_excerpts(store, races, fake_jevlin, va_state):
    [r] = run_once(make_finder(store, races, fake_jevlin, GOOD, fetch=True), va_state, now=NOW)
    assert fake_jevlin.paths == ["/v1/search", "/v1/fetch"] and r["status"] == "proposed"


def test_it_never_writes_confirmed_json(store, races, fake_jevlin, va_state, isolated, monkeypatch):
    monkeypatch.chdir(isolated)
    run_once(make_finder(store, races, fake_jevlin, GOOD), va_state, now=NOW)
    assert not list(isolated.rglob("confirmed.json"))
    assert not (config.ROOT / "state" / "confirmed.json").exists() or \
        "proposal" not in (config.ROOT / "state" / "confirmed.json").read_text()


def test_called_and_open_races_are_skipped(va_state):
    va_state["races"][0]["status"] = "open"
    assert pending_races(va_state, NOW) == []
    assert pending_races(va_state, datetime(2025, 11, 4, 23, 30, tzinfo=timezone.utc), ["governor-va-2025"]) == []
    va_state["races"][0].update(status="called", call={"winner": "x", "ap_url": "u"})
    assert pending_races(va_state, NOW, ["governor-va-2025"]) == []


def test_a_waiting_proposal_is_not_searched_again(store, races, fake_jevlin, va_state):
    finder = make_finder(store, races, fake_jevlin, GOOD)
    run_once(finder, va_state, now=NOW)
    assert run_once(finder, va_state, now=NOW) == []
    assert fake_jevlin.paths == ["/v1/search"]


def test_a_later_miss_does_not_remove_a_proposal_and_found_at_is_kept(isolated):
    path = isolated / "p.json"
    first = {"race_id": "x", "status": "proposed", "winner": "A", "ap_url": "u", "found_at": "t1"}
    write_proposals(path, [first])
    write_proposals(path, [{"race_id": "x", "status": "not_found"}, {**first, "found_at": "t2"}])
    assert json.loads(path.read_text()) == {"x": {"race_id": "x", "winner": "A", "ap_call_time": None, "ap_url": "u",
                                                  "quote": None, "found_at": "t1", "search_request_id": None,
                                                  "call_time_missing": None}}


def test_jevlin_cap_stops_searching(store, races, fake_jevlin, va_state):
    store.record_spend("cf_test", "jevlin", "call_finder.search", {}, 1.0)
    [r] = run_once(make_finder(store, races, fake_jevlin, GOOD, jevlin_cap_usd=1.0), va_state, now=NOW)
    assert r["status"] == "skipped" and fake_jevlin.requests == []


def test_jevlin_errors_are_recorded_not_raised(store, races, va_state):
    finder = make_finder(store, races, lambda req: httpx.Response(402, json={"code": "spend_ceiling"}), GOOD)
    [r] = run_once(finder, va_state, now=NOW)
    assert r["status"] == "error" and "402" in r["reasons"][0]


def test_validate_matches_names_loosely_but_maps_to_the_listed_name(races):
    pages = [{"url": AP_URL, "title": "", "text": QUOTE}]
    p, why = validate({**GOOD, "winner": "  abigail   SPANBERGER "}, races["governor-va-2025"], pages,
                      poll_close="2025-11-04T19:00:00-05:00", now=NOW)
    assert why == [] and p["winner"] == "Abigail Spanberger"


def test_a_restarted_call_finder_continues_the_day_budget(races, va_state):
    from agent.jevlin import budget_since
    from agent.store import Store

    since = budget_since("day", None)
    first = FakeJevlin()
    finder = make_finder(Store(), races, first, GOOD, jevlin_cap_usd=0.02, budget_since=since)
    [r] = run_once(finder, va_state, now=NOW)
    assert r["status"] == "proposed" and len(first.requests) == 1  # $0.022 spent

    restarted = FakeJevlin()  # new process, new run id, same ledger
    finder = make_finder(Store(), races, restarted, GOOD, jevlin_cap_usd=0.02, budget_since=since)
    finder.run_id = "cf_restarted"
    [r] = run_once(finder, va_state, now=NOW, refresh=True)
    assert r["status"] == "skipped" and "since" in r["reasons"][0] and restarted.requests == []


def test_rehearsal_overrides_hosts_query_and_prompt(monkeypatch, store, races):
    from agent import call_finder as cf
    assert cf.call_hosts() == ("apnews.com",)
    monkeypatch.setenv("MIDTERMS_CALL_HOSTS", "espn.com, nfl.com")
    monkeypatch.setenv("MIDTERMS_CALL_QUERY", "{name} final score")
    monkeypatch.setenv("MIDTERMS_CALL_PROMPT", "call_finder_nfl.txt")
    assert cf.call_hosts() == ("espn.com", "nfl.com")
    assert cf.is_ap("https://www.espn.com/nfl/game/_/gameId/1") and not cf.is_ap("https://apnews.com/x")
    finder = CallFinder(client=None, model=None, budget=None, store=store, races=races, run_id="t")
    race = next(iter(races.values()))
    body = finder.search_body(race)
    assert body["domain_filter"] == ["espn.com", "nfl.com"]
    assert body["query"].endswith("final score") and "AP" not in body["query"]
    assert "NFL game" in finder.prompt
