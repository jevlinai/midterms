"""Spend accounting and hard caps (style of agentville's tests/test_budget.py)."""

import pytest
from langchain_core.messages import AIMessage

from agent.budget import PRICES, Budget, BudgetExceeded, cost_of, price_for
from agent.config import ConfigError
from agent.models import OfflinePlanModel, PlannedCall, build_model
from agent.runtime import build_agent
from agent.state import state_tools


def msg(out_tokens, model=None):
    return AIMessage("x", usage_metadata={"input_tokens": 0, "output_tokens": out_tokens, "total_tokens": out_tokens},
                     response_metadata={"model_name": model} if model else {})


def test_prices_per_million_tokens():
    assert PRICES["claude-haiku-5-5"].input == 0.10 and PRICES["claude-haiku-5-5"].output == 0.50
    assert (PRICES["claude-sonnet-5-5"].input, PRICES["claude-sonnet-5-5"].output, PRICES["claude-sonnet-5-5"].cache_read) \
        == (2.0, 10.0, 0.20)
    assert (PRICES["claude-opus-5-5"].input, PRICES["claude-opus-5-5"].output, PRICES["claude-opus-5-5"].cache_read) \
        == (4.0, 20.0, 0.20)


def test_cost_counts_cache_reads_and_writes():
    usage = {"input_tokens": 15256, "output_tokens": 779,
             "input_token_details": {"cache_read": 3910, "ephemeral_5m_input_tokens": 11344}}
    usd, t = cost_of(usage, PRICES["claude-opus-5-5"])
    assert t == {"input": 2, "cache_read": 3910, "cache_write": 11344, "output": 779}
    assert usd == pytest.approx((2 * 4 + 3910 * 0.2 + 11344 * 5 + 779 * 20) / 1e6)


def test_haiku_cache_reads_are_priced_at_input_until_published():
    usd, _ = cost_of({"input_tokens": 1000, "output_tokens": 0, "input_token_details": {"cache_read": 1000}},
                     PRICES["claude-haiku-5-5"])
    assert usd == pytest.approx(1000 * 0.10 / 1e6)


@pytest.mark.parametrize("m", ["claude-some-new-model", "anthropic:claude-unknown", "gpt-5"])
def test_unknown_model_has_no_price(m):
    with pytest.raises(ConfigError, match="no price"):
        price_for(m)


def test_unknown_model_is_refused_before_any_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-used")
    with pytest.raises(ConfigError, match="no price"):
        build_model("claude-some-new-model")


def test_run_cap(store):
    b = Budget(store, "claude-opus-5-5", run_cap_usd=1.0, daily_cap_usd=100, total_cap_usd=100)
    assert b.record("r1", msg(45_000), "agent") == pytest.approx(0.9)
    b.check("r1")
    b.record("r1", msg(45_000), "agent")
    with pytest.raises(BudgetExceeded, match="run r1"):
        b.check("r1")
    b.check("r2")


def test_daily_cap(store):
    b = Budget(store, "claude-sonnet-5-5", run_cap_usd=100, daily_cap_usd=0.5, total_cap_usd=100)
    b.record("r1", msg(60_000), "agent")  # $0.60
    with pytest.raises(BudgetExceeded, match="daily cap"):
        b.check("r2")


def test_total_cap(store):
    b = Budget(store, "claude-sonnet-5-5", run_cap_usd=100, daily_cap_usd=100, total_cap_usd=1.0)
    store.record_spend("earlier", "claude-sonnet-5-5", "agent", {}, 1.0)
    with pytest.raises(BudgetExceeded, match="total spend"):
        b.check("r3")


def test_jevlin_spend_does_not_count_toward_llm_caps(store):
    b = Budget(store, "claude-haiku-5-5", run_cap_usd=0.01, daily_cap_usd=0.01, total_cap_usd=0.01)
    store.record_spend("r1", "jevlin", "call_finder.search", {}, 5.0)
    b.check("r1")
    assert store.jevlin_spend_for_run("r1") == 5.0


def test_spend_is_priced_by_the_model_that_answered(store):
    b = Budget(store, "claude-opus-5-5", run_cap_usd=4, daily_cap_usd=4, total_cap_usd=4)
    assert b.record("r1", msg(10_000, "claude-sonnet-5-5"), "agent") == pytest.approx(0.10)
    assert b.record("r1", msg(10_000, "claude-unknown"), "agent") == pytest.approx(0.20)


def test_a_billed_model_needs_all_three_caps(store, monkeypatch):
    with pytest.raises(ConfigError, match="MIDTERMS_RUN_CAP_USD"):
        Budget.from_env(store, "claude-haiku-5-5")
    for k in ("MIDTERMS_RUN_CAP_USD", "MIDTERMS_DAILY_CAP_USD", "MIDTERMS_TOTAL_CAP_USD"):
        monkeypatch.setenv(k, "1")
    assert Budget.from_env(store, "claude-haiku-5-5").total_cap_usd == 1.0
    assert Budget.from_env(store, "offline")  # free: no caps needed


def test_agent_stops_before_the_call_that_would_exceed(store, state):
    calls = []

    class CountingModel(OfflinePlanModel):
        def _generate(self, messages, stop=None, run_manager=None, **kw):
            calls.append(1)
            return super()._generate(messages, stop, run_manager, **kw)

    budget = Budget(store, "claude-haiku-5-5", run_cap_usd=100, daily_cap_usd=100, total_cap_usd=20)
    store.record_spend("earlier", "claude-haiku-5-5", "agent", {}, 20.0)
    agent = build_agent(state_tools(lambda: state), "sys", model=CountingModel(plan=[PlannedCall("leaderboard", {})]),
                        budget=budget)
    res = agent.run("q")
    assert res.status == "over_budget" and "total spend" in res.error and calls == []
