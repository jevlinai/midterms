"""The draft queue and the human publish gate, against a fake X API (httpx.MockTransport)."""

import httpx
import pytest

from agent.drafts import Drafts, NotAuthorized, attempt_key
from agent.guard import template_post
from conftest import mutate


@pytest.fixture
def drafts(store):
    return Drafts(store=store)


@pytest.fixture
def queued(drafts, led_race, state):
    return drafts.add(led_race["id"], template_post(led_race["id"], state), state, source="template")


def test_add_queues_with_the_guard_result(drafts, queued, led_race):
    assert queued["status"] == "queued" and queued["guard"] == {**queued["guard"], "ok": True, "reasons": []}
    assert queued["id"].startswith(led_race["id"] + "-")
    assert [d["id"] for d in drafts.list()] == [queued["id"]]


def test_the_same_text_twice_is_one_draft(drafts, queued, led_race, state):
    again = drafts.add(led_race["id"], queued["text"] + "\n", state)
    assert again["id"] == queued["id"] and len(drafts.list()) == 1


def test_publish_refuses_without_by(drafts, queued, state, x_client, fake_x):
    for by in (None, "", "   "):
        with pytest.raises(NotAuthorized, match="--by"):
            drafts.publish(queued["id"], by=by, state=state, live=True, client=x_client)
    assert fake_x.requests == []


def test_publish_without_the_live_switch_is_a_dry_run(drafts, queued, state, x_client, fake_x):
    out = drafts.publish(queued["id"], by="hammad", state=state, client=x_client)  # env var unset
    assert out.status == "dry_run" and "MIDTERMS_PUBLISH_LIVE" in out.detail
    assert out.plan["text"] == queued["text"] and out.plan["live_posting_enabled"] is False
    assert fake_x.requests == []
    assert drafts.get(queued["id"])["status"] == "queued"


def test_the_live_switch_must_be_exactly_true(drafts, queued, state, x_client, fake_x, monkeypatch):
    monkeypatch.setenv("MIDTERMS_PUBLISH_LIVE", "yes")
    assert drafts.publish(queued["id"], by="hammad", state=state, client=x_client).status == "dry_run"
    monkeypatch.setenv("MIDTERMS_PUBLISH_LIVE", "true")
    assert drafts.publish(queued["id"], by="hammad", state=state, client=x_client).status == "posted"
    assert len(fake_x.requests) == 1


def test_publish_posts_and_records(drafts, queued, state, x_client, fake_x, store):
    out = drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    assert out.status == "posted" and out.url == f"https://x.com/jevlinai/status/{out.tweet_id}"
    assert fake_x.bodies == [{"text": queued["text"]}]
    d = drafts.get(queued["id"])
    assert d["status"] == "published" and d["published"]["by"] == "hammad" and d["published"]["tweet_id"] == out.tweet_id
    a = store.attempt(attempt_key(queued["race_id"], queued["text"]))
    assert a["status"] == "posted" and a["tweet_id"] == out.tweet_id and a["authorized_by"] == "hammad"


def test_publish_refuses_when_the_guard_fails_against_the_current_state(drafts, queued, state, x_client, fake_x):
    # The call was withdrawn after the draft was made: the race is no longer called.
    withdrawn = mutate(state, queued["race_id"], lambda r: r.update(status="closed", call=None))
    with pytest.raises(NotAuthorized, match="guard fails"):
        drafts.publish(queued["id"], by="hammad", state=withdrawn, live=True, client=x_client)
    assert fake_x.requests == []
    assert drafts.get(queued["id"])["guard"]["ok"] is False


def test_publish_refuses_a_draft_that_never_passed(drafts, led_race, state, x_client, fake_x):
    d = drafts.add(led_race["id"], "Democrats hold the seat! Live: jevlin.ai/midterms", state)
    assert d["guard"]["ok"] is False
    with pytest.raises(NotAuthorized, match="guard fails"):
        drafts.publish(d["id"], by="hammad", state=state, live=True, client=x_client)
    assert fake_x.requests == []


def test_a_second_publish_of_the_same_draft_is_refused(drafts, queued, state, x_client, fake_x):
    drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    with pytest.raises(NotAuthorized, match="already published"):
        drafts.publish(queued["id"], by="usman", state=state, live=True, client=x_client)
    assert len(fake_x.requests) == 1


def test_the_ledger_stops_a_repost_even_if_the_draft_file_is_reset(drafts, queued, state, x_client, fake_x):
    """A crash after posting but before the draft file was saved, or a hand-edited file: the SQLite
    attempt_key still says it went out."""
    drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    d = drafts.get(queued["id"])
    d.update(status="queued", published=None)
    drafts._save(d)
    with pytest.raises(NotAuthorized, match="already posted"):
        drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    assert len(fake_x.requests) == 1 and drafts.get(queued["id"])["status"] == "published"


def test_an_unknown_outcome_blocks_a_retry_until_a_person_checks(drafts, queued, state, x_client, fake_x):
    fake_x.fail_with = [httpx.ReadTimeout("slow")]
    out = drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    assert out.status == "unknown"
    with pytest.raises(NotAuthorized, match="may have posted"):
        drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    assert len(fake_x.requests) == 1
    out = drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client, retry_unsafe=True)
    assert out.status == "posted" and len(fake_x.requests) == 2


def test_a_definite_refusal_can_be_retried(drafts, queued, state, x_client, fake_x):
    fake_x.fail_with = [403]
    out = drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    assert out.status == "failed" and "403" in out.detail
    assert drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client).status == "posted"


def test_rejected_drafts_cannot_be_published(drafts, queued, state, x_client, fake_x):
    with pytest.raises(NotAuthorized, match="--by"):
        drafts.reject(queued["id"], None)
    drafts.reject(queued["id"], "gaurav", "wording")
    with pytest.raises(NotAuthorized, match="rejected by gaurav"):
        drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    assert fake_x.requests == []


def test_replies_go_out_as_a_thread(drafts, queued, led_race, state, x_client, fake_x):
    child = drafts.add(led_race["id"], queued["text"].replace("Live: jevlin.ai/midterms", "Method: jevlin.ai/midterms"),
                       state, reply_to_draft=queued["id"])
    with pytest.raises(NotAuthorized, match="not published yet"):
        drafts.publish(child["id"], by="hammad", state=state, live=True, client=x_client)
    parent = drafts.publish(queued["id"], by="hammad", state=state, live=True, client=x_client)
    drafts.publish(child["id"], by="hammad", state=state, live=True, client=x_client)
    assert fake_x.bodies[-1]["reply"] == {"in_reply_to_tweet_id": parent.tweet_id}


def test_bad_draft_ids_are_refused(drafts):
    with pytest.raises(NotAuthorized):
        drafts.get("../../etc/passwd")


def test_the_agent_tool_set_has_no_publisher():
    """Nothing the agent can call reaches X: the runtime refuses side-effect tool names, and the only
    tools anywhere in the package are the four read-only state tools."""
    from langchain_core.tools import StructuredTool

    from agent.runtime import build_agent
    from agent.state import state_tools

    names = {t.name for t in state_tools()}
    assert names == {"get_race", "list_races", "leaderboard", "compare"}
    bad = StructuredTool.from_function(func=lambda text: text, name="publish_post", description="x")
    with pytest.raises(ValueError, match="read-only"):
        build_agent([bad], "sys", model="offline")
