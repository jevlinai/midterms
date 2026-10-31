"""Detector tests (no network). Run: python3 -I -m unittest detector.test_detector"""
import concurrent.futures as cf
import pathlib
import tempfile
import unittest

from detector import textmatch
from detector.detect import judge_record
from detector.jev import Jev, decide

SULLIVANS = [{"name": "Dan Sullivan", "surname": "Sullivan", "party": "R"},
             {"name": "Daniel J. Sullivan Jr.", "surname": "Sullivan", "party": None},
             {"name": "Ann Other", "surname": "Other", "party": "D"}]
LUJAN = [{"name": "Ben Ray Luján", "surname": "Luján", "party": "D"}, {"name": "Nella Domenici", "surname": "Domenici", "party": "R"}]
COMPOUND = [{"name": "Marie Gluesenkamp Perez", "surname": "Gluesenkamp Perez", "party": "D"},
            {"name": "Joe Kent", "surname": "Kent", "party": "R"}]


class Filter(unittest.TestCase):
    def hit(self, text, cands):
        return textmatch.filter_hit(text, textmatch.name_patterns(cands))

    def test_needs_win_word(self):
        self.assertEqual(self.hit("Sullivan and Other debate in Anchorage", SULLIVANS), [])

    def test_shared_surname_needs_first_name(self):
        self.assertEqual(self.hit("Sullivan wins Alaska Senate race", SULLIVANS), [])
        self.assertEqual(self.hit("Dan Sullivan wins Alaska Senate race", SULLIVANS), ["Dan Sullivan"])

    def test_accent_folding(self):
        self.assertEqual(self.hit("Lujan re-elected to Senate", LUJAN), ["Ben Ray Luján"])

    def test_compound_surname_last_part(self):
        self.assertEqual(self.hit("Perez defeats Kent again", COMPOUND), ["Marie Gluesenkamp Perez", "Joe Kent"])

    def test_no_substring_match(self):
        self.assertEqual(self.hit("Kentucky results: winner declared", COMPOUND), [])


def fake_jev(answer_for):
    """A transport that returns a fixed answer per page text keyword."""
    def transport(req):
        page = req["state"]["page"]
        for kw, (choice, p, this_e, call) in answer_for.items():
            if kw in page:
                return {"model": "jev-test", "usage": {"input_tokens": 100},
                        "answers": {"winner": {"type": "choice", "choice": choice, "probabilities": {choice: p}},
                                    "this_election": {"type": "noul", "noul": this_e},
                                    "is_call": {"type": "noul", "noul": call}}}
        return {"model": "jev-test", "usage": {"input_tokens": 100},
                "answers": {"winner": {"type": "choice", "choice": "none", "probabilities": {"none": 0.99}},
                            "this_election": {"type": "noul", "noul": 0.9}, "is_call": {"type": "noul", "noul": 0.1}}}
    return transport


class Rule(unittest.TestCase):
    def ans(self, choice, p, this_e, call):
        return {"answers": {"winner": {"choice": choice, "probabilities": {choice: p}},
                            "this_election": {"noul": this_e}, "is_call": {"noul": call}}}

    def test_rule(self):
        self.assertEqual(decide(self.ans("Ann Other", 0.95, 0.95, 0.9)), "Ann Other")
        self.assertIsNone(decide(self.ans("Ann Other", 0.6, 0.95, 0.9)))   # unsure who
        self.assertIsNone(decide(self.ans("Ann Other", 0.95, 0.3, 0.9)))   # wrong election (e.g. 2020)
        self.assertIsNone(decide(self.ans("Ann Other", 0.95, 0.95, 0.2)))  # a lead, not a call
        self.assertIsNone(decide(self.ans("none", 0.99, 0.95, 0.9)))


RACE = {"id": "senate-zz", "name": "Example Senate", "office": "senate", "state": "AK", "district": None,
        "candidates": SULLIVANS}


def record(engines):
    return {"round": "20261103T2100", "race_id": "senate-zz", "summary": {"cache_hit": False},
            "response": {"candidates": [{"provider": e, "status": "ok", "citations": c} for e, c in engines.items()]}}


class Credit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jev = Jev(cache_path=pathlib.Path(self.tmp.name) / "c.sqlite",
                       transport=fake_jev({"AP": ("Ann Other", 0.97, 0.97, 0.95)}))
        self.pool = cf.ThreadPoolExecutor(4)

    def tearDown(self):
        self.pool.shutdown()
        self.tmp.cleanup()

    def test_page_mode_credits_every_engine_with_the_url(self):
        url = "https://apnews.com/article/alaska-senate-2026"
        rec = record({
            "exa": [{"url": url, "title": "Alaska Senate", "snippet": "AP: Ann Other wins the Alaska Senate race, defeating Dan Sullivan"}],
            "serper": [{"url": url.replace("https://", "https://www."), "title": "Alaska Senate", "snippet": "Alaska Senate"}],
            "brave": [{"url": "https://example.com/preview", "title": "Preview", "snippet": "Who will win in Alaska?"}],
        })
        f = judge_record(rec, RACE, self.jev, "page", self.pool)
        self.assertEqual(f["engines"]["exa"]["named"], {"Ann Other": [url]})
        self.assertIn("Ann Other", f["engines"]["serper"]["named"])  # same page, short snippet: still credited
        self.assertEqual(f["engines"]["brave"]["named"], {})

    def test_engine_mode_judges_own_text_only(self):
        url = "https://apnews.com/article/alaska-senate-2026"
        rec = record({
            "exa": [{"url": url, "title": "Alaska Senate", "snippet": "AP: Ann Other wins the Alaska Senate race"}],
            "serper": [{"url": url, "title": "Alaska Senate", "snippet": "Alaska Senate"}],
        })
        f = judge_record(rec, RACE, self.jev, "engine", self.pool)
        self.assertIn("Ann Other", f["engines"]["exa"]["named"])
        self.assertEqual(f["engines"]["serper"]["named"], {})

    def test_cache(self):
        rec = record({"exa": [{"url": "https://apnews.com/x", "title": "t", "snippet": "AP: Ann Other wins"}]})
        judge_record(rec, RACE, self.jev, "page", self.pool)
        _, _, cached = self.jev.check(RACE, RACE["candidates"], "t\nAP: Ann Other wins")
        self.assertTrue(cached)


if __name__ == "__main__":
    unittest.main()
