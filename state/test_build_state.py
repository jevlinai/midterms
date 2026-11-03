"""build_state tests on synthetic races. Run: python3 -m unittest state.test_build_state"""
import datetime as dt
import unittest

from state.build_state import build, ET

CANDS = [{"name": "Ada Alpha", "party": "D", "incumbent": False}, {"name": "Ben Bravo", "party": "R", "incumbent": True}]
RACES = {
    "senate-zz": {"id": "senate-zz", "office": "senate", "state": "GA", "district": None, "name": "Test Senate",
                  "poll_close_et": "2026-11-03T19:00:00-05:00", "candidates": CANDS},
    "house-zz-01": {"id": "house-zz-01", "office": "house", "state": "GA", "district": "01", "name": "Test House",
                    "poll_close_et": "2026-11-03T19:00:00-05:00", "candidates": CANDS},
}
SIX = ["exa", "brave", "parallel-search", "perplexity-search", "serper", "tinyfish"]
NOW = dt.datetime(2026, 11, 3, 22, 0, tzinfo=ET)
PRE = {"sha256": None, "committed_at": None, "url": None}


def t(hh, mm):
    return dt.datetime(2026, 11, 3, hh, mm, tzinfo=ET)


def rows(*named_by_round):
    return [(t(19, 5 * i), set(n), "ok", {x: ["https://example.com/p"] for x in n}) for i, n in enumerate(named_by_round)]


def conf(by):
    return {"winner": "Ada Alpha", "party": "D", "ap_call_time": "2026-11-03T19:10:00-05:00",
            "ap_url": "https://apnews.com/x", "confirmed_by": by}


class Build(unittest.TestCase):
    def test_one_person_enough_for_senate_by_default(self):
        s, _ = build(RACES, {}, {"senate-zz": conf(["one"])}, {}, PRE, NOW)
        self.assertEqual(next(x for x in s["races"] if x["id"] == "senate-zz")["status"], "called")

    def test_no_winner_until_two_people_confirm_senate_when_configured(self):
        import confirm.confirm as C
        from unittest import mock
        findings = {"senate-zz": {e: rows([], ["Ada Alpha"]) for e in SIX}}
        with mock.patch.object(C, "TWO_PEOPLE", {"senate", "governor"}):
            s, _ = build(RACES, findings, {"senate-zz": conf(["one"])}, {}, PRE, NOW)
        r = next(x for x in s["races"] if x["id"] == "senate-zz")
        self.assertEqual(r["status"], "closed")
        self.assertIsNone(r["call"])
        self.assertEqual(r["engines"], {})

    def test_one_person_enough_for_house(self):
        s, _ = build(RACES, {}, {"house-zz-01": conf(["one"])}, {}, PRE, NOW)
        self.assertEqual(next(x for x in s["races"] if x["id"] == "house-zz-01")["status"], "called")

    def test_found_wrong_lead_first_jevlin(self):
        f = {e: rows([], [], ["Ada Alpha"], ["Ada Alpha"]) for e in SIX}
        f["exa"] = rows([], ["Ada Alpha"], ["Ada Alpha"])          # found at 19:05
        f["brave"] = rows(["Ben Bravo"], [], ["Ada Alpha"], ["Ada Alpha"])  # wrong at 19:00
        s, inactive = build(RACES, {"senate-zz": f}, {"senate-zz": conf(["one", "two"])}, {}, PRE, NOW)
        r = next(x for x in s["races"] if x["id"] == "senate-zz")
        self.assertEqual(r["engines"]["exa"]["minutes_after_close"], 5)
        self.assertEqual(r["engines"]["exa"]["lead_vs_serper"], 5)       # Serper found at 19:10
        self.assertEqual(r["engines"]["brave"]["wrong_at"], t(19, 0).isoformat())
        self.assertEqual(r["first"], ["exa"])
        self.assertEqual(r["engines"]["jevlin"]["minutes_after_close"], 5)
        self.assertIn("senate-zz", inactive)  # every engine named the winner in two consecutive rounds
        lb = {b["engine"]: b for b in s["leaderboard"]}
        self.assertEqual(lb["brave"]["wrong"], 1)
        self.assertEqual(lb["exa"]["found_first"], 1)

    def test_not_inactive_until_all_six_twice(self):
        f = {e: rows(["Ada Alpha"], ["Ada Alpha"]) for e in SIX}
        f["tinyfish"] = rows(["Ada Alpha"], [])
        _, inactive = build(RACES, {"senate-zz": f}, {"senate-zz": conf(["one", "two"])}, {}, PRE, NOW)
        self.assertNotIn("senate-zz", inactive)


if __name__ == "__main__":
    unittest.main()


class PrecloseExclusion(unittest.TestCase):
    def test_site_naming_winner_before_close_is_excluded_everywhere(self):
        fake = "https://1m.fakeprint.example/article/x"
        real = "https://apnews.com/article/y"
        def r(hh, mm, urls):
            return (t(hh, mm), set(urls), "ok", urls)
        f = {e: [r(18, 0, {}), r(19, 0, {}), r(19, 5, {"Ada Alpha": [real]})] for e in SIX}
        # exa returned a pre-written page naming the winner an hour before polls closed, and again after.
        f["exa"] = [r(18, 0, {"Ada Alpha": [fake]}), r(19, 0, {"Ada Alpha": [fake.replace("1m.", "1y.")]}),
                    r(19, 5, {"Ada Alpha": [real]})]
        s, _ = build(RACES, {"senate-zz": f}, {"senate-zz": conf(["one", "two"])}, {}, PRE, NOW)
        race = next(x for x in s["races"] if x["id"] == "senate-zz")
        self.assertEqual(race["engines"]["exa"]["minutes_after_close"], 5)  # not 0: the fake page doesn't count
        self.assertEqual([x["site"] for x in s["excluded_sites"]], ["fakeprint.example"])

    def test_preclose_rounds_never_count_as_found(self):
        f = {e: [(t(18, 45), {"Ada Alpha"}, "ok", {"Ada Alpha": ["https://apnews.com/a"]})] for e in SIX}
        s, _ = build(RACES, {"senate-zz": f}, {"senate-zz": conf(["one", "two"])}, {}, PRE, NOW)
        race = next(x for x in s["races"] if x["id"] == "senate-zz")
        self.assertIsNone(race["engines"]["exa"]["found_at"])
