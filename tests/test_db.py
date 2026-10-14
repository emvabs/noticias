"""Migrations: on connect by default, or explicitly with python -m app.db migrate."""
import pytest

from app import config, db


def test_migrate_command_creates_and_stamps_the_schema(tmp_path, capsys):
    path = tmp_path / "news.db"
    db.main(["migrate", "--db", str(path)])
    assert "schema version" in capsys.readouterr().out
    conn = db.connect(path, run_migrations=False)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


def test_migrate_command_is_safe_to_run_twice(tmp_path):
    path = tmp_path / "news.db"
    db.main(["migrate", "--db", str(path)])
    db.main(["migrate", "--db", str(path)])


def test_without_auto_migrate_an_unmigrated_database_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="python -m app.db migrate"):
        db.connect(tmp_path / "news.db", run_migrations=False)


def test_without_auto_migrate_connect_does_not_migrate(tmp_path, monkeypatch):
    path = tmp_path / "news.db"
    db.connect(path).close()
    monkeypatch.setattr(db, "migrate", lambda conn: pytest.fail("migrated"))
    monkeypatch.setenv("NOTICIAS_AUTO_MIGRATE", "0")
    db.connect(path).execute("SELECT 1 FROM articles")


def test_connect_migrates_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTICIAS_AUTO_MIGRATE", raising=False)
    conn = db.connect(tmp_path / "news.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_memory_databases_always_migrate(monkeypatch):
    monkeypatch.setenv("NOTICIAS_AUTO_MIGRATE", "0")
    db.connect(":memory:").execute("SELECT 1 FROM articles")


@pytest.mark.parametrize("value,expected", [
    (None, True), ("", True), ("1", True), ("true", True),
    ("0", False), ("false", False), ("No", False), ("off", False)])
def test_auto_migrate_setting(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("NOTICIAS_AUTO_MIGRATE", raising=False)
    else:
        monkeypatch.setenv("NOTICIAS_AUTO_MIGRATE", value)
    assert config.auto_migrate() is expected


def test_migrate_turns_unknown_bylines_into_empty_lists(tmp_path):
    """Rows stored before bylines were kept have NULL authors; [] means the same."""
    conn = db.connect(tmp_path / "news.db")
    conn.execute("""INSERT INTO articles (source, title, title_norm, url, published_at, fetched_at,
                    authors) VALUES ('rtp', 'T', 't', 'https://x/1', '2026-10-01T00:00:00Z',
                    '2026-10-01T00:00:00Z', NULL)""")
    conn.execute("""INSERT INTO article_archive (url, source, scope, "group", language,
                    published_at, title, authors) VALUES ('https://x/2', 'rtp', 'portugal',
                    'mainstream', 'pt', '2026-10-01T00:00:00Z', 'T', NULL)""")
    db.migrate(conn)
    assert conn.execute("SELECT authors FROM articles").fetchone()[0] == "[]"
    assert conn.execute("SELECT authors FROM article_archive WHERE url = 'https://x/2'"
                        ).fetchone()[0] == "[]"
