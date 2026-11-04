"""The confirm page runs confirm.py's own commands. Run: python3 -m unittest confirm.test_web"""
import datetime as dt
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.request
from http.server import HTTPServer
from unittest import mock

from confirm import confirm as C
from confirm import web as W
from confirm.test_confirm import RACES

NIGHT = dt.datetime(2026, 11, 3, 20, 30, tzinfo=C.ET)
PROPOSAL = {"senate-zz": {"race_id": "senate-zz", "winner": "Ada Alpha", "ap_call_time": "2026-11-03T20:05:00-05:00",
                          "ap_url": "https://apnews.com/article/x", "quote": "AP: Ada Alpha wins", "found_at": "x"}}


class Web(unittest.TestCase):
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

    def test_status_lists_a_closed_race_with_its_proposal(self):
        C.PROPOSALS.write_text(json.dumps(PROPOSAL))
        s = W.status(NIGHT)
        self.assertEqual([w["id"] for w in s["waiting"]], ["senate-zz"])
        self.assertEqual(s["waiting"][0]["proposal"]["winner"], "Ada Alpha")
        self.assertEqual(s["waiting"][0]["minutes_since_close"], 90)

    def test_each_race_links_to_its_ap_results_page(self):
        self.assertEqual(W.ap_page({"state": "RI", "office": "senate"}),
                         "https://apnews.com/projects/elections-2026/rhode-island-us-senate-results/")
        self.assertEqual(W.ap_page({"state": "NH", "office": "governor"}),
                         "https://apnews.com/projects/elections-2026/new-hampshire-governor-results/")
        self.assertEqual(W.ap_page({"state": "VA", "office": "house"}),
                         "https://apnews.com/projects/elections-2026/virginia-us-house-results/")

    def test_nothing_waits_before_polls_close(self):
        s = W.status(dt.datetime(2026, 11, 3, 18, 0, tzinfo=C.ET))
        self.assertEqual(s["waiting"], [])
        self.assertEqual(s["upcoming_total"], 1)

    def test_accept_runs_confirm_accept_and_the_race_is_called(self):
        C.PROPOSALS.write_text(json.dumps(PROPOSAL))
        code, out = W.handle_post("/api/accept", {"race_id": "senate-zz", "by": "Hammad"})
        self.assertEqual(code, 200, out)
        self.assertTrue(C.is_called(C.load(C.CONFIRMED, {})["senate-zz"], RACES["senate-zz"]))
        self.assertEqual(W.status(NIGHT)["called_total"], 1)

    def test_confirm_py_checks_still_apply(self):
        code, out = W.handle_post("/api/confirm", {"race_id": "senate-zz", "by": "Hammad", "winner": "Ada Alpha",
                                                   "time": "2026-11-03T20:00", "url": "https://example.com/a"})
        self.assertEqual(code, 422)
        self.assertIn("AP", out["message"])
        self.assertFalse(C.CONFIRMED.exists())

    def test_a_call_time_in_the_future_is_refused(self):
        code, out = W.handle_post("/api/confirm", {"race_id": "senate-zz", "by": "Hammad", "winner": "Ada Alpha",
                                                   "time": "2099-11-04T05:10", "url": "https://apnews.com/article/x"})
        self.assertEqual(code, 422)
        self.assertIn("future", out["message"])

    def test_a_name_is_required(self):
        C.PROPOSALS.write_text(json.dumps(PROPOSAL))
        self.assertEqual(W.handle_post("/api/accept", {"race_id": "senate-zz", "by": " "})[0], 400)

    def test_retract(self):
        C.PROPOSALS.write_text(json.dumps(PROPOSAL))
        W.handle_post("/api/accept", {"race_id": "senate-zz", "by": "Hammad"})
        code, _ = W.handle_post("/api/retract", {"race_id": "senate-zz", "by": "Hammad", "reason": "test"})
        self.assertEqual(code, 200)
        self.assertFalse(C.is_called(C.load(C.CONFIRMED, {})["senate-zz"], RACES["senate-zz"]))

    def test_server_refuses_cross_origin_and_non_json_posts(self):
        srv = HTTPServer(("127.0.0.1", 0), W.Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            def post(headers, body=b'{"race_id":"senate-zz","by":"x"}'):
                req = urllib.request.Request(base + "/api/accept", data=body, method="POST", headers=headers)
                try:
                    return urllib.request.urlopen(req).status
                except urllib.error.HTTPError as e:
                    return e.code
            self.assertEqual(post({"content-type": "application/x-www-form-urlencoded"}), 415)
            self.assertEqual(post({"content-type": "application/json", "origin": "https://evil.example"}), 403)
            self.assertEqual(urllib.request.urlopen(base + "/").status, 200)
            self.assertIn(b"waiting", urllib.request.urlopen(base + "/api/status").read())
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
