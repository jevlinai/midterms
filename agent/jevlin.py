"""A small Jevlin client for the agent: `POST /v1/search` and `POST /v1/fetch` only.

Same patterns as poller/poller.py: Bearer auth, an Idempotency-Key on search so a retry is replayed
rather than billed twice, one retry on 429/5xx honouring Retry-After (capped).

The agent uses its own key, MIDTERMS_AGENT_JEVLIN_KEY, so its traffic never mixes with the
measurement; it falls back to ~/.jevlin/credentials.json. The key is never printed or logged.
"""

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from agent.config import ConfigError

BASE_URL = "https://api.jevlin.ai"
USER_AGENT = "jevlin-midterms-agent/1"


class JevlinError(Exception):
    def __init__(self, msg: str, status: int | None = None, code: str | None = None):
        super().__init__(msg)
        self.status, self.code = status, code


def load_key() -> str:
    key = os.environ.get("MIDTERMS_AGENT_JEVLIN_KEY", "").strip()
    if key:
        return key
    path = Path(os.environ.get("JEVLIN_CREDENTIALS", "~/.jevlin/credentials.json")).expanduser()
    try:
        key = json.loads(path.read_text())["api_key"]
    except (OSError, ValueError, KeyError) as e:
        raise ConfigError(f"no Jevlin key: set MIDTERMS_AGENT_JEVLIN_KEY or log in with the jevlin CLI "
                          f"({type(e).__name__})") from None
    return key


class JevlinClient:
    def __init__(self, key: str | None = None, *, base_url: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 60.0, max_wait_s: float = 30.0):
        self._key = key or load_key()
        self.base_url = (base_url or os.environ.get("MIDTERMS_JEVLIN_URL") or BASE_URL).rstrip("/")
        self.http = httpx.Client(transport=transport, timeout=timeout)
        self.max_wait_s = max_wait_s

    def __repr__(self) -> str:
        return f"JevlinClient({self.base_url}, key=<redacted>)"

    def _post(self, path: str, body: dict, idempotency_key: str | None = None) -> dict:
        headers = {"authorization": f"Bearer {self._key}", "content-type": "application/json",
                   "user-agent": USER_AGENT}
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key
        last: JevlinError | None = None
        for attempt in (1, 2):
            try:
                r = self.http.post(self.base_url + path, content=json.dumps(body).encode(), headers=headers)
            except httpx.HTTPError as e:
                last = JevlinError(f"{path}: {type(e).__name__}")
                if attempt == 1:
                    time.sleep(2)
                    continue
                raise last from None
            if r.status_code == 200:
                return r.json()
            code = None
            try:
                code = r.json().get("code")
            except ValueError:
                pass
            last = JevlinError(f"{path}: http {r.status_code} {code or ''}".strip(), r.status_code, code)
            if attempt == 1 and (r.status_code == 429 or r.status_code >= 500 or code == "idempotency_in_progress"):
                wait = min(float(r.headers.get("retry-after") or 5), self.max_wait_s)
                time.sleep(wait)
                continue
            raise last
        raise last or JevlinError(path)

    def search(self, body: dict, *, idempotency_key: str | None = None) -> dict:
        return self._post("/v1/search", body, idempotency_key)

    def fetch(self, urls: list[str], *, max_chars: int = 10000, query: str | None = None) -> dict:
        body: dict = {"pages": [{"url": u} for u in urls[:5]], "max_chars": max_chars}
        if query:
            body["query"] = query
        return self._post("/v1/fetch", body)


def cost_usd(resp: dict) -> float:
    return float(((resp or {}).get("usage") or {}).get("cost_micros") or 0) / 1e6


ET = timezone(timedelta(hours=-5))  # EST: DST ended 1 Nov 2026


def budget_since(scope: str | None, since: str | None, *, today: date | None = None) -> datetime | None:
    """When a command's Jevlin budget starts counting.

    scope "run" (the default without --budget-since): this process only -> None.
    scope "day" (the default with --budget-since): all of this command's Jevlin spend in the ledger since
    00:00 ET on --budget-since (or today in ET), across runs, so a restart continues the same budget."""
    scope = scope or ("day" if since else "run")
    if scope not in ("run", "day"):
        raise ConfigError("--budget-scope must be run or day")
    if scope == "run":
        if since:
            raise ConfigError("--budget-since needs --budget-scope day")
        return None
    try:
        d = date.fromisoformat(since) if since else (today or datetime.now(ET).date())
    except ValueError:
        raise ConfigError(f"--budget-since must be YYYY-MM-DD, got {since!r}") from None
    return datetime(d.year, d.month, d.day, tzinfo=ET)
