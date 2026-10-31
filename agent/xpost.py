"""Post to X as @jevlinai: API v2 `POST https://api.x.com/2/tweets`, OAuth 1.0a user context (HMAC-SHA1).

Only the human-run `midterms publish` command uses this; no agent tool can reach it.

Env (never printed): MIDTERMS_X_API_KEY, MIDTERMS_X_API_SECRET, MIDTERMS_X_ACCESS_TOKEN,
MIDTERMS_X_ACCESS_SECRET; MIDTERMS_X_USERNAME (default jevlinai) only shapes the post URL.

The v2 body is JSON, so per RFC 5849 section 3.4.1.3 it is not part of the signature base string;
only the oauth_* parameters (and any query parameters) are signed.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit, parse_qsl

import httpx

from agent.config import ConfigError

TWEETS_URL = "https://api.x.com/2/tweets"
ENV = ("MIDTERMS_X_API_KEY", "MIDTERMS_X_API_SECRET", "MIDTERMS_X_ACCESS_TOKEN", "MIDTERMS_X_ACCESS_SECRET")


class XError(Exception):
    """A post failed. `definite` = X answered with a refusal, so nothing went out and a retry is safe;
    otherwise (timeout, network, 5xx) the post may or may not exist."""

    def __init__(self, msg: str, *, definite: bool, status: int | None = None):
        super().__init__(msg)
        self.definite = definite
        self.status = status


def pct(s: str) -> str:
    """RFC 3986 percent-encoding, as OAuth 1.0a requires (unreserved: A-Z a-z 0-9 - . _ ~)."""
    return quote(str(s), safe="-._~")


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str = field(repr=False)
    access_token: str = field(repr=False)
    access_secret: str = field(repr=False)

    @classmethod
    def from_env(cls) -> "Credentials":
        vals = {k: os.environ.get(k, "").strip() for k in ENV}
        if missing := [k for k, v in vals.items() if not v]:
            raise ConfigError(f"X credentials missing: {', '.join(missing)}")
        return cls(*vals.values())

    def __repr__(self) -> str:  # never show secrets, even in tracebacks
        return "Credentials(<redacted>)"


def signature_base(method: str, url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    base_url = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
    all_params = list(params.items()) + parse_qsl(parts.query, keep_blank_values=True)
    encoded = sorted((pct(k), pct(v)) for k, v in all_params)
    param_str = "&".join(f"{k}={v}" for k, v in encoded)
    return "&".join([method.upper(), pct(base_url), pct(param_str)])


def sign(method: str, url: str, params: dict[str, str], consumer_secret: str, token_secret: str) -> str:
    key = f"{pct(consumer_secret)}&{pct(token_secret)}"
    digest = hmac.new(key.encode(), signature_base(method, url, params).encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def auth_header(method: str, url: str, creds: Credentials, *, extra_params: dict[str, str] | None = None,
                nonce: str | None = None, timestamp: str | None = None) -> str:
    """The Authorization header. `extra_params` are form/query parameters that are signed but not put
    in the header (none for a JSON v2 request; used by the test vector)."""
    oauth = {
        "oauth_consumer_key": creds.api_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": creds.access_token,
        "oauth_version": "1.0",
    }
    oauth["oauth_signature"] = sign(method, url, {**oauth, **(extra_params or {})}, creds.api_secret,
                                    creds.access_secret)
    return "OAuth " + ", ".join(f'{pct(k)}="{pct(v)}"' for k, v in sorted(oauth.items()))


@dataclass
class Posted:
    tweet_id: str
    url: str


class XClient:
    def __init__(self, creds: Credentials | None = None, *, transport: httpx.BaseTransport | None = None,
                 username: str | None = None, timeout: float = 20.0):
        self.creds = creds or Credentials.from_env()
        self.username = username or os.environ.get("MIDTERMS_X_USERNAME", "jevlinai")
        self.http = httpx.Client(transport=transport, timeout=timeout)

    def post(self, text: str, *, reply_to: str | None = None) -> Posted:
        body: dict = {"text": text}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": str(reply_to)}
        headers = {"authorization": auth_header("POST", TWEETS_URL, self.creds), "content-type": "application/json",
                   "user-agent": "jevlin-midterms-agent/1"}
        try:
            r = self.http.post(TWEETS_URL, content=json.dumps(body, ensure_ascii=False).encode(), headers=headers)
        except httpx.TimeoutException as e:
            raise XError(f"timed out talking to X: {type(e).__name__}", definite=False) from None
        except httpx.HTTPError as e:
            # A connect failure means nothing was sent; anything later is unknown.
            definite = isinstance(e, httpx.ConnectError)
            raise XError(f"network error talking to X: {type(e).__name__}", definite=definite) from None
        if r.status_code >= 500:
            raise XError(f"X answered {r.status_code}", definite=False, status=r.status_code)
        if r.status_code not in (200, 201):
            detail = _error_detail(r)
            raise XError(f"X refused the post ({r.status_code}): {detail}", definite=True, status=r.status_code)
        try:
            tid = str(r.json()["data"]["id"])
        except (ValueError, KeyError, TypeError):
            raise XError("X accepted the post but the reply had no id", definite=False, status=r.status_code) from None
        return Posted(tid, f"https://x.com/{self.username}/status/{tid}")


def _error_detail(r: httpx.Response) -> str:
    try:
        j = r.json()
    except ValueError:
        return r.text[:200]
    return str(j.get("detail") or j.get("title") or j.get("errors") or j)[:300]
