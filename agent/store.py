"""The agent's SQLite ledger (state/agent.sqlite): two tables only.

  spend     every billed call (LLM tokens, Jevlin searches): who, what, how much
  attempts  every live X post attempt, keyed by attempt_key = hash(race_id, text), so a crash or a
            second run never posts the same text for the same race twice

Slimmed from agentville's store.py (hammadtq/agentville): only the spend and attempts tables.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from agent import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
  run_id TEXT NOT NULL, model TEXT NOT NULL, step TEXT NOT NULL,
  input INTEGER NOT NULL DEFAULT 0, cache_read INTEGER NOT NULL DEFAULT 0,
  cache_write INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0,
  usd REAL NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS spend_run ON spend(run_id);
CREATE INDEX IF NOT EXISTS spend_at ON spend(at);
CREATE TABLE IF NOT EXISTS attempts (
  key TEXT PRIMARY KEY, draft_id TEXT NOT NULL, race_id TEXT NOT NULL, text_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,            -- posting | posted | failed | unknown
  authorized_by TEXT, reply_to TEXT, tweet_id TEXT, url TEXT, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- post-drafter: one row per race it has drafted (so a restart never drafts twice), and an
-- append-only list of drafts made (for the hourly cap)
CREATE TABLE IF NOT EXISTS drafted (
  race_id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, call_key TEXT NOT NULL, drafted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts_made (race_id TEXT NOT NULL, draft_id TEXT NOT NULL, at TEXT NOT NULL);
"""

# Jevlin search spend is recorded too, but the LLM caps only count LLM rows.
NON_LLM_PREFIX = "jevlin"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        """A write. Reads go through _one/_all, which fetch under the same lock: the connection is
        shared by the track's worker threads, and a fetch outside the lock can read another thread's rows."""
        with self._lock:
            return self.conn.execute(sql, args)

    def _one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, args).fetchone()

    def _all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    # spend ----------------------------------------------------------------------------------

    def record_spend(self, run_id: str, model: str, step: str, tokens: dict[str, int], usd: float) -> None:
        self._exec("INSERT INTO spend VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id, model, step, tokens.get("input", 0), tokens.get("cache_read", 0),
                    tokens.get("cache_write", 0), tokens.get("output", 0), usd, now_iso()))

    def _llm_sum(self, where: str, args: tuple) -> float:
        r = self._one(f"SELECT COALESCE(SUM(usd),0) AS s FROM spend WHERE model NOT LIKE ? AND {where}",
                      (NON_LLM_PREFIX + "%", *args))
        return float(r["s"])

    def spend_for_run(self, run_id: str) -> float:
        return self._llm_sum("run_id=?", (run_id,))

    def total_spend(self) -> float:
        return self._llm_sum("1=1", ())

    def spend_since(self, t: datetime) -> float:
        return self._llm_sum("at>=?", (t.astimezone(timezone.utc).isoformat(timespec="seconds"),))

    def jevlin_spend_for_run(self, run_id: str) -> float:
        r = self._one("SELECT COALESCE(SUM(usd),0) AS s FROM spend WHERE model LIKE ? AND run_id=?",
                      (NON_LLM_PREFIX + "%", run_id))
        return float(r["s"])

    def spend_by_step(self, run_id: str, step: str) -> dict:
        """LLM tokens and dollars recorded under one step label (e.g. one track check)."""
        r = self._one("SELECT COUNT(*) AS calls, COALESCE(SUM(input),0) AS input, COALESCE(SUM(cache_read),0) AS "
                       "cache_read, COALESCE(SUM(cache_write),0) AS cache_write, COALESCE(SUM(output),0) AS output, "
                       "COALESCE(SUM(usd),0) AS usd FROM spend WHERE run_id=? AND step=? AND model NOT LIKE ?",
                      (run_id, step, NON_LLM_PREFIX + "%"))
        return dict(r)

    # post-drafter ----------------------------------------------------------------------------

    def drafted(self, race_id: str) -> dict | None:
        r = self._one("SELECT * FROM drafted WHERE race_id=?", (race_id,))
        return dict(r) if r else None

    def record_drafted(self, race_id: str, draft_id: str, call_key: str, *, made: bool = True) -> None:
        now = now_iso()
        self._exec("INSERT INTO drafted VALUES (?,?,?,?) ON CONFLICT(race_id) DO UPDATE SET draft_id=excluded.draft_id, "
                   "call_key=excluded.call_key, drafted_at=excluded.drafted_at", (race_id, draft_id, call_key, now))
        if made:
            self._exec("INSERT INTO drafts_made VALUES (?,?,?)", (race_id, draft_id, now))

    def drafts_made_since(self, t: datetime) -> int:
        r = self._one("SELECT COUNT(*) AS n FROM drafts_made WHERE at>=?",
                      (t.astimezone(timezone.utc).isoformat(timespec="seconds"),))
        return int(r["n"])

    # attempts -------------------------------------------------------------------------------

    def attempt(self, key: str) -> dict | None:
        r = self._one("SELECT * FROM attempts WHERE key=?", (key,))
        return dict(r) if r else None

    def upsert_attempt(self, key: str, *, draft_id: str, race_id: str, text_sha256: str, status: str,
                       **fields) -> None:
        now = now_iso()
        if self.attempt(key) is None:
            self._exec("INSERT INTO attempts (key, draft_id, race_id, text_sha256, status, created_at, updated_at) "
                       "VALUES (?,?,?,?,?,?,?)", (key, draft_id, race_id, text_sha256, status, now, now))
        cols = {"status": status, "updated_at": now, **fields}
        self._exec(f"UPDATE attempts SET {', '.join(f'{k}=?' for k in cols)} WHERE key=?", (*cols.values(), key))

    def posted_for_race(self, race_id: str) -> list[dict]:
        return [dict(r) for r in self._all("SELECT * FROM attempts WHERE race_id=? AND status='posted'",
                                            (race_id,))]
