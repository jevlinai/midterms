"""Confirm tool safety checks. Run: python3 -m unittest confirm.test_confirm"""
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
                        mock.patch.object(C, "load_races", lambda: RACES)]
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

    def test_second_person_must_agree(self):
        self.run_cmd(*OK, "--by", "one")
        with self.assertRaises(SystemExit):
            self.run_cmd("confirm", "senate-zz", "--winner", "Ben Bravo", "--time", "2026-11-03T20:00", "--url", "https://apnews.com/article/x", "--by", "two")

    def test_retract(self):
        self.run_cmd(*OK, "--by", "one")
        self.run_cmd(*OK, "--by", "two")
        self.run_cmd("retract", "senate-zz", "--by", "one", "--reason", "test")
        self.assertFalse(C.is_called(self.saved(), RACES["senate-zz"]))


if __name__ == "__main__":
    unittest.main()
