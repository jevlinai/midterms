"""The agent runtime: a LangChain agent (a LangGraph graph) over the tools it is given.

Adapted from agentville (hammadtq/agentville), the `create_agent` + middleware pattern in
src/agentville/runtime/agent.py, without checkpoints, approvals or tracing (runs here are short
and stateless).

  create_agent(...)          a "model" node and a "tools" node in a loop
  ModelCallLimitMiddleware   caps model calls per run (exit_behavior="error": a hit limit is a failure)
  BudgetMiddleware           checks the $ caps before every model call, records spend after it

The model comes from MIDTERMS_MODEL, which is required: the model choice is pending with Hammad.
"offline" selects OfflinePlanModel (free, for tests).

The agent only ever gets read-only tools. Nothing here can post, write state or confirm a call.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError

from agent import config
from agent.budget import Budget, BudgetExceeded, BudgetMiddleware
from agent.models import PlannedCall, build_model, message_text
from agent.store import Store

# A tool whose name suggests a side effect is refused outright: the agent has read-only tools only.
FORBIDDEN_TOOL_WORDS = ("publish", "post", "tweet", "send", "confirm", "write", "delete")


@dataclass
class RunContext:
    run_id: str


@dataclass
class RunResult:
    run_id: str
    status: str  # succeeded | failed | limit_reached | over_budget
    output: str | None = None
    error: str | None = None
    messages: list | None = None


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid4().hex[:6]}"


class Agent:
    def __init__(self, graph: Any, model_label: str, max_calls: int):
        self.graph = graph
        self.model_label = model_label
        self.max_calls = max_calls

    def run(self, prompt: str, *, run_id: str | None = None) -> RunResult:
        run_id = run_id or new_run_id()
        # Middleware adds graph steps; the model-call limit is the real cap, this is only a backstop.
        cfg = {"recursion_limit": self.max_calls * 6 + 10, "run_name": "midterms-agent"}
        try:
            out = self.graph.invoke({"messages": [HumanMessage(prompt)]}, cfg, context=RunContext(run_id))
        except (ModelCallLimitExceededError, GraphRecursionError) as e:
            return RunResult(run_id, "limit_reached", error=f"stopped after {self.max_calls} model calls: {e}")
        except BudgetExceeded as e:
            return RunResult(run_id, "over_budget", error=str(e))
        except Exception as e:  # noqa: BLE001 - surfaced to the caller, never swallowed silently
            return RunResult(run_id, "failed", error=f"{type(e).__name__}: {e}")
        messages = out.get("messages", []) if isinstance(out, dict) else getattr(out, "value", {}).get("messages", [])
        final = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        return RunResult(run_id, "succeeded", output=message_text(final).strip() if final else None, messages=messages)


def build_agent(tools: list[BaseTool], system_prompt: str, *, model: str | BaseChatModel | None = None,
                plan: list[PlannedCall] | None = None, final_text: str | None = None, budget: Budget | None = None,
                store: Store | None = None, max_calls: int = 8) -> Agent:
    for t in tools:
        if any(w in t.name.lower() for w in FORBIDDEN_TOOL_WORDS):
            raise ValueError(f"tool {t.name!r} looks like a side effect; the agent gets read-only tools only")
    if isinstance(model, BaseChatModel):
        chat, label = model, getattr(model, "model", None) or "offline"
    else:
        label = model or config.model_name()
        chat = build_model(label, plan=plan, final_text=final_text)
    if budget is None:
        budget = Budget.from_env(store or Store(), label if isinstance(label, str) else "offline")
    middleware: list[Any] = [ModelCallLimitMiddleware(run_limit=max_calls, exit_behavior="error"),
                             BudgetMiddleware(budget)]
    graph = create_agent(chat, tools, system_prompt=system_prompt, middleware=middleware,
                         context_schema=RunContext, name="midterms")
    return Agent(graph, label, max_calls)
