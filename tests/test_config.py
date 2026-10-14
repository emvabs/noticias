"""NOTICIAS_* environment variables override config.yaml."""
import pytest

from app import config, db


def base():
    return {"server": {"host": "127.0.0.1", "port": 8000, "reload": True},
            "storage": {"database": "data/news.db", "retention_days": 14}}


def test_no_variables_keep_config_yaml():
    assert config.apply_env(base(), {}) == base()


def test_empty_variables_are_ignored():
    env = {name: "" for name in config.ENV_OVERRIDES}
    assert config.apply_env(base(), env) == base()


def test_each_variable_overrides_its_setting():
    cfg = config.apply_env(base(), {
        "NOTICIAS_DB": "/var/lib/noticias/news.db", "NOTICIAS_HOST": "0.0.0.0",
        "NOTICIAS_PORT": "9000", "NOTICIAS_RELOAD": "0", "NOTICIAS_WORKER": "separate"})
    assert cfg["storage"] == {"database": "/var/lib/noticias/news.db", "retention_days": 14}
    assert cfg["server"] == {"host": "0.0.0.0", "port": 9000, "reload": False}
    assert cfg["worker"] == "separate"


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("yes", True),
                                            ("0", False), ("False", False), ("off", False)])
def test_reload_reads_yes_and_no(value, expected):
    assert config.apply_env(base(), {"NOTICIAS_RELOAD": value})["server"]["reload"] is expected


def test_a_bad_port_names_the_variable():
    with pytest.raises(ValueError, match="NOTICIAS_PORT"):
        config.apply_env(base(), {"NOTICIAS_PORT": "eighty"})


def test_load_config_applies_the_environment(monkeypatch):
    monkeypatch.setenv("NOTICIAS_PORT", "9123")
    monkeypatch.setenv("NOTICIAS_WORKER", "separate")
    assert config.load_config()["server"]["port"] == 9123
    assert config.worker_mode() == "separate"


def test_the_database_path_reaches_connect(tmp_path, monkeypatch):
    path = tmp_path / "elsewhere" / "news.db"
    monkeypatch.setenv("NOTICIAS_DB", str(path))
    db.connect().close()
    assert path.exists()


def test_sources_come_with_their_defaults(monkeypatch):
    monkeypatch.setattr(config, "_load", lambda name: {"sources": [
        {"id": "a", "name": "A", "feeds": ["https://a"]},
        {"id": "b", "name": "B", "feeds": ["https://b"], "scope": "world", "language": "en"}]})
    a, b = config.load_sources()
    assert (a["scope"], a["language"], a["group"], a["author_from"], a["summary_from"]) == (
        "portugal", "pt", "mainstream", "feed", "feed")
    assert (b["scope"], b["language"], b["group"]) == ("world", "en", "mainstream")
