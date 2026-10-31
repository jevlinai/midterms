"""The results thread: the version the leaderboard supports, guard-checked, queued as a reply chain."""

import copy

import pytest

from agent import thread
from agent.config import ConfigError
from agent.drafts import Drafts, NotAuthorized
from agent.guard import check_summary_post, results_outcome

REPO = "https://github.com/jevlinai/midterms"


def with_medians(state, **medians):
    s = copy.deepcopy(state)
    for r in s["leaderboard"]:
        if r["engine"] in medians:
            r["median_minutes_after_close"] = medians[r["engine"]]
    return s


@pytest.fixture
def google_wins(state):
    return with_medians(state, serper=10)


@pytest.fixture
def drafts(store):
    return Drafts(store=store)


def test_example_state_supports_ai_faster_and_every_post_passes(state):
    out = results_outcome(state)
    assert out["version"] == "ai_faster" and out["best"] == "exa"
    posts = thread.compose(state, "ai_faster", REPO)
    assert "AI search was faster than Google." in posts[0] and "Exa: 30 min median from poll close (4 wrong)" in posts[0]
    assert all(ok for ok, _ in thread.check_thread(posts, state, REPO)), thread.check_thread(posts, state, REPO)


def test_google_faster_is_said_plainly(google_wins):
    out = results_outcome(google_wins)
    assert out["version"] == "google_faster"
    posts = thread.compose(google_wins, "google_faster", REPO)
    assert "Google was faster than AI search." in posts[0]
    assert "Google (via Serper): 10 min median from poll close (6 wrong)" in posts[0]
    assert all(ok for ok, _ in thread.check_thread(posts, google_wins, REPO))
    # and the AI headline is refused on this data
    ok, why = check_summary_post(thread.compose(google_wins, "ai_faster", REPO)[0], google_wins, repo_url=REPO)
    assert not ok and "Google was as fast or faster" in " ".join(why)


def test_a_tie_goes_to_google(state):
    s = with_medians(state, exa=35)
    out = results_outcome(s)
    assert out["version"] == "google_faster" and out["tie"]
    assert "Google was as fast as AI search." in thread.compose(s, "google_faster", REPO)[0]


def test_the_agent_alone_beating_google_is_ai_faster_with_the_agent_as_lead(state):
    s = with_medians(state, exa=50, brave=50, **{"parallel-search": 50, "perplexity-search": 50}, tinyfish=50,
                     telem=50, agent=20)
    out = results_outcome(s)
    assert out["version"] == "ai_faster" and out["lead"] == "agent"
    head = thread.compose(s, "ai_faster", REPO)[0]
    assert "Agent on Jevlin: 20 min" in head
    assert check_summary_post(head, s, repo_url=REPO)[0]


def test_jevlin_never_decides_the_headline(state):
    s = with_medians(state, exa=40, brave=40, **{"parallel-search": 40, "perplexity-search": 40}, tinyfish=40,
                     telem=40, agent=40, jevlin=1)
    assert results_outcome(s)["version"] == "google_faster"


@pytest.mark.parametrize("old,new,why", [
    ("Exa: 30 min median", "Exa: 25 min median", "does not match exa's median"),
    ("(4 wrong).\nGoogle", "(0 wrong).\nGoogle", "does not match exa's wrong"),
    ("114 races", "120 races", "not a total"),
    ("AI search was faster than Google.", "AI search was faster than Google, a landslide.", "commentary"),
])
def test_the_guard_checks_every_number_and_word(state, old, new, why):
    head = thread.compose(state, "ai_faster", REPO)[0]
    assert old in head
    ok, reasons = check_summary_post(head.replace(old, new), state, repo_url=REPO)
    assert not ok and why in " ".join(reasons), reasons


def test_the_guard_checks_links_hash_and_candidates(state):
    method = thread.compose(state, "ai_faster", REPO)[3]
    assert check_summary_post(method, state, repo_url=REPO)[0]
    assert "link not allowed" in " ".join(check_summary_post(method.replace(REPO, "https://evil.example/x"), state,
                                                             repo_url=REPO)[1])
    assert "not the pre-registration hash" in " ".join(check_summary_post(
        method.replace("0000000000000000", "deadbeefdeadbeef"), state, repo_url=REPO)[1])
    name = state["races"][0]["candidates"][0]["name"]
    assert "names candidate" in " ".join(check_summary_post(f"Thanks, {name}.", state, repo_url=REPO)[1])


def test_queue_is_a_reply_chain_and_publish_posts_it_in_order(google_wins, drafts, x_client, fake_x):
    queued = thread.queue_thread(google_wins, drafts, REPO)
    assert [d["position"] for d in queued] == list(range(1, len(queued) + 1))
    assert all(d["kind"] == "thread" and d["thread_id"] == "thread-google-faster" for d in queued)
    assert [d["reply_to_draft"] for d in queued] == [None] + [d["id"] for d in queued[:-1]]
    with pytest.raises(NotAuthorized, match="not published yet"):
        drafts.publish(queued[1]["id"], by="gaurav", state=google_wins, live=True, client=x_client)
    ids = [drafts.publish(d["id"], by="gaurav", state=google_wins, live=True, client=x_client).tweet_id for d in queued]
    assert "reply" not in fake_x.bodies[0]
    assert [b["reply"]["in_reply_to_tweet_id"] for b in fake_x.bodies[1:]] == ids[:-1]


def test_publish_rechecks_the_thread_against_the_current_state(state, drafts, x_client, fake_x):
    queued = thread.queue_thread(state, drafts, REPO)  # ai_faster
    flipped = with_medians(state, serper=10)          # a late correction: Google was faster after all
    with pytest.raises(NotAuthorized, match="guard fails"):
        drafts.publish(queued[0]["id"], by="gaurav", state=flipped, live=True, client=x_client)
    assert fake_x.requests == []


def test_notes_have_both_versions_and_say_which_is_supported(state, isolated):
    text = thread.write_notes(isolated / "notes" / "thread-draft.md", state, REPO)
    assert "## Version: ai_faster (SUPPORTED by the data)" in text
    assert "## Version: google_faster (NOT supported by the data: do not post)" in text
    assert "Google was faster than AI search." in text


def test_the_repo_link_is_required(monkeypatch):
    with pytest.raises(ConfigError, match="MIDTERMS_REPO_URL"):
        thread.repo_url()
    monkeypatch.setenv("MIDTERMS_REPO_URL", REPO + "/")
    assert thread.repo_url() == REPO
