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
def serper_wins(state):
    return with_medians(state, serper=10)


@pytest.fixture
def drafts(store):
    return Drafts(store=store)


def test_one_version_seven_posts_and_every_post_passes(state):
    assert thread.VERSIONS == ("results",)
    posts = thread.compose(state, "results", REPO)
    assert len(posts) == 7
    for e in ("Exa", "Brave", "Parallel", "Perplexity", "Serper", "TinyFish", "Telem"):
        assert f"\n{e}: " in posts[2]
    assert all(ok for ok, _ in thread.check_thread(posts, state, REPO)), thread.check_thread(posts, state, REPO)


def test_no_baseline_anywhere_in_the_thread(serper_wins):
    posts = thread.compose(serper_wins, "results", REPO)
    text = "\n".join(posts)
    assert "ahead of" not in text and "faster than" not in text
    assert posts[2].splitlines()[1] == "Serper: 10 min (6 wrong)"  # ranked first, like any engine
    assert all(ok for ok, _ in thread.check_thread(posts, serper_wins, REPO))


def test_claims_of_beating_an_engine_are_refused(state, serper_wins):
    ok, why = check_summary_post("AI search was faster than Serper.", serper_wins, repo_url=REPO)
    assert not ok and "Serper was as fast or faster" in " ".join(why)


def test_the_guard_checks_every_number_and_word(state):
    from agent.guard import first_coverage
    posts = thread.compose(state, "results", REPO)
    line = posts[2].splitlines()[1]                      # e.g. "Serper: 27.5 min (6 wrong)"
    name, rest = line.split(": ", 1)
    mins, wrong = rest.split(" min (")[0], rest.split("(")[1].split(" wrong")[0]
    cov = first_coverage(state)
    cases = [
        (posts[2], line, line.replace(f"{mins} min", f"{float(mins) + 3:g} min"), "median"),
        (posts[2], line, line.replace(f"({wrong} wrong)", f"({int(wrong) + 9} wrong)"), "wrong"),
        (posts[2], f"ranking, {cov['called']} races", f"ranking, {cov['called'] + 6} races", "not a total"),
        (posts[1], f"first on {cov['best_first']} of", f"first on {cov['best_first'] + 4} of", "not a total"),
        (posts[1], "That's supersearch.", "That's supersearch, a landslide.", "commentary"),
    ]
    for post, old, new, why in cases:
        assert old in post, (old, post)
        ok, reasons = check_summary_post(post.replace(old, new), state, repo_url=REPO)
        assert not ok and why in " ".join(reasons), (new, reasons)


def test_the_guard_checks_links_hash_and_candidates(state):
    posts = thread.compose(state, "results", REPO)
    method, close = posts[4], posts[6]
    assert check_summary_post(method, state, repo_url=REPO)[0] and check_summary_post(close, state, repo_url=REPO)[0]
    assert "link not allowed" in " ".join(check_summary_post(close.replace(REPO, "https://evil.example/x"), state,
                                                             repo_url=REPO)[1])
    assert "not the pre-registration hash" in " ".join(check_summary_post(
        method.replace("0000000000000000", "deadbeefdeadbeef"), state, repo_url=REPO)[1])
    name = state["races"][0]["candidates"][0]["name"]
    assert "names candidate" in " ".join(check_summary_post(f"Thanks, {name}.", state, repo_url=REPO)[1])


def test_queue_is_a_reply_chain_and_publish_posts_it_in_order(serper_wins, drafts, x_client, fake_x):
    queued = thread.queue_thread(serper_wins, drafts, REPO)
    assert [d["position"] for d in queued] == list(range(1, len(queued) + 1))
    assert all(d["kind"] == "thread" and d["thread_id"] == "thread-results" for d in queued)
    assert [d["reply_to_draft"] for d in queued] == [None] + [d["id"] for d in queued[:-1]]
    with pytest.raises(NotAuthorized, match="not published yet"):
        drafts.publish(queued[1]["id"], by="gaurav", state=serper_wins, live=True, client=x_client)
    ids = [drafts.publish(d["id"], by="gaurav", state=serper_wins, live=True, client=x_client).tweet_id for d in queued]
    assert "reply" not in fake_x.bodies[0]
    assert [b["reply"]["in_reply_to_tweet_id"] for b in fake_x.bodies[1:]] == ids[:-1]


def test_publish_rechecks_the_thread_against_the_current_state(state, drafts, x_client, fake_x):
    queued = thread.queue_thread(state, drafts, REPO)
    flipped = with_medians(state, exa=99)             # a late correction changes Exa's median
    with pytest.raises(NotAuthorized, match="guard fails"):
        drafts.publish(queued[2]["id"], by="gaurav", state=flipped, live=True, client=x_client)
    assert fake_x.requests == []


def test_notes_have_the_thread_with_guard_results(state, isolated):
    text = thread.write_notes(isolated / "notes" / "thread-draft.md", state, REPO)
    assert "through Jevlin" in text and "No single engine was first on every race." in text
    assert "guard pass" in text and "guard FAIL" not in text


def test_the_repo_link_is_required(monkeypatch):
    with pytest.raises(ConfigError, match="MIDTERMS_REPO_URL"):
        thread.repo_url()
    monkeypatch.setenv("MIDTERMS_REPO_URL", REPO + "/")
    assert thread.repo_url() == REPO


def test_a_thread_post_that_says_google_is_rejected(state):
    from agent.guard import check_summary_post
    ok, reasons = check_summary_post("Which search found election results first? Google.", state, repo_url="https://github.com/jevlinai/midterms")
    assert not ok and any("Google" in r for r in reasons)


def test_the_thread_leads_with_asking_every_engine(state):
    from agent.guard import first_coverage
    cov = first_coverage(state)
    lead = thread.compose(state, "results", REPO)[1]
    assert lead == thread.compose(with_medians(state, serper=10), "results", REPO)[1]  # true whichever engine wins
    assert "No single engine was first on every race." in lead
    assert f"first on {cov['best_first']} of {cov['found']}" in lead and cov["best_first"] < cov["found"]
    assert "One Jevlin call asks all of them, so you don't have to guess which." in lead
    assert check_summary_post(lead, state, repo_url=REPO)[0]


def test_one_engine_first_everywhere_is_said_instead(state):
    s = copy.deepcopy(state)
    for r in s["races"]:
        ts = [x["found_at"] for e, x in (r.get("engines") or {}).items() if x.get("found_at")]
        if ts and "exa" in r["engines"]:
            r["engines"]["exa"]["found_at"] = min(ts, key=lambda v: v)  # exa ties the earliest everywhere it ran
    from agent.guard import first_coverage
    cov = first_coverage(s)
    if not cov["one_engine_everywhere"]:
        pytest.skip("example state has called races exa never ran on")
    lead = thread.compose_lead(s)
    assert "One engine was first on every race any engine found: Exa." in lead
    assert check_summary_post(lead, s, repo_url=REPO)[0]


def test_the_thread_is_a_race_and_closes_on_superintelligence(state):
    posts = thread.compose(state, "results", REPO)
    assert "through Jevlin" in posts[0] and "agent" not in posts[0].lower()
    assert "Jevlin agent" not in "\n".join(posts)
    assert "Superintelligence" in posts[5] and "supersearch" in posts[6]
    assert "mnipresent" not in "\n".join(posts)
