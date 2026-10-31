"""Spend tracking and hard caps for billed model calls.

Adapted from agentville (hammadtq/agentville), src/agentville/budget.py.

Every model response's token usage is priced and written to the `spend` table. Before each call the
guard checks three caps: this run (MIDTERMS_RUN_CAP_USD), today since 00:00 UTC
(MIDTERMS_DAILY_CAP_USD) and all runs ever (MIDTERMS_TOTAL_CAP_USD). Over any cap the call is
refused with BudgetExceeded; nothing is sent. A billed model needs all three caps set: there is no
default spend.

Prices are USD per million tokens (Anthropic's table, as given for this project on 1 Nov 2026).
An unknown model raises rather than being guessed. Cache writes cost 1.25x input (5-minute TTL) or
2x (1-hour). Haiku 5.5's cache-read price was not in the table we were given, so its cache reads
are priced at the full input rate: an overestimate, never an underestimate.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from agent import config
from agent.config import ConfigError
from agent.store import Store


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float | None  # None: unknown, priced at the input rate

    @property
    def cache_read_rate(self) -> float:
        return self.input if self.cache_read is None else self.cache_read

    @property
    def cache_write(self) -> float:
        return self.input * 1.25


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(0.10, 0.50, None),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20),
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "offline": Price(0.0, 0.0, 0.0),
}


class BudgetExceeded(Exception):
    """A cap would be exceeded; the model call was not made."""


def bare(model: str) -> str:
    return model.split(":", 1)[-1]


def price_for(model: str) -> Price:
    name = bare(model)
    if name not in PRICES:
        raise ConfigError(f"no price for model {name!r}; add it to agent.budget.PRICES (with its published "
                          "price) before spending")
    return PRICES[name]


def cost_of(usage: dict[str, Any], price: Price) -> tuple[float, dict[str, int]]:
    """LangChain usage_metadata -> (USD, token breakdown). input_tokens includes cache reads and writes."""
    details = usage.get("input_token_details") or {}
    cache_read = int(details.get("cache_read") or 0)
    write_1h = int(details.get("ephemeral_1h_input_tokens") or 0)
    write_5m = int(details.get("ephemeral_5m_input_tokens") or 0) or int(details.get("cache_creation") or 0)
    cache_write = write_5m + write_1h
    total_in = int(usage.get("input_tokens") or 0)
    fresh_in = max(total_in - cache_read - cache_write, 0)
    out = int(usage.get("output_tokens") or 0)
    usd = (fresh_in * price.input + cache_read * price.cache_read_rate + write_5m * price.cache_write
           + write_1h * price.input * 2 + out * price.output) / 1e6
    return usd, {"input": fresh_in, "cache_read": cache_read, "cache_write": cache_write, "output": out}


class Budget:
    def __init__(self, store: Store, model: str, *, run_cap_usd: float, daily_cap_usd: float,
                 total_cap_usd: float):
        self.store = store
        self.model = model
        self.price = price_for(model)  # an unpriced model fails here, before any call
        self.run_cap_usd = run_cap_usd
        self.daily_cap_usd = daily_cap_usd
        self.total_cap_usd = total_cap_usd

    @classmethod
    def from_env(cls, store: Store, model: str) -> "Budget":
        caps = {k: config.float_env(k) for k in
                ("MIDTERMS_RUN_CAP_USD", "MIDTERMS_DAILY_CAP_USD", "MIDTERMS_TOTAL_CAP_USD")}
        missing = [k for k, v in caps.items() if v is None]
        if missing and bare(model) != "offline":
            raise ConfigError(f"model {model!r} is billed; set {', '.join(missing)} first (US dollars)")
        return cls(store, model, run_cap_usd=caps["MIDTERMS_RUN_CAP_USD"] or 0.0,
                   daily_cap_usd=caps["MIDTERMS_DAILY_CAP_USD"] or 0.0,
                   total_cap_usd=caps["MIDTERMS_TOTAL_CAP_USD"] or 0.0)

    def today(self) -> float:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return self.store.spend_since(midnight)

    def check(self, run_id: str) -> None:
        if bare(self.model) == "offline":
            return
        run_spent = self.store.spend_for_run(run_id)
        if run_spent >= self.run_cap_usd:
            raise BudgetExceeded(f"run {run_id} has spent ${run_spent:.4f}, its cap is ${self.run_cap_usd:.2f}")
        if (day := self.today()) >= self.daily_cap_usd:
            raise BudgetExceeded(f"today's spend ${day:.4f} has reached the daily cap of ${self.daily_cap_usd:.2f}")
        if (total := self.store.total_spend()) >= self.total_cap_usd:
            raise BudgetExceeded(f"total spend ${total:.4f} has reached the cap of ${self.total_cap_usd:.2f}")

    def record(self, run_id: str, message: AIMessage, step: str) -> float:
        """Priced by the model that answered (a refusal fallback can switch models); the run's model if
        the response doesn't say or names an unpriced model."""
        usage = getattr(message, "usage_metadata", None) or {}
        answered = (getattr(message, "response_metadata", None) or {}).get("model_name")
        model = answered if answered in PRICES else bare(self.model)
        usd, tokens = cost_of(usage, price_for(model))
        self.store.record_spend(run_id, model, step, tokens, usd)
        return usd


class BudgetMiddleware(AgentMiddleware):
    """Checks the caps before every model call in the agent loop and records spend after it."""

    def __init__(self, budget: Budget, step: str = "agent"):
        super().__init__()
        self.budget = budget
        self.step = step

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self.budget.check(runtime.context.run_id)
        return None

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        last = state["messages"][-1] if state.get("messages") else None
        if isinstance(last, AIMessage):
            self.budget.record(runtime.context.run_id, last, self.step)
        return None
