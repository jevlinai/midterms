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


def model_name(env: str = "MIDTERMS_MODEL") -> str:
    """The model id. Required, no default: the choice is pending with Hammad. "offline" is free."""
    m = (os.environ.get(env) or "").strip()
    if not m and env != "MIDTERMS_MODEL":
        m = (os.environ.get("MIDTERMS_MODEL") or "").strip()
    if not m:
        raise ConfigError(f"{env} is not set. Use 'offline' for a free dry run, or a model id such as "
                          "'claude-haiku-5-5' once the model and its price are approved.")
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
