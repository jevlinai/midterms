"""Confirm tool safety checks. Run: python3 -m unittest confirm.test_confirm"""
import datetime as dt
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from confirm import confirm as C

RACES = {"senate-zz": {"id": "senate-zz", "office": "senate", "state": "GA", "district": None, "name": "Test Senate",
                       "poll_close_et": "2026-11-03T19:00:00-05:00",
                       "candidates": [{"name": "Ada Alpha", "party": "D", "incumbent": False},
                                      {"name": "Ben Bravo", "party": "R", "incumbent": True}]}}
OK = ["confirm", "senate-zz", "--winner", "Ada Alpha", "--time", "2026-11-03T20:00", "--url", "https://apnews.com/article/x"]


class Confirm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.patches = [mock.patch.object(C, "STATE", d), mock.patch.object(C, "CONFIRMED", d / "confirmed.json"),
                        mock.patch.object(C, "LOG", d / "log.jsonl"), mock.patch.object(C, "PROPOSALS", d / "p.json"),
                        mock.patch.object(C, "load_races", lambda: RACES),
                        mock.patch.object(C, "now_et", lambda: dt.datetime(2026, 11, 4, 3, 0, tzinfo=C.ET))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_cmd(self, *extra):
        C.main(list(extra))

    def saved(self):
        return json.loads(C.CONFIRMED.read_text())["senate-zz"]

    def test_accept_uses_the_proposal(self):
        C.PROPOSALS.write_text(json.dumps({"senate-zz": {"race_id": "senate-zz", "winner": "Ada Alpha",
            "ap_call_time": "2026-11-03T20:05:00-05:00", "ap_url": "https://apnews.com/article/x", "quote": "q"}}))
        self.run_cmd("accept", "senate-zz", "--by", "one")
        s = self.saved()
        self.assertEqual((s["winner"], s["ap_url"], s["confirmed_by"]), ("Ada Alpha", "https://apnews.com/article/x", ["one"]))
        self.assertTrue(s["ap_call_time"].startswith("2026-11-03T20:05"))

    def test_accept_needs_a_time_when_the_proposal_has_none(self):
        C.PROPOSALS.write_text(json.dumps({"senate-zz": {"winner": "Ada Alpha", "ap_call_time": None,
                                                         "ap_url": "https://apnews.com/article/x"}}))
        with self.assertRaises(SystemExit):
            self.run_cmd("accept", "senate-zz", "--by", "one")
        self.run_cmd("accept", "senate-zz", "--by", "one", "--time", "2026-11-03T20:10")
        self.assertTrue(self.saved()["ap_call_time"].startswith("2026-11-03T20:10"))

    def test_accept_without_a_proposal_refuses(self):
        with self.assertRaises(SystemExit):
            self.run_cmd("accept", "senate-zz", "--by", "one")

    def test_one_person_is_enough_by_default(self):
        self.run_cmd(*OK, "--by", "one")
        self.assertTrue(C.is_called(self.saved(), RACES["senate-zz"]))

    @mock.patch.object(C, "TWO_PEOPLE", {"senate", "governor"})
    def test_two_different_people_for_senate(self):
        self.run_cmd(*OK, "--by", "one")
        self.assertFalse(C.is_called(self.saved(), RACES["senate-zz"]))
        with self.assertRaises(SystemExit):
            self.run_cmd(*OK, "--by", "one")  # same person twice
        self.run_cmd(*OK, "--by", "two")
        self.assertTrue(C.is_called(self.saved(), RACES["senate-zz"]))

    def test_rejects_non_candidate_non_ap_and_early_time(self):
        with self.assertRaises(SystemExit):
            self.run_cmd("confirm", "senate-zz", "--winner", "Somebody Else", "--time", "2026-11-03T20:00", "--url", "https://apnews.com/a", "--by", "x")
        with self.assertRaises(SystemExit):
            self.run_cmd("confirm", "senate-zz", "--winner", "Ada Alpha", "--time", "2026-11-03T20:00", "--url", "https://example.com/a", "--by", "x")
        with self.assertRaises(SystemExit):
            self.run_cmd("confirm", "senate-zz", "--winner", "Ada Alpha", "--time", "2026-11-03T18:00", "--url", "https://apnews.com/a", "--by", "x")

    @mock.patch.object(C, "TWO_PEOPLE", {"senate", "governor"})
    def test_second_person_must_agree(self):
        self.run_cmd(*OK, "--by", "one")
        with self.assertRaises(SystemExit):
            self.run_cmd("confirm", "senate-zz", "--winner", "Ben Bravo", "--time", "2026-11-03T20:00", "--url", "https://apnews.com/article/x", "--by", "two")

    @mock.patch.object(C, "TWO_PEOPLE", {"senate", "governor"})
    def test_retract(self):
        self.run_cmd(*OK, "--by", "one")
        self.run_cmd(*OK, "--by", "two")
        self.run_cmd("retract", "senate-zz", "--by", "one", "--reason", "test")
        self.assertFalse(C.is_called(self.saved(), RACES["senate-zz"]))


if __name__ == "__main__":
    unittest.main()
