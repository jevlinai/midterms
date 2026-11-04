"""The confirm page: the same confirm tool, as a page a person can use from a phone on election night.

  uv run python confirm/web.py            # serves http://<CONFIRM_WEB_HOST>:<CONFIRM_WEB_PORT>/

It lists closed races with no confirmed call, the call finder's proposal for each (winner, AP's
call time and link, the sentence it quoted), and one button: "I checked AP's page: called for X".
The button runs exactly `confirm.py accept <race> --by <name>`, so every check in confirm.py applies
(AP's own link, a call after polls close, a candidate in the race). A race with no proposal can be
confirmed by hand (winner, time, AP link), and a confirmed call can be retracted.

The page never calls a race itself: a person opens AP's link and presses the button.

Reachable only where it listens: CONFIRM_WEB_HOST (default 127.0.0.1; on the Mac mini, its Tailscale
address). Writes need a same-origin JSON request, so another site open in the same browser can't post.
One request at a time, so two clicks never race on confirmed.json.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from confirm import confirm as C  # noqa: E402

HOST = os.environ.get("CONFIRM_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("CONFIRM_WEB_PORT", "8787"))
PAGE = pathlib.Path(__file__).resolve().parent / "web.html"


STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
AP_BASE = "https://apnews.com/projects/elections-2026/"
OFFICE_ORDER = {"senate": 0, "governor": 1, "house": 2}


def ap_page(race: dict) -> str | None:
    """AP's results page for the race (its URL scheme on election night): one page per state for
    Senate and Governor, and one per state for House, where the district is picked on the page."""
    name = STATE_NAMES.get(race.get("state") or "")
    if not name:
        return None
    slug = name.lower().replace(" ", "-")
    office = {"senate": "us-senate", "governor": "governor", "house": "us-house"}.get(race["office"])
    return f"{AP_BASE}{slug}-{office}-results/" if office else None


def run_cli(argv: list[str]) -> tuple[bool, str]:
    """Run confirm.py's own command; its sys.exit messages come back as errors."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            C.main(argv)
        return True, out.getvalue().strip()
    except SystemExit as e:
        msg = e.code if isinstance(e.code, str) else out.getvalue().strip() or f"exit {e.code}"
        return (e.code in (0, None)), str(msg)


def status(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(C.ET)
    races = C.load_races()
    confirmed = C.load(C.CONFIRMED, {})
    proposals = C.load(C.PROPOSALS, {})
    waiting, called, upcoming = [], [], []
    for r in races.values():
        close = dt.datetime.fromisoformat(r["poll_close_et"])
        c = confirmed.get(r["id"])
        row = {"id": r["id"], "name": r.get("name") or r["id"], "office": r["office"],
               "poll_close_et": r["poll_close_et"], "ap_page": ap_page(r), "district": r.get("district"), "state_name": STATE_NAMES.get(r.get("state") or "", ""),
               "candidates": [{"name": x["name"], "party": x.get("party")} for x in r["candidates"]]}
        if close > now:
            upcoming.append(row)
            continue
        if C.is_called(c, r):
            called.append({**row, "call": c})
            continue
        p = proposals.get(r["id"])
        waiting.append({**row, "proposal": {k: p.get(k) for k in ("winner", "ap_call_time", "ap_url", "quote", "found_at")}
                        if p else None,
                        "minutes_since_close": int((now - close).total_seconds() // 60)})
    # Every proposal first (Senate/Governor before House), then the races with no proposal
    # (Senate/Governor before House), each by poll close.
    def has_prop(x):
        return bool(x["proposal"] and x["proposal"].get("winner"))
    waiting.sort(key=lambda x: (not has_prop(x), OFFICE_ORDER.get(x["office"], 3) > 1, x["poll_close_et"], x["name"]))
    called.sort(key=lambda x: max(x["call"].get("confirmed_at") or [""]), reverse=True)
    upcoming.sort(key=lambda x: x["poll_close_et"])
    nxt = upcoming[0]["poll_close_et"] if upcoming else None
    return {"now": now.isoformat(), "waiting": waiting, "called": called[:30], "called_total": len(called),
            "upcoming_total": len(upcoming), "next_close": nxt,
            "next_close_count": sum(1 for u in upcoming if u["poll_close_et"] == nxt)}


def handle_post(path: str, body: dict) -> tuple[int, dict]:
    by = str(body.get("by") or "").strip()
    rid = str(body.get("race_id") or "").strip()
    if not by:
        return 400, {"ok": False, "message": "type your name first"}
    if not rid:
        return 400, {"ok": False, "message": "no race"}
    if path == "/api/accept":
        argv = ["accept", rid, "--by", by] + (["--time", body["time"]] if body.get("time") else [])
    elif path == "/api/confirm":
        argv = ["confirm", rid, "--winner", str(body.get("winner") or ""), "--time", str(body.get("time") or ""),
                "--url", str(body.get("url") or ""), "--by", by]
    elif path == "/api/retract":
        argv = ["retract", rid, "--by", by, "--reason", str(body.get("reason") or "").strip() or "retracted on the confirm page"]
    else:
        return 404, {"ok": False, "message": "not found"}
    ok, msg = run_cli(argv)
    return (200 if ok else 422), {"ok": ok, "message": msg}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("x-frame-options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._json(200, status())
        else:
            self._json(404, {"ok": False, "message": "not found"})

    def do_POST(self):  # noqa: N802
        # Same-origin JSON only: a form or script on another site can't send this without a CORS
        # preflight, which this server never answers.
        if not (self.headers.get("content-type") or "").startswith("application/json"):
            return self._json(415, {"ok": False, "message": "JSON only"})
        origin = self.headers.get("origin")
        if origin and origin.split("//", 1)[-1] != (self.headers.get("host") or ""):
            return self._json(403, {"ok": False, "message": "cross-origin request refused"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        except ValueError:
            return self._json(400, {"ok": False, "message": "bad JSON"})
        code, obj = handle_post(self.path, body)
        self._json(code, obj)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{dt.datetime.now(C.ET):%H:%M:%S} confirm-web {self.command} {self.path} {fmt % args}\n")


def main():
    srv = HTTPServer((HOST, PORT), Handler)  # one request at a time
    print(f"confirm page on http://{HOST}:{PORT}/", file=sys.stderr, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
