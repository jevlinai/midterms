"""Chat model factory.

Adapted from agentville (hammadtq/agentville), src/agentville/runtime/models.py
(`build_model`, `OfflinePlanModel`).

"offline": a deterministic stand-in that calls a fixed list of tools, then answers with either a
fixed text (`final_text`) or a plain report of the tool results. No network, no cost. It exists so
the agent loop, budget and guard can be tested without an API key. It does not reason.

A Claude model id ("claude-haiku-5-5", or "anthropic:claude-haiku-5-5"): Claude through
langchain-anthropic. Notes for the current models: thinking cannot be disabled on Opus 5.5 /
Sonnet 5.5, so depth is set with `effort` (MIDTERMS_EFFORT, default "low": these are short,
bounded tasks); `temperature` is not sent (rejected); forced tool_choice is rejected, so it is
never set. Opus 5.5 and Sonnet 5.5 get the server-side refusal fallback ("fallbacks": "default");
Haiku 5.5 has none.
"""

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from agent.budget import bare, price_for
from agent.config import ConfigError

EFFORTS = ("low", "medium", "high", "xhigh", "max")
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")


@dataclass
class PlannedCall:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


class OfflinePlanModel(BaseChatModel):
    """Calls the planned tools one at a time, then answers."""

    plan: list[PlannedCall] = []
    final_text: str | None = None
    _bound: list[str] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "midterms-offline"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "OfflinePlanModel":
        self._bound = [getattr(t, "name", None) or t.get("name") for t in tools]
        return self

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
        turn = messages[last_human + 1:]
        step = sum(1 for m in turn if isinstance(m, AIMessage) and m.tool_calls)
        if step < len(self.plan):
            call = self.plan[step]
            if self._bound and call.tool not in self._bound:
                raise ConfigError(f"planned tool {call.tool!r} is not bound; have {self._bound}")
            msg = AIMessage(content="", tool_calls=[{"name": call.tool, "args": call.args, "id": f"offline_{step}"}],
                            usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        else:
            text = self.final_text if self.final_text is not None else \
                _report([m for m in turn if isinstance(m, ToolMessage)])
            msg = AIMessage(content=text, usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _report(results: list[ToolMessage]) -> str:
    lines = ["[offline model: no LLM was called; this text is assembled from tool results]"]
    for r in results:
        if r.status == "error":
            lines.append(f"- {r.name} failed: {r.content}")
        else:
            lines.append(f"- {r.name}: {str(r.content)[:400]}")
    return "\n".join(lines)


def build_model(model: str, *, plan: list[PlannedCall] | None = None, final_text: str | None = None,
                max_tokens: int = 4000, effort: str | None = None) -> BaseChatModel:
    if model == "offline":
        return OfflinePlanModel(plan=plan or [], final_text=final_text)
    provider, _, name = model.partition(":") if ":" in model else ("anthropic", "", model)
    if provider != "anthropic" or not name.startswith("claude-"):
        raise ConfigError(f"unsupported model {model!r}; use 'offline' or a Claude model id such as claude-haiku-5-5")
    price_for(name)  # unpriced models are refused before any call
    effort = effort or os.environ.get("MIDTERMS_EFFORT", "low")
    if effort not in EFFORTS:
        raise ConfigError(f"MIDTERMS_EFFORT must be one of {', '.join(EFFORTS)}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ConfigError("ANTHROPIC_API_KEY is not set (use MIDTERMS_MODEL=offline for a free run)")
    from langchain_anthropic import ChatAnthropic

    kwargs: dict[str, Any] = {}
    model_kwargs: dict[str, Any] = {}
    if bare(name) in FALLBACK_MODELS:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        model_kwargs["fallbacks"] = "default"
    return ChatAnthropic(model=name, max_tokens=max_tokens, output_config={"effort": effort},
                         model_kwargs=model_kwargs, **kwargs)


def message_text(msg: Any) -> str:
    """The text of a model reply, whether content is a string or a list of blocks."""
    if msg is None:
        return ""
    t = getattr(msg, "text", None)
    if isinstance(t, str):
        return t
    c = getattr(msg, "content", "")
    if isinstance(c, str):
        return c
    return "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")


def parse_json_object(text: str) -> dict:
    """The first JSON object in a reply (models sometimes wrap it in a code fence)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
                elif ch != "\\":
                    esc = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        pass
                    break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object in the model's reply")
