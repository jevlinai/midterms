"""The post guard and the fallback template."""


import pytest

from agent.guard import check_post, check_answer, clock, template_post, weighted_length
from agent.state import is_called
from conftest import mutate, uncalled


def good_post(race: dict, state: dict) -> str:
    return template_post(race["id"], state)


def reasons(text, race_id, state):
    ok, why = check_post(text, race_id, state)
    assert not ok, f"expected a failure for: {text!r}"
    return " | ".join(why)


# ---------- the template always passes ----------

def test_template_passes_for_every_called_race(state):
    called = [r for r in state["races"] if is_called(r)]
    assert len(called) > 100
    for r in called:
        t = template_post(r["id"], state)
        ok, why = check_post(t, r["id"], state)
        assert ok, (r["id"], why, t)
        assert weighted_length(t) <= 280


def test_template_follows_launch_md(state):
    led_race = next(r for r in state["races"] if r["status"] == "called" and len(r["first"]) == 1
                    and r["first"][0] != "serper" and (r["engines"][r["first"][0]]["lead_vs_serper"] or 0) > 0)
    e = state["engines"]
    label = next(x["label"] for x in e if x["id"] == led_race["first"][0])
    mins = led_race["engines"][led_race["first"][0]]["minutes_after_close"]
    t = template_post(led_race["id"], state)
    assert t == (f"{led_race['name']}. AP called it at {clock(led_race['call']['ap_call_time'])} ET "
                 f"{led_race['call']['ap_url']}\nFirst to find it: {label}, {mins:g} min after polls "
                 "closed.\nLive: jevlin.ai/midterms")


@pytest.mark.parametrize("edit", [
    lambda r: r.update(first=[]),                                        # nobody found it yet
    lambda r: r.update(first=["serper"]),                                # Serper first
    lambda r: r.update(first=["exa", "brave"]),                          # a tie between two engines
    lambda r: (r.update(first=["exa", "serper"]), r["engines"]["exa"].update(lead_vs_serper=0)),  # tied with Serper
    lambda r: r.update(first=["exa", "serper"]),                         # inconsistent data: still passes
    lambda r: (r.update(first=["exa"]), r["engines"]["exa"].update(lead_vs_serper=-5)),  # inconsistent: no number
    lambda r: r.update(first=["jevlin", "exa"]),                         # the computed union ties the earliest
    lambda r: (r.update(first=["exa"]), r["engines"]["exa"].update(lead_vs_serper=None)),  # Serper never found it
    lambda r: r.update(name="North Carolina 14th Congressional District House Special Election"),
    lambda r: r["call"].update(ap_call_time="2026-11-04T00:07:00-05:00"),  # just after midnight ET
    lambda r: r["call"].update(ap_url="https://apnews.com/article/" + "x" * 200),  # links count 23
])
def test_template_passes_in_edge_cases(led_race, state, edit):
    s = mutate(state, led_race["id"], edit)
    t = template_post(led_race["id"], s)
    ok, why = check_post(t, led_race["id"], s)
    assert ok, (why, t)


def test_no_template_for_an_uncalled_race(state):
    with pytest.raises(ValueError, match="not called"):
        template_post(uncalled(state)["id"], state)


# ---------- the guard rejects ----------

def test_rejects_a_wrong_minute_count(led_race, state):
    mins = led_race["engines"][led_race["first"][0]]["minutes_after_close"]
    t = good_post(led_race, state).replace(f"{mins:g} min after polls", f"{mins + 5:g} min after polls")
    assert "does not match" in reasons(t, led_race["id"], state)


def test_rejects_a_minute_count_attributed_to_the_wrong_engine(led_race, state):
    first = led_race["first"][0]
    other = next(e for e, v in led_race["engines"].items()
                 if e not in (first, "serper", "jevlin") and v["lead_vs_serper"] != v_lead(led_race, first))
    label = {"exa": "Exa", "brave": "Brave", "parallel-search": "Parallel", "perplexity-search": "Perplexity",
             "tinyfish": "TinyFish", "agent": "Agent on Jevlin"}[other]
    lead = v_lead(led_race, first)
    t = (f"{led_race['name']}. AP called it at {clock(led_race['call']['ap_call_time'])} ET {led_race['call']['ap_url']}\n"
         f"{label} was {lead:g} min ahead of Serper.\nLive: jevlin.ai/midterms")
    assert led_race["engines"][other]["lead_vs_serper"] != lead
    assert "compares engines" in reasons(t, led_race["id"], state)


def v_lead(race, eid):
    return race["engines"][eid]["lead_vs_serper"]


def test_rejects_a_stray_number(led_race, state):
    t = good_post(led_race, state).replace("Live:", "Checked 4,312 pages. Live:")
    assert "4,312" in reasons(t, led_race["id"], state)


def test_rejects_a_wrong_call_time(led_race, state):
    t = good_post(led_race, state).replace(clock(led_race["call"]["ap_call_time"]), "11:59pm")
    assert "time '11:59pm'" in reasons(t, led_race["id"], state)


def test_rejects_a_winner_when_the_race_is_not_called(state):
    r = uncalled(state)
    name = r["candidates"][0]["name"]
    why = reasons(f"{r['name']}: {name} is the winner.\nLive: jevlin.ai/midterms", r["id"], state)
    assert "not called" in why and name in why


def test_rejects_call_talk_for_an_uncalled_race_even_without_a_name(state):
    r = uncalled(state)
    assert "no confirmed AP call" in reasons(f"{r['name']} has been called.\nLive: jevlin.ai/midterms", r["id"], state)


def test_rejects_a_surname_only_winner_for_an_uncalled_race(state):
    r = uncalled(state)
    surname = r["candidates"][0]["name"].split()[-1]
    assert "not called" in reasons(f"{r['name']}: {surname} is ahead.", r["id"], state)


