"""Shared fixtures. No test touches the network or calls an LLM: sockets are blocked, X and Jevlin are
httpx.MockTransport fakes, and the model is OfflinePlanModel.

state/example-state.json is SYNTHETIC (fake races and candidates). The only real-world data is the
recorded Jevlin response for a PAST race (Virginia governor, 2025) in tests/fixtures/."""

import copy
import json
import socket
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLE_STATE = ROOT / "state" / "example-state.json"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test gets its own ledger, queue and proposals file, and no live switches or secrets."""
    monkeypatch.setenv("MIDTERMS_DB", str(tmp_path / "agent.sqlite"))
    monkeypatch.setenv("MIDTERMS_DRAFTS_DIR", str(tmp_path / "drafts"))
    monkeypatch.setenv("MIDTERMS_PROPOSALS", str(tmp_path / "proposals.json"))
    monkeypatch.setenv("MIDTERMS_CALL_FINDER_LOG", str(tmp_path / "call_finder.jsonl"))
    monkeypatch.setenv("MIDTERMS_STATE", str(EXAMPLE_STATE))
    monkeypatch.setenv("MIDTERMS_AUTOPOST_STOP", str(tmp_path / "STOP_POSTING"))
    monkeypatch.setenv("MIDTERMS_AUTOPOST_LAST_TRY", str(tmp_path / "autopost-last-try"))
    for k in ("MIDTERMS_AUTOPOST_ANCHOR", "MIDTERMS_AUTOPOST_GAP_SECONDS", "MIDTERMS_AUTOPOST_MAX", "MIDTERMS_AUTOPOST_OFFICES"):
        monkeypatch.delenv(k, raising=False)
    for k in ("MIDTERMS_PUBLISH_LIVE", "MIDTERMS_MODEL", "MIDTERMS_CALL_FINDER_MODEL", "ANTHROPIC_API_KEY",
              "MIDTERMS_RUN_CAP_USD", "MIDTERMS_DAILY_CAP_USD", "MIDTERMS_TOTAL_CAP_USD",
              "MIDTERMS_X_API_KEY", "MIDTERMS_X_API_SECRET", "MIDTERMS_X_ACCESS_TOKEN", "MIDTERMS_X_ACCESS_SECRET",
              "MIDTERMS_AGENT_JEVLIN_KEY", "MIDTERMS_REPO_URL", "MIDTERMS_THREAD_NOTES"):
        monkeypatch.delenv(k, raising=False)

    def no_network(*a, **kw):
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    return tmp_path


@pytest.fixture
def state() -> dict:
    return json.loads(EXAMPLE_STATE.read_text())


def called_with_lead(state: dict) -> dict:
    """A called race whose first engine is not Serper and is ahead of Serper by > 0 minutes."""
    for r in state["races"]:
        if r["status"] == "called" and r["first"] and "serper" not in r["first"]:
            lead = r["engines"][r["first"][0]].get("lead_vs_serper")
            if isinstance(lead, (int, float)) and lead > 0:
                return r
    raise AssertionError("example state has no called race led by a non-Serper engine")


@pytest.fixture
def led_race(state) -> dict:
    return called_with_lead(state)


def uncalled(state: dict) -> dict:
    return next(r for r in state["races"] if r["status"] == "closed")


@pytest.fixture
def store():
    from agent.store import Store

    s = Store()
    yield s
    s.close()


class FakeX:
    """Answers like X API v2 POST /2/tweets and records every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.next_id = 1850000000000000001
        self.fail_with: list[object] = []  # an int status to return, or an exception to raise

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert req.method == "POST" and str(req.url) == "https://api.x.com/2/tweets"
        assert req.headers["authorization"].startswith("OAuth ")
        if self.fail_with:
            f = self.fail_with.pop(0)
            if isinstance(f, Exception):
                raise f
            return httpx.Response(f, json={"title": "Forbidden", "detail": "duplicate content"})
        body = json.loads(req.content)
        tid = str(self.next_id)
        self.next_id += 1
        return httpx.Response(201, json={"data": {"id": tid, "text": body["text"]}})

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def fake_x():
    return FakeX()


@pytest.fixture
def x_client(fake_x):
    from agent.xpost import Credentials, XClient

    return XClient(Credentials("ck", "cs", "at", "as"), transport=httpx.MockTransport(fake_x))


def mutate(state: dict, race_id: str, fn) -> dict:
    s = copy.deepcopy(state)
    fn(next(r for r in s["races"] if r["id"] == race_id))
    return s
