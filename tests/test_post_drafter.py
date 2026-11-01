"""post-drafter: drafts as races get called, posting set only, no double drafting, retractions, hourly cap."""

import copy
from datetime import datetime, timedelta, timezone

import pytest

from agent import cli, config
from agent.drafts import Drafts, NotAuthorized
from agent.post_drafter import posting_set, run_pass
from agent.store import Store

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)


def races_for(state, toss_up, lean):
    """The race list as data/races.json would give it, for the synthetic state."""
    out = {r["id"]: {"id": r["id"], "office": r["office"]} for r in state["races"]}
    out[toss_up]["cook_rating"] = "toss_up_r"
    out[lean]["cook_rating"] = "lean_d"
    return out


@pytest.fixture
def setup(state):
    called_house = [r["id"] for r in state["races"] if r["office"] == "house" and r["status"] == "called"]
    toss_up, lean = called_house[0], called_house[1]
    return state, races_for(state, toss_up, lean), toss_up, lean


def run(state, races, *, store=None, per_hour=1000, now=NOW, final_text=None, lines=None):
    store = store or Store()
    drafts = Drafts(store=store)
    stats = run_pass(state, drafts, store, races=races, model="offline", final_text=final_text, per_hour=per_hour,
                     now=now, log=(lines.append if lines is not None else lambda *a: None))
    return stats, drafts, store


def test_posting_set():
    races = {"senate-a": {"office": "senate"}, "governor-b": {"office": "governor"},
             "house-c": {"office": "house", "cook_rating": "toss_up_d"}, "house-d": {"office": "house", "cook_rating": "lean_r"},
             "house-e": {"office": "house"}}
    assert posting_set(races) == {"senate-a", "governor-b", "house-c"}


def test_drafts_only_called_races_in_the_posting_set(setup):
    state, races, toss_up, lean = setup
    lines = []
    stats, drafts, _ = run(state, races, lines=lines)
    drafted = {d["race_id"] for d in drafts.list()}
    want = {r["id"] for r in state["races"] if r["status"] == "called"
            and (r["office"] in ("senate", "governor") or r["id"] == toss_up)}
    assert drafted == want and stats["drafted"] == len(want)
    assert all(d["guard"]["ok"] for d in drafts.list())
    assert len(lines) == len(want) and all(line.startswith("drafted ") for line in lines)


def test_a_house_race_that_is_not_a_toss_up_is_skipped(setup):
    state, races, toss_up, lean = setup
    _, drafts, _ = run(state, races)
    ids = {d["race_id"] for d in drafts.list()}
    assert toss_up in ids and lean not in ids


def test_no_double_drafting_after_a_restart(setup):
    state, races, *_ = setup
    first, drafts, store = run(state, races)
    n = len(drafts.list())
    store.close()
    restarted = Store()  # same state/agent.sqlite
    # even if the agent would now write different text, nothing is drafted again
    stats, drafts2, _ = run(state, races, store=restarted, final_text="Different wording. Live: jevlin.ai/midterms")
    assert stats["drafted"] == 0 and len(drafts2.list()) == n


def test_a_draft_made_by_hand_is_not_drafted_again(setup):
    state, races, *_ = setup
    rid = next(r["id"] for r in state["races"] if r["office"] == "senate" and r["status"] == "called")
    from agent.post_draft import draft_for_race

    store = Store()
    draft_for_race(rid, state, Drafts(store=store), model="offline", store=store)
    _, drafts, _ = run(state, races, store=store)
    assert len([d for d in drafts.list() if d["race_id"] == rid]) == 1


def test_retraction_marks_the_draft_stale_and_publish_refuses_it(setup, x_client, fake_x):
    state, races, *_ = setup
    rid = next(r["id"] for r in state["races"] if r["office"] == "governor" and r["status"] == "called")
    _, drafts, store = run(state, races)
    [d] = [d for d in drafts.list() if d["race_id"] == rid]

    retracted = copy.deepcopy(state)
    r = next(r for r in retracted["races"] if r["id"] == rid)
    r.update(status="closed", call=None)
    lines = []
    stats, drafts, _ = run(retracted, races, store=store, lines=lines)
    assert stats["stale"] == 1 and stats["drafted"] == 0
    assert drafts.get(d["id"])["status"] == "stale" and "retracted" in drafts.get(d["id"])["stale"]["reason"]
    assert any(line.startswith(f"stale {d['id']}") for line in lines)
    with pytest.raises(NotAuthorized, match="stale"):
        drafts.publish(d["id"], by="hammad", state=retracted, live=True, client=x_client)
    with pytest.raises(NotAuthorized, match="stale"):  # even against a state where the race is called again
        drafts.publish(d["id"], by="hammad", state=state, live=True, client=x_client)
    assert fake_x.requests == []

    # a new, different call later: drafted again, and only for the new call
    recalled = copy.deepcopy(state)
    r = next(r for r in recalled["races"] if r["id"] == rid)
    r["call"]["ap_url"] = "https://example.com/synthetic-ap-call-2"
    stats, drafts, _ = run(recalled, races, store=store)
    assert stats["drafted"] == 1
    new = [x for x in drafts.list() if x["race_id"] == rid and x["status"] == "queued"]
    assert len(new) == 1 and "synthetic-ap-call-2" in new[0]["text"]
    assert run(recalled, races, store=store)[0]["drafted"] == 0


def test_a_changed_call_makes_the_old_draft_stale(setup):
    state, races, *_ = setup
    rid = next(r["id"] for r in state["races"] if r["office"] == "senate" and r["status"] == "called")
    _, drafts, store = run(state, races)
    [old] = [d for d in drafts.list() if d["race_id"] == rid]
    changed = copy.deepcopy(state)
    next(r for r in changed["races"] if r["id"] == rid)["call"]["ap_url"] = "https://example.com/corrected"
    stats, drafts, _ = run(changed, races, store=store)
    assert drafts.get(old["id"])["status"] == "stale" and stats["drafted"] == 1


def test_hourly_cap(setup):
    state, races, *_ = setup
    lines = []
    stats, drafts, store = run(state, races, per_hour=3, lines=lines)
    assert stats["drafted"] == 3 and stats["capped"] > 0 and len(drafts.list()) == 3
    assert lines[-1].startswith("cap reached: 3 drafts in the last hour")
    # the next pass in the same hour drafts nothing
    assert run(state, races, store=store, per_hour=3, now=NOW + timedelta(minutes=30))[0]["drafted"] == 0
    # rolling: the drafts were recorded at the real time; an hour after that, 3 more
    later = datetime.now(timezone.utc) + timedelta(minutes=61)
    assert run(state, races, store=store, per_hour=3, now=later)[0]["drafted"] == 3


def test_the_cap_comes_from_the_environment(setup, monkeypatch):
    state, races, *_ = setup
    monkeypatch.setenv("MIDTERMS_DRAFTS_PER_HOUR", "2")
    store = Store()
    stats = run_pass(state, Drafts(store=store), store, races=races, model="offline", now=NOW, log=lambda *a: None)
    assert stats["drafted"] == 2


def test_the_cli_needs_the_three_llm_caps(monkeypatch, capsys):
    monkeypatch.setenv("MIDTERMS_MODEL", "claude-sonnet-5-5")
    assert cli.main(["post-drafter", "--once"]) == 2
    assert "MIDTERMS_RUN_CAP_USD" in capsys.readouterr().err
    assert not config.drafts_dir().exists() or not list(config.drafts_dir().glob("*.json"))
