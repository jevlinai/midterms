"""The agent runtime, `ask`, and `draft-post`, with the offline model."""

import pytest

from agent import cli
from agent.config import ConfigError
from agent.drafts import Drafts
from agent.guard import template_post
from agent.models import OfflinePlanModel, PlannedCall, parse_json_object
from agent.post_draft import draft_for_race
from agent.runtime import build_agent
from agent.state import state_tools
from conftest import uncalled


def test_model_is_required(monkeypatch):
    with pytest.raises(ConfigError, match="MIDTERMS_MODEL is not set"):
        build_agent(state_tools(), "sys")


def test_a_billed_model_needs_an_api_key(monkeypatch):
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        build_agent(state_tools(), "sys", model="claude-haiku-5-5")


def test_offline_agent_runs_the_plan(state):
    agent = build_agent(state_tools(lambda: state), "sys", model="offline",
                        plan=[PlannedCall("leaderboard", {"office": "senate"}), PlannedCall("get_race", {"race_id": "senate-al"})])
    res = agent.run("which engine was fastest?")
    assert res.status == "succeeded"
    assert "leaderboard" in res.output and "get_race" in res.output


def test_model_call_limit(state):
    plan = [PlannedCall("leaderboard", {})] * 10
    res = build_agent(state_tools(lambda: state), "sys", model="offline", plan=plan, max_calls=3).run("q")
    assert res.status == "limit_reached"


def test_ask_cli_offline(monkeypatch, capsys):
    monkeypatch.setenv("MIDTERMS_MODEL", "offline")
    assert cli.main(["ask", "Which engine found governor races first?"]) == 0
    assert "offline model" in capsys.readouterr().out


def test_ask_withholds_an_answer_naming_an_uncalled_candidate(monkeypatch, capsys, state):
    name = uncalled(state)["candidates"][0]["name"]
    monkeypatch.setenv("MIDTERMS_MODEL", "offline")
    real = OfflinePlanModel

    class Leaky(real):
        final_text: str | None = f"{name} is winning."

    monkeypatch.setattr("agent.runtime.build_model", lambda *a, **k: Leaky(plan=[]))
    assert cli.main(["ask", "who is winning?"]) == 1
    captured = capsys.readouterr()
    assert name not in captured.out and "withheld" in captured.err


def test_draft_post_uses_a_good_agent_draft(led_race, state, store):
    good = template_post(led_race["id"], state).replace("First search engine to find the result", "First to find it")
    d = draft_for_race(led_race["id"], state, Drafts(store=store), model="offline", final_text=good)
    assert d["source"] == "agent" and d["guard"]["ok"] and d["text"] == good


def test_draft_post_falls_back_to_the_template(led_race, state, store):
    bad = f"Huge night: {led_race['call']['winner']} wins in a landslide!"
    d = draft_for_race(led_race["id"], state, Drafts(store=store), model="offline", final_text=bad)
    assert d["source"] == "template" and d["guard"]["ok"]
    assert d["text"] == template_post(led_race["id"], state)
    assert any("landslide" in r for r in d["agent_reasons"])


def test_draft_post_refuses_an_uncalled_race(state, store):
    with pytest.raises(ValueError, match="not called"):
        draft_for_race(uncalled(state)["id"], state, Drafts(store=store), model="offline")


def test_publish_cli_refuses_without_by(led_race, state, capsys):
    d = Drafts().add(led_race["id"], template_post(led_race["id"], state), state)
    assert cli.main(["publish", d["id"]]) == 2
    assert "--by" in capsys.readouterr().err
    assert cli.main(["publish", d["id"], "--by", "hammad"]) == 0  # dry run: no live switch
    assert "dry run" in capsys.readouterr().out


def test_parse_json_object_handles_fences_and_braces():
    assert parse_json_object('```json\n{"a": "x}y", "b": {"c": 1}}\n```') == {"a": "x}y", "b": {"c": 1}}
    with pytest.raises(ValueError):
        parse_json_object("no json here")
