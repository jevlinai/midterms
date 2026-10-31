"""OAuth 1.0a signing and the X v2 client (fake transport only; no real X calls)."""

import httpx
import pytest

from agent.config import ConfigError
from agent.xpost import Credentials, XClient, XError, auth_header, pct, sign, signature_base

# The worked example from X's (Twitter's) "Creating a signature" documentation.
VECTOR = {
    "method": "POST",
    "url": "https://api.twitter.com/1.1/statuses/update.json",
    "params": {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
    "consumer_key": "xvz1evFS4wEEPTGEFPHBog",
    "consumer_secret": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
    "token": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
    "token_secret": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
    "nonce": "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
    "timestamp": "1318622958",
    "signature": "hCtSmYh+iHYCEqBWrE7C7hYmtUk=",
}


def _oauth():
    return {"oauth_consumer_key": VECTOR["consumer_key"], "oauth_nonce": VECTOR["nonce"],
            "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": VECTOR["timestamp"],
            "oauth_token": VECTOR["token"], "oauth_version": "1.0"}


def test_signature_base_string_matches_the_documented_one():
    base = signature_base(VECTOR["method"], VECTOR["url"], {**_oauth(), **VECTOR["params"]})
    assert base == (
        "POST&https%3A%2F%2Fapi.twitter.com%2F1.1%2Fstatuses%2Fupdate.json&include_entities%3Dtrue%26"
        "oauth_consumer_key%3Dxvz1evFS4wEEPTGEFPHBog%26oauth_nonce%3DkYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg%26"
        "oauth_signature_method%3DHMAC-SHA1%26oauth_timestamp%3D1318622958%26oauth_token%3D370773112-"
        "GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb%26oauth_version%3D1.0%26status%3DHello%2520Ladies%2520%252B%2520"
        "Gentlemen%252C%2520a%2520signed%2520OAuth%2520request%2521")


def test_signature_matches_the_known_vector():
    got = sign(VECTOR["method"], VECTOR["url"], {**_oauth(), **VECTOR["params"]}, VECTOR["consumer_secret"],
               VECTOR["token_secret"])
    assert got == VECTOR["signature"]


def test_auth_header_carries_the_vector_signature():
    creds = Credentials(VECTOR["consumer_key"], VECTOR["consumer_secret"], VECTOR["token"], VECTOR["token_secret"])
    h = auth_header(VECTOR["method"], VECTOR["url"], creds, extra_params=VECTOR["params"], nonce=VECTOR["nonce"],
                    timestamp=VECTOR["timestamp"])
    assert h.startswith("OAuth ") and f'oauth_signature="{pct(VECTOR["signature"])}"' in h
    assert VECTOR["consumer_secret"] not in h and VECTOR["token_secret"] not in h


def test_percent_encoding_is_rfc3986():
    assert pct("Ladies + Gentlemen, ~a-b_c.d!*'()") == "Ladies%20%2B%20Gentlemen%2C%20~a-b_c.d%21%2A%27%28%29"


def test_post_sends_v2_json_with_reply(fake_x, x_client):
    p = x_client.post("hello", reply_to="123")
    assert fake_x.bodies == [{"text": "hello", "reply": {"in_reply_to_tweet_id": "123"}}]
    assert p.url == f"https://x.com/jevlinai/status/{p.tweet_id}"
    assert fake_x.requests[0].headers["content-type"] == "application/json"


@pytest.mark.parametrize("fail,definite", [(403, True), (429, True), (503, False),
                                           (httpx.ReadTimeout("t"), False), (httpx.ConnectError("c"), True)])
def test_errors_say_whether_a_retry_is_safe(fake_x, x_client, fail, definite):
    fake_x.fail_with = [fail]
    with pytest.raises(XError) as e:
        x_client.post("hello")
    assert e.value.definite is definite


def test_credentials_come_from_env_and_are_never_shown(monkeypatch):
    with pytest.raises(ConfigError, match="MIDTERMS_X_API_KEY"):
        Credentials.from_env()
    for k, v in {"MIDTERMS_X_API_KEY": "k", "MIDTERMS_X_API_SECRET": "s3cr3t", "MIDTERMS_X_ACCESS_TOKEN": "t",
                 "MIDTERMS_X_ACCESS_SECRET": "t0ps3cr3t"}.items():
        monkeypatch.setenv(k, v)
    c = Credentials.from_env()
    assert "s3cr3t" not in repr(c) and "t0ps3cr3t" not in repr(c)
    assert isinstance(XClient(c, transport=httpx.MockTransport(lambda r: httpx.Response(500))), XClient)
