"""Loads config.yaml and sources.yaml from the project root."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    with open(ROOT / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config():
    return _load("config.yaml")


FALSE_WORDS = {"0", "false", "no", "off"}


def env_bool(name, default):
    """A yes/no environment variable: "0", "false", "no" or "off" mean no."""
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value not in FALSE_WORDS


def auto_migrate():
    """Whether connecting runs the migrations (default) or expects ``python -m app.db migrate``."""
    return env_bool("NOTICIAS_AUTO_MIGRATE", True)


WORKER_MODES = ("embedded", "separate")


def worker_mode():
    """Who runs the scheduled fetch: the web server ("embedded") or ``python -m app.worker``."""
    mode = load_config().get("worker", "embedded")
    if mode not in WORKER_MODES:
        raise ValueError(f"worker: {mode!r} in config.yaml; use one of {', '.join(WORKER_MODES)}")
    return mode


def load_sources(enabled_only=True):
    sources = _load("sources.yaml").get("sources", [])
    if enabled_only:
        sources = [s for s in sources if s.get("enabled", True) and s.get("feeds")]
    return sources


def group_settings(group, *keys, default=None):
    """A per-group setting from config.yaml, falling back to the general value.

    group_settings("independent", "retention_days") -> 120
    group_settings("mainstream", "retention_days")  -> None (use the general one)
    """
    node = load_config().get("groups", {}).get(group or "", {})
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def path(p):
    """Resolve a config path relative to the project root."""
    return ROOT / p
