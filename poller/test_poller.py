"""Schedule and summary tests. Run: python3 -I poller/test_poller.py"""
import datetime as dt
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import poller  # noqa: E402

CFG = json.loads((pathlib.Path(__file__).resolve().parent / "config.json").read_text())
ET = poller.ET


def at(day, hh, mm=0):
    return dt.datetime(2026, 11, day, hh, mm, tzinfo=ET)


RACE = {"id": "senate-ga", "poll_close_et": "2026-11-03T19:00:00-05:00", "query": "q"}


class Schedule(unittest.TestCase):
    def ids(self, tick, inactive=()):
        return [r["id"] for r in poller.active_races([RACE], tick, CFG, set(inactive))]

    def test_not_before_close_except_sweeps(self):
        self.assertEqual(self.ids(at(3, 18, 55)), [])
        self.assertEqual(self.ids(at(3, 18, 0)), ["senate-ga"])   # close - 60 min sweep
        self.assertEqual(self.ids(at(3, 18, 45)), ["senate-ga"])  # close - 15 min sweep
        self.assertEqual(self.ids(at(3, 18, 30)), [])

    def test_active_from_close(self):
        self.assertEqual(self.ids(at(3, 19, 0)), ["senate-ga"])
        self.assertEqual(self.ids(at(4, 6, 55)), ["senate-ga"])  # close + 11h55

    def test_followup_hourly_only(self):
        self.assertEqual(self.ids(at(4, 7, 5)), [])  # past 12h, not on the hour
        self.assertEqual(self.ids(at(4, 8, 0)), ["senate-ga"])

    def test_followup_ends(self):
        self.assertEqual(self.ids(at(11, 9, 0)), [])

    def test_inactive_skipped(self):
        self.assertEqual(self.ids(at(3, 20, 0), ["senate-ga"]), [])

    def test_uncontested_skipped(self):
        r = dict(RACE, uncontested=True)
        self.assertEqual(poller.active_races([r], at(3, 20), CFG, set()), [])

    def test_round_alignment(self):
        self.assertEqual(poller.round_start(at(3, 19, 7), 5), at(3, 19, 5))


class Summary(unittest.TestCase):
    def test_flags_cache_hit_trimmed_and_missing(self):
        resp = {
            "request_id": "x",
            "candidates": [{"provider": "exa", "status": "ok", "citations": [{}, {}], "cost_micros": 7000,
                            "latency": {"total_ms": 900}}],
            "decision": {"trimmed": ["telem"]},
            "usage": {"cache_hit": True, "cost_micros": 0},
        }
        s = poller.summarize(resp, CFG)
        self.assertTrue(s["cache_hit"])
        self.assertEqual(s["trimmed"], ["telem"])
        self.assertIn("serper", s["missing"])
        self.assertEqual(s["engines"]["exa"]["results"], 2)


if __name__ == "__main__":
    unittest.main()
