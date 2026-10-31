"""Draft one live post for a called race: the agent writes it, the guard checks it, and a failing
draft is replaced by the fixed template (which always passes). The result is queued; a person
publishes it with `midterms publish`. The agent has no tool that posts or writes the queue."""

from pathlib import Path

from agent.drafts import Drafts
from agent.guard import check_post, template_post
from agent.models import PlannedCall
from agent.runtime import build_agent
from agent.state import is_called, race_by_id, state_tools

PROMPT = (Path(__file__).parent / "prompts" / "draft_post.txt").read_text()


def draft_for_race(race_id: str, state: dict, drafts: Drafts, *, model=None, final_text: str | None = None,
                   budget=None, store=None) -> dict:
    race = race_by_id(state, race_id)
    if race is None:
        raise ValueError(f"no race {race_id!r}")
    if not is_called(race):
        raise ValueError(f"{race_id} is not called: posts are drafted only after a person has confirmed AP's call")
    agent = build_agent(state_tools(lambda: state, names=("get_race",)), PROMPT, model=model,
                        plan=[PlannedCall("get_race", {"race_id": race_id})], final_text=final_text,
                        budget=budget, store=store or drafts.store, max_calls=4)
    res = agent.run(f"Draft the post for race {race_id}.")
    text = (res.output or "").strip().strip('"').strip()
    agent_reasons: list[str] = []
    if res.status != "succeeded":
        agent_reasons = [f"agent {res.status}: {res.error}"]
    elif text:
        ok, agent_reasons = check_post(text, race_id, state)
        if ok:
            return {**drafts.add(race_id, text, state, source="agent"), "agent_reasons": []}
    else:
        agent_reasons = ["agent returned no text"]
    d = drafts.add(race_id, template_post(race_id, state), state, source="template")
    return {**d, "agent_reasons": agent_reasons}
