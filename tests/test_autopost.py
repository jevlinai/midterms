"""autopost: confirmed calls go out as one live reply thread under the anchor post, paced and capped."""

import copy

import pytest

from agent import autopost
from agent.drafts import Drafts
from agent.guard import LIVE_URL, check_post

ANCHOR = "1849999999999999999"


@pytest.fixture
def drafts(store):
    return Drafts(store=store)


def postable(state):
    return [r for r in state["races"] if r["office"] in ("senate", "governor") and r["status"] == "called"
            and autopost.worth_posting(r, state)[0]]


def test_nothing_without_an_anchor_or_with_the_stop_file(state, drafts, x_client, fake_x, isolated):
    assert autopost.run_pass(state, drafts, anchor=None, live=True, client=x_client)["stopped"]
    (isolated / "STOP_POSTING").write_text("")
    assert "stop file" in autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client)["stopped"]
    assert fake_x.requests == []


def test_posts_a_thread_under_the_anchor_one_per_gap(state, drafts, x_client, fake_x):
    races = sorted(postable(state), key=lambda r: (r["call"]["ap_call_time"], r["id"]))
    assert len(races) >= 3
    t0 = 1_800_000_000
    opener = autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, now=t0)
    assert opener["posted"]["race_id"] == autopost.OPENER_ID and fake_x.bodies[0]["text"] == autopost.OPENER
    assert fake_x.bodies[0]["reply"]["in_reply_to_tweet_id"] == ANCHOR
    # too soon: nothing
    assert autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, now=t0 + 30)["posted"] is None
    first = autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, now=t0 + 200)
    assert first["posted"]["race_id"] == races[0]["id"] and first["posted"]["status"] == "posted"
    assert fake_x.bodies[1]["reply"]["in_reply_to_tweet_id"] == "1850000000000000001"


def test_each_reply_answers_the_previous_one(state, drafts, x_client, fake_x):
    for _ in range(4):
        autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0, now=4_000_000_000)
    replies = [b["reply"]["in_reply_to_tweet_id"] for b in fake_x.bodies]
    ids = [str(1850000000000000001 + i) for i in range(4)]
    assert replies == [ANCHOR, ids[0], ids[1], ids[2]]


def test_reply_text_is_the_template_without_the_live_line_and_passes_the_guard(state):
    r = postable(state)[0]
    text = autopost.reply_text(r["id"], state)
    assert LIVE_URL not in text and text.startswith(r["name"]) and r["call"]["ap_url"] in text
    assert check_post(text, r["id"], state)[0]
    assert "#" not in text and "@" not in text


def test_never_posts_an_uncalled_race_or_one_nobody_found(state, drafts, x_client, fake_x):
    s = copy.deepcopy(state)
    for r in s["races"]:
        if r["status"] == "called" and r["office"] in ("senate", "governor"):
            r["first"] = []
    autopost.run_pass(s, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0)  # opener
    res = autopost.run_pass(s, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0)
    assert res["posted"] is None and len(fake_x.requests) == 1
    assert all(v == "no engine has found the winner yet" for v in res["skipped"].values())


def test_skips_races_where_every_engine_tied(state):
    r = copy.deepcopy(postable(state)[0])
    r["first"] = list(r["engines"].keys())
    assert autopost.worth_posting(r, state) == (False, "every engine was first together")


def test_cap(state, drafts, x_client, fake_x):
    for _ in range(5):
        autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0, max_posts=3)
    assert len(fake_x.requests) == 3  # the opener and two races


def test_withdrawn_call_is_reported_and_not_reposted(state, drafts, x_client, fake_x):
    autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0)  # opener
    first = autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0)
    rid = first["posted"]["race_id"]
    s = copy.deepcopy(state)
    for r in s["races"]:
        if r["id"] == rid:
            r["status"], r["call"] = "closed", None
    res = autopost.run_pass(s, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0)
    assert res["withdrawn"] and res["withdrawn"][0]["race_id"] == rid
    assert all(b["text"].split(".")[0] != rid for b in fake_x.bodies[1:])


def test_dry_run_without_the_live_switch(state, drafts, x_client, fake_x):
    res = autopost.run_pass(state, drafts, anchor=ANCHOR, client=x_client, gap_s=0)  # env unset
    assert res["posted"]["status"] == "dry_run" and fake_x.requests == []
    assert autopost.run_pass(state, drafts, anchor=ANCHOR, client=x_client, gap_s=0)["posted"]["race_id"] == autopost.OPENER_ID


def test_a_failed_post_waits_a_gap_before_trying_again(state, drafts, x_client, fake_x):
    t0 = 1_900_000_000
    autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, now=t0 - 1000)  # opener
    fake_x.fail_with = [403]
    res = autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, now=t0)
    assert res["posted"]["status"] == "failed"
    assert autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, now=t0 + 10)["posted"] is None
    later = autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, now=t0 + 200)
    assert later["posted"]["status"] == "posted"


def test_a_race_posted_by_hand_is_not_posted_again(state, drafts, x_client, fake_x):
    from agent.guard import template_post
    r = sorted(postable(state), key=lambda r: (r["call"]["ap_call_time"], r["id"]))[0]
    d = drafts.add(r["id"], template_post(r["id"], state), state, source="template")
    drafts.publish(d["id"], by="hammad", state=state, live=True, client=x_client)
    autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0)  # opener
    res = autopost.run_pass(state, drafts, anchor=ANCHOR, live=True, client=x_client, gap_s=0)
    assert res["posted"]["race_id"] != r["id"]
