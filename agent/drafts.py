"""The draft queue (state/drafts/<id>.json) and the human publish gate to X.

The publish-gate ideas are adapted from agentville (hammadtq/agentville),
src/agentville/publishing/workflow.py: `attempt_key`, approval gates, `--by`, and a live switch in
the environment (there AGENTVILLE_PUBLISH_LIVE, here MIDTERMS_PUBLISH_LIVE).

The agent can add drafts (through code that runs the guard first) but has NO tool that publishes.
Publishing happens only in `publish()`, which only the `midterms publish <id> --by NAME` command
calls. Gates, every time:
  1. a person says who they are (--by);
  2. the draft is queued (not rejected, not already published);
  3. the guard passes against the CURRENT state.json (a call can be withdrawn after drafting);
  4. this race+text has never been posted (attempt_key in the SQLite ledger), and no earlier attempt
     is in an unknown state (sent, outcome unknown: a person checks X first);
  5. MIDTERMS_PUBLISH_LIVE=true. Without it, publish() returns the dry-run plan and sends nothing.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from agent import config
from agent.guard import check_post, check_summary_post, weighted_length
from agent.store import Store, now_iso
from agent.xpost import TWEETS_URL, XClient, XError

STATUSES = ("queued", "published", "rejected")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")


class NotAuthorized(Exception):
    """A publishing gate isn't met; nothing was sent."""


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode()).hexdigest()


def attempt_key(race_id: str, text: str) -> str:
    """One key per race and exact text: the same post can only ever go out once."""
    return hashlib.sha256(f"{race_id}\n{text.strip()}".encode()).hexdigest()[:32]


@dataclass
class Outcome:
    status: str  # dry_run | posted | failed | unknown
    detail: str
    plan: dict = field(default_factory=dict)
    tweet_id: str | None = None
    url: str | None = None