def test_rejects_the_winner_without_the_ap_link(led_race, state):
    t = (f"{led_race['name']}. AP called it for {led_race['call']['winner']} at "
         f"{clock(led_race['call']['ap_call_time'])} ET.\nLive: jevlin.ai/midterms")
    why = reasons(t, led_race["id"], state)
    assert "AP link" in why


def test_accepts_the_confirmed_winner_with_the_ap_link(led_race, state):
    t = (f"{led_race['name']}. AP called it for {led_race['call']['winner']} at "
         f"{clock(led_race['call']['ap_call_time'])} ET {led_race['call']['ap_url']}\nLive: jevlin.ai/midterms")
    assert check_post(t, led_race["id"], state) == (True, [])


def test_rejects_naming_a_candidate_who_is_not_the_winner(led_race, state):
    loser = next(c["name"] for c in led_race["candidates"] if c["name"] != led_race["call"]["winner"])
    t = good_post(led_race, state).replace("Live:", f"Called for {loser}. Live:")
    assert "not the confirmed winner" in reasons(t, led_race["id"], state)


def test_rejects_a_candidate_from_another_race(led_race, state):
    own = {c["name"] for c in led_race["candidates"]}
    other = next(c["name"] for r in state["races"] if r["id"] != led_race["id"] for c in r["candidates"]
                 if c["name"] not in own)
    t = good_post(led_race, state).replace("Live:", f"{other}. Live:")
    assert "another race" in reasons(t, led_race["id"], state)


def test_rejects_an_invented_name(led_race, state):
    t = good_post(led_race, state).replace("Live:", "Jane Q Nobody. Live:")
    assert "unrecognised name" in reasons(t, led_race["id"], state)


@pytest.mark.parametrize("phrase", ["Democrats hold the seat.", "A Republican flip.", "Projected landslide.",
                                    "She will win easily.", "Likely a recount.", "The GOP gains.", "Victory for the left.",
                                    "The odds were long.", "Voters should be glad.", "(D)", "Great night!"])
def test_rejects_party_commentary_predictions_and_odds(led_race, state, phrase):
    t = good_post(led_race, state).replace("Live:", f"{phrase} Live:")
    assert "not allowed" in reasons(t, led_race["id"], state) or "commentary" in reasons(t, led_race["id"], state)


def test_rejects_a_missing_ap_link_when_talking_about_the_call(led_race, state):
    t = good_post(led_race, state).replace(" " + led_race["call"]["ap_url"], "")
    assert "AP link" in reasons(t, led_race["id"], state)


def test_rejects_other_links(led_race, state):
    t = good_post(led_race, state).replace("jevlin.ai/midterms", "https://evil.example/x")
    assert "link not allowed" in reasons(t, led_race["id"], state)


def test_rejects_a_wrong_first_claim(led_race, state):
    wrong = next(e for e in ("exa", "brave", "tinyfish", "serper") if e not in led_race["first"])
    label = {"exa": "Exa", "brave": "Brave", "tinyfish": "TinyFish", "serper": "Serper"}[wrong]
    first_label = next(x["label"] for x in state["engines"] if x["id"] == led_race["first"][0])
    t = good_post(led_race, state).replace(f"find it: {first_label},", f"find it: {label},")
    assert "was first" in reasons(t, led_race["id"], state)


def test_rejects_an_engine_outside_the_measurement(led_race, state):
    t = good_post(led_race, state).replace("Live:", "Bing was slower. Live:")
    assert "not in the measurement" in reasons(t, led_race["id"], state)


def test_rejects_engine_results_for_an_uncalled_race(state):
    r = uncalled(state)
    assert "only for called races" in reasons(f"{r['name']}: Perplexity is searching.", r["id"], state)


def test_rejects_too_long(led_race, state):
    t = good_post(led_race, state) + "\n" + "Search speed, measured live. " * 8
    assert "too long" in reasons(t, led_race["id"], state)


def test_weighted_length_counts_links_as_23():
    assert weighted_length("a https://apnews.com/" + "x" * 300) == 2 + 23
    assert weighted_length("jevlin.ai/midterms") == 23
    assert weighted_length("中") == 2


def test_unknown_race():
    assert check_post("hello", "senate-zz", {"races": [], "engines": []}) == (False, ["no race 'senate-zz' in state"])


def test_check_answer_flags_candidates_of_uncalled_races(state):
    r = uncalled(state)
    assert check_answer(f"{r['candidates'][1]['name']} is winning.", state)
    assert check_answer("Perplexity was first in 12 races.", state) == []


def test_a_post_that_says_google_is_rejected(state, led_race):
    from agent.guard import check_post, template_post
    text = template_post(led_race["id"], state).replace("Live:", "Google had it too.\nLive:")
    ok, reasons = check_post(text, led_race["id"], state)
    assert not ok and any("Google" in r for r in reasons), text


def test_a_note_before_the_post_or_a_missing_race_name_is_rejected(led_race, state):
    from agent.guard import check_post, template_post
    good = template_post(led_race["id"], state)
    for bad in ("Three engines tied for first, so I'll name all three.\n\n" + good,
                good.replace(led_race["name"], "NFL game", 1)):
        ok, reasons = check_post(bad, led_race["id"], state)
        assert not ok and any("must start with the race name" in r for r in reasons), bad


def test_engines_are_never_set_against_each_other(led_race, state):
    from agent.guard import check_post, template_post
    for phrase in ("12 min ahead of Serper", "behind Exa", "tied with Serper"):
        text = template_post(led_race["id"], state).replace("Live:", f"Exa was {phrase}.\nLive:")
        ok, reasons = check_post(text, led_race["id"], state)
        assert not ok and any("compares engines" in r for r in reasons), text
