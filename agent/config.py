"""Paths and settings, all from the environment. Nothing here reads a .env file or prints a secret."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """A required setting is missing or invalid; nothing was sent."""


def _path(env: str, default: Path) -> Path:
    v = os.environ.get(env)
    return Path(v).expanduser() if v else default


def state_path() -> Path:
    return _path("MIDTERMS_STATE", ROOT / "state" / "state.json")


def races_path() -> Path:
    return _path("MIDTERMS_RACES", ROOT / "data" / "races.json")


def drafts_dir() -> Path:
    return _path("MIDTERMS_DRAFTS_DIR", ROOT / "state" / "drafts")


def db_path() -> Path:
    return _path("MIDTERMS_DB", ROOT / "state" / "agent.sqlite")


def proposals_path() -> Path:
    return _path("MIDTERMS_PROPOSALS", ROOT / "state" / "proposals.json")


def call_finder_log_path() -> Path:
    return _path("MIDTERMS_CALL_FINDER_LOG", ROOT / "state" / "call_finder.jsonl")


def model_name(env: str = "MIDTERMS_MODEL") -> str:
    """The model id. Required, no default, so nothing is billed by accident. The approved value for
    the agents is claude-sonnet-5-5 (Hammad, 1 Nov 2026). "offline" is free (tests, dry runs)."""
    m = (os.environ.get(env) or "").strip()
    if not m and env != "MIDTERMS_MODEL":
        m = (os.environ.get("MIDTERMS_MODEL") or "").strip()
    if not m:
        raise ConfigError(f"{env} is not set. Set MIDTERMS_MODEL=claude-sonnet-5-5 "
                          "(the approved model; billed, needs the three caps), or 'offline' for a free dry run.")
    return m


def float_env(name: str) -> float | None:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return None
    try:
        return float(v)
    except ValueError as e:
        raise ConfigError(f"{name} must be a number of US dollars, got {v!r}") from e


def publish_live() -> bool:
    return os.environ.get("MIDTERMS_PUBLISH_LIVE", "").strip().lower() == "true"
