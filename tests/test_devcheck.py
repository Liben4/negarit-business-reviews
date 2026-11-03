"""Local-dev safety net: stale/missing schema gives an actionable page; reset-dev-db rebuilds cleanly."""
from sqlalchemy import inspect, text

from negarit import create_app
from negarit.extensions import db
from negarit.models import Category, Page


def make(tmp_path, **over):
    path = tmp_path / "dev.sqlite"
    return create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}", "SCHEMA_GUARD": True, "TESTING": True,
                       "RATELIMIT_ENABLED": False, "SQLALCHEMY_ENGINE_OPTIONS": {}, **over}), path


def test_guard_explains_missing_schema_then_clears(tmp_path):
    app, _ = make(tmp_path)
    with app.app_context():
        r = app.test_client().get("/")
        assert r.status_code == 503 and b"flask db upgrade" in r.data and b"reset-dev-db" in r.data and b"no migration history" in r.data
        assert app.test_client().get("/static/css/site.css").status_code == 200  # assets still served
        from flask_migrate import upgrade
        upgrade()
        assert app.test_client().get("/").status_code == 200
        assert app.test_client().get("/").status_code == 200  # verified once, no repeat checks


def test_guard_detects_old_schema_version(tmp_path):
    app, _ = make(tmp_path)
    with app.app_context():
        from flask_migrate import upgrade
        upgrade()
        db.session.execute(text("UPDATE alembic_version SET version_num = 'oldprototype1'"))
        db.session.commit()
        app.extensions.pop("_schema_ok", None)
        r = app.test_client().get("/")
        assert r.status_code == 503 and b"oldprototype1" in r.data and b"expects" in r.data


def test_guard_is_off_in_strict_mode():
    from negarit.config import load_config
    assert load_config({"VERCEL": "1", "SECRET_KEY": "s" * 40, "DATABASE_URL": "postgresql://u:p@h/db"})["SCHEMA_GUARD"] is False
    assert load_config({})["SCHEMA_GUARD"] is True


def test_reset_dev_db_rebuilds_a_stale_database(tmp_path):
    app, path = make(tmp_path, STORAGE_PROVIDER="local")
    with app.app_context():
        db.session.execute(text("CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT)"))  # simulate the old prototype DB
        db.session.commit()
        assert not inspect(db.engine).has_table("authors")
        r = app.test_cli_runner().invoke(args=["reset-dev-db", "--yes"])
        assert r.exit_code == 0, r.output
        assert "schema at head, 25 categories, 9 pages" in r.output and "flask create-admin" in r.output
        assert inspect(db.engine).has_table("authors") and Category.query.count() == 25 and Page.query.count() == 9
        app.extensions.pop("_schema_ok", None)
        assert app.test_client().get("/").status_code == 200


def test_reset_dev_db_refuses_non_dev(tmp_path):
    app, _ = make(tmp_path)
    app.config["STRICT"] = True
    with app.app_context():
        r = app.test_cli_runner().invoke(args=["reset-dev-db"])
        assert r.exit_code != 0 and "Refusing" in r.output


def test_default_dev_database_is_anchored_to_project_and_renamed(monkeypatch):
    import os
    from negarit.config import PROJECT_ROOT, load_config
    uri = load_config({})["SQLALCHEMY_DATABASE_URI"]
    assert uri == "sqlite:///" + os.path.join(PROJECT_ROOT, "instance", "nbr-dev.sqlite")  # not dev.sqlite, not cwd-relative
    monkeypatch.chdir("/")
    assert load_config({})["SQLALCHEMY_DATABASE_URI"] == uri