class Drafts:
    def __init__(self, directory: Path | None = None, store: Store | None = None):
        self.dir = Path(directory or config.drafts_dir())
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = store or Store()

    # queue ----------------------------------------------------------------------------------

    def path(self, draft_id: str) -> Path:
        if not _ID_RE.match(draft_id):
            raise NotAuthorized(f"bad draft id {draft_id!r}")
        return self.dir / f"{draft_id}.json"

    def get(self, draft_id: str) -> dict:
        p = self.path(draft_id)
        if not p.exists():
            raise NotAuthorized(f"no draft {draft_id}")
        return json.loads(p.read_text())

    def _save(self, d: dict) -> None:
        p = self.path(d["id"])
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, p)

    @staticmethod
    def guard(d: dict, state: dict) -> tuple[bool, list[str]]:
        """The guard for this kind of draft: a race post, or a results-thread post."""
        if d.get("kind") == "thread":
            return check_summary_post(d["text"], state, repo_url=d.get("repo_url"))
        return check_post(d["text"], d["race_id"], state)

    def add(self, race_id: str, text: str, state: dict, *, source: str = "agent",
            reply_to_tweet_id: str | None = None, reply_to_draft: str | None = None, kind: str = "race",
            thread_id: str | None = None, position: int | None = None, of: int | None = None,
            repo_url: str | None = None) -> dict:
        """Queue a draft with its guard result. The same race+text twice returns the existing draft.
        Thread drafts use the thread id in place of race_id."""
        text = text.strip()
        ok, reasons = self.guard({"kind": kind, "text": text, "race_id": race_id, "repo_url": repo_url}, state)
        key = attempt_key(race_id, text)
        draft_id = f"{race_id}-{key[:8]}"
        p = self.path(draft_id)
        if p.exists():
            return json.loads(p.read_text())
        d = {"id": draft_id, "race_id": race_id, "text": text, "source": source,
             "guard": {"ok": ok, "reasons": reasons, "checked_at": now_iso()},
             "status": "queued", "created_at": now_iso(), "attempt_key": key,
             "reply_to_tweet_id": reply_to_tweet_id, "reply_to_draft": reply_to_draft,
             "published": None, "rejected": None, "kind": kind}
        if kind == "thread":
            d.update(thread_id=thread_id, position=position, of=of, repo_url=repo_url)
        self._save(d)
        return d

    def list(self, status: str | None = None) -> list[dict]:
        out = [json.loads(p.read_text()) for p in sorted(self.dir.glob("*.json"))]
        if status:
            out = [d for d in out if d["status"] == status]
        return sorted(out, key=lambda d: d["created_at"])

    def reject(self, draft_id: str, by: str | None, reason: str | None = None) -> dict:
        if not (by or "").strip():
            raise NotAuthorized("say who is rejecting this draft (--by NAME)")
        d = self.get(draft_id)
        if d["status"] == "published":
            raise NotAuthorized(f"{draft_id} is already published ({d['published']['url']}); delete it on X instead")
        d["status"] = "rejected"
        d["rejected"] = {"by": by.strip(), "at": now_iso(), "reason": reason}
        self._save(d)
        return d

    # publish (human only) -------------------------------------------------------------------

    def _reply_target(self, d: dict) -> str | None:
        if d.get("reply_to_tweet_id"):
            return str(d["reply_to_tweet_id"])
        if d.get("reply_to_draft"):
            parent = self.get(d["reply_to_draft"])
            if parent["status"] != "published":
                raise NotAuthorized(f"{d['id']} replies to {parent['id']}, which is not published yet")
            return parent["published"]["tweet_id"]
        return None

    def publish(self, draft_id: str, *, by: str | None, state: dict, live: bool | None = None,
                client: XClient | None = None, retry_unsafe: bool = False) -> Outcome:
        if not (by or "").strip():
            raise NotAuthorized("say who is publishing this post (--by NAME)")
        by = by.strip()
        d = self.get(draft_id)
        if d["status"] == "rejected":
            raise NotAuthorized(f"{draft_id} was rejected by {d['rejected']['by']}")
        if d["status"] == "published":
            raise NotAuthorized(f"{draft_id} is already published: {d['published']['url']}")

        ok, reasons = self.guard(d, state)
        d["guard"] = {"ok": ok, "reasons": reasons, "checked_at": now_iso()}
        self._save(d)
        if not ok:
            raise NotAuthorized("the guard fails against the current state: " + "; ".join(reasons))

        key = attempt_key(d["race_id"], d["text"])
        prev = self.store.attempt(key)
        if prev and prev["status"] == "posted":
            self._mark_published(d, prev["tweet_id"], prev["url"], prev["authorized_by"] or by)
            raise NotAuthorized(f"this text was already posted for {d['race_id']}: {prev['url']}")
        if prev and prev["status"] in ("posting", "unknown") and not retry_unsafe:
            raise NotAuthorized("an earlier attempt may have posted (it stopped after sending). Check "
                                f"x.com/{os.environ.get('MIDTERMS_X_USERNAME', 'jevlinai')} first; if it is not "
                                "there, run publish again with --retry-unsafe")
        reply_to = self._reply_target(d)

        live = config.publish_live() if live is None else live
        plan = {"draft": draft_id, "race_id": d["race_id"], "text": d["text"],
                "weighted_length": weighted_length(d["text"]), "guard": "pass", "attempt_key": key,
                "endpoint": f"POST {TWEETS_URL}", "reply_to_tweet_id": reply_to, "by": by,
                "live_posting_enabled": live}
        if not live:
            return Outcome("dry_run", "dry run: nothing sent. Set MIDTERMS_PUBLISH_LIVE=true to post.", plan)

        client = client or XClient()
        sha = text_sha256(d["text"])
        self.store.upsert_attempt(key, draft_id=draft_id, race_id=d["race_id"], text_sha256=sha, status="posting",
                                  authorized_by=by, reply_to=reply_to, error=None)
        try:
            posted = client.post(d["text"], reply_to=reply_to)
        except XError as e:
            status = "failed" if e.definite else "unknown"
            self.store.upsert_attempt(key, draft_id=draft_id, race_id=d["race_id"], text_sha256=sha, status=status,
                                      error=str(e))
            return Outcome(status, str(e), plan)
        self.store.upsert_attempt(key, draft_id=draft_id, race_id=d["race_id"], text_sha256=sha, status="posted",
                                  tweet_id=posted.tweet_id, url=posted.url, error=None)
        self._mark_published(d, posted.tweet_id, posted.url, by)
        return Outcome("posted", f"live: {posted.url}", plan, posted.tweet_id, posted.url)

    def _mark_published(self, d: dict, tweet_id: str, url: str, by: str) -> None:
        d["status"] = "published"
        d["published"] = {"tweet_id": tweet_id, "url": url, "by": by, "at": now_iso()}
        self._save(d)
