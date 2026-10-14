"""Loads config.yaml and sources.yaml from the project root.

Every environment variable the app reads is read here (NOTICIAS_*).
"""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    with open(ROOT / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


FALSE_WORDS = {"0", "false", "no", "off"}


def env_bool(name, default):
    """A yes/no environment variable: "0", "false", "no" or "off" mean no."""
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value not in FALSE_WORDS


def _to_bool(name, value):
    return value.strip().lower() not in FALSE_WORDS


def _to_int(name, value):
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name}={value!r} is not a number") from None


# Environment variables that override config.yaml, so a server can be set up
# without editing the file. Unset (or empty) means config.yaml decides.
ENV_OVERRIDES = {
    "NOTICIAS_DB": (("storage", "database"), None),
    "NOTICIAS_HOST": (("server", "host"), None),
    "NOTICIAS_PORT": (("server", "port"), _to_int),
    "NOTICIAS_RELOAD": (("server", "reload"), _to_bool),
    "NOTICIAS_WORKER": (("worker",), None),
}


def apply_env(cfg, environ=None):
    """config.yaml with the NOTICIAS_* environment variables applied on top."""
    environ = os.environ if environ is None else environ
    for name, (keys, convert) in ENV_OVERRIDES.items():
        value = environ.get(name, "").strip()
        if not value:
            continue
        node = cfg
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = convert(name, value) if convert else value
    return cfg


def load_config():
    return apply_env(_load("config.yaml"))


def auto_migrate():
    """Whether connecting runs the migrations (default) or expects ``python -m app.db migrate``."""
    return env_bool("NOTICIAS_AUTO_MIGRATE", True)


def admin_token():
    """Token that lets non-local requests trigger a fetch; empty = localhost only."""
    return os.environ.get("NOTICIAS_ADMIN_TOKEN", "").strip()


WORKER_MODES = ("embedded", "separate")


def worker_mode():
    """Who runs the scheduled fetch: the web server ("embedded") or ``python -m app.worker``."""
    mode = load_config().get("worker", "embedded")
    if mode not in WORKER_MODES:
        raise ValueError(f"worker: {mode!r} (config.yaml or NOTICIAS_WORKER); use one of {', '.join(WORKER_MODES)}")
    return mode


def load_sources(enabled_only=True):
    sources = _load("sources.yaml").get("sources", [])
    if enabled_only:
        sources = [s for s in sources if s.get("enabled", True) and s.get("feeds")]
    return sources


def source_names():
    """id -> display name of every outlet in sources.yaml, enabled or not."""
    return {s["id"]: s["name"] for s in load_sources(enabled_only=False)}


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
