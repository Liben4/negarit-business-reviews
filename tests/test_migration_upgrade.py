"""Upgrading an EXISTING v2 database must preserve every row and give new columns safe defaults."""
import datetime as dt
import os
import tempfile

import sqlalchemy as sa
from flask_migrate import downgrade, upgrade

from negarit import create_app
from negarit.config import normalize_db_url

V2 = "86462bd9bf17"


def fill(conn, table, **vals):
    """Insert a row into a v2 table, auto-filling any other NOT NULL column by type (v2 had no server defaults)."""
    t = sa.Table(table, sa.MetaData(), autoload_with=conn)
    row = dict(vals)
    for c in t.columns:
        if c.name in row or c.primary_key or c.nullable:
            continue
        ty = c.type
        row[c.name] = (False if isinstance(ty, sa.Boolean) else 0 if isinstance(ty, sa.Integer) else
                       dt.datetime(2026, 1, 1) if isinstance(ty, sa.DateTime) else "")
    return conn.execute(t.insert().values(**row)).inserted_primary_key[0]


def make_app():
    url = os.environ.get("TEST_DATABASE_URL")
    path = tempfile.mkstemp(suffix=".sqlite")[1]
    app = create_app({"SQLALCHEMY_DATABASE_URI": normalize_db_url(url) if url else f"sqlite:///{path}", "TESTING": True,
                      "SCHEMA_GUARD": False, "RATELIMIT_ENABLED": False, "SQLALCHEMY_ENGINE_OPTIONS": {}})
    return app, path, bool(url)


def wipe(engine, is_pg):
    with engine.begin() as c:
        if is_pg:
            c.execute(sa.text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))


def test_upgrade_from_v2_preserves_data_and_applies_defaults():
    app, path, is_pg = make_app()
    with app.app_context():
        from negarit.extensions import db
        wipe(db.engine, is_pg)
        upgrade(revision=V2)  # a database exactly as v2 shipped it
        with db.engine.begin() as c:
            uid = fill(c, "users", email="ed@example.com", name="Ed", password_hash="x", role="editor", active=True)
            aid = fill(c, "authors", name="Ed", slug="ed", user_id=uid, active=True)
            cid = fill(c, "categories", name="Economy", slug="economy", is_primary=True, sort_order=20)
            tid = fill(c, "tags", name="Inflation", slug="inflation")
            a1 = fill(c, "articles", title="Old data story", slug="old-data-story", article_type="data-story", status="published",
                      category_id=cid, author_id=aid, body_html="<p>Body</p>", body_md="Body", published_at=dt.datetime(2026, 5, 1))
            a2 = fill(c, "articles", title="A draft", slug="a-draft", article_type="news", status="draft", category_id=cid, author_id=aid)
            c.execute(sa.text("INSERT INTO article_tags (article_id, tag_id) VALUES (:a, :t)"), {"a": a1, "t": tid})
            fill(c, "subscribers", email="s@example.com", status="confirmed", token="tok")
            fill(c, "advertisements", title="Ad", advertiser="Acme", dest_url="https://a.example", placement="banner", clicks=7)
            fill(c, "contact_messages", name="N", email="n@example.com", subject="S", message="M")
            fill(c, "site_settings", key="site_name", value="Kept Name")

        upgrade()  # -> head: the thing under test

        with db.engine.connect() as c:
            q = lambda sql: c.execute(sa.text(sql)).mappings().all()
            arts = {r["slug"]: r for r in q("SELECT * FROM articles")}
            assert set(arts) == {"old-data-story", "a-draft"}  # nothing lost
            assert arts["old-data-story"]["article_type"] == "data-report"  # renamed type converted
            assert arts["old-data-story"]["body_html"] == "<p>Body</p>" and arts["old-data-story"]["body_md"] == "Body"
            assert not arts["a-draft"]["is_breaking"] and arts["a-draft"]["sponsor_name"] == ""
            assert q("SELECT email, role FROM users")[0]["role"] == "editor"
            assert q("SELECT expertise, slug FROM authors")[0]["expertise"] == ""
            cat = q("SELECT * FROM categories")[0]
            assert cat["name"] == "Economy" and cat["active"] and cat["parent_id"] is None
            tag = q("SELECT * FROM tags")[0]
            assert tag["name"] == "Inflation" and tag["active"] and tag["description"] == ""
            assert q("SELECT COUNT(*) AS n FROM article_tags")[0]["n"] == 1  # relationships intact
            ad = q("SELECT * FROM advertisements")[0]
            assert ad["clicks"] == 7 and ad["impressions"] == 0 and ad["campaign"] == ""
            assert q("SELECT status FROM subscribers")[0]["status"] == "confirmed"
            assert q("SELECT value FROM site_settings")[0]["value"] == "Kept Name"
            for t in ("datasets", "data_points", "reports", "home_sections", "home_slots", "article_revisions", "editorial_notes"):
                assert q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] == 0

        # the migrated database works with the current models (not just the raw tables)
        from negarit.models import Article
        assert Article.query.filter_by(slug="old-data-story").one().type_label == "Data Report"
        assert Article.query.filter_by(slug="old-data-story").one().category.active is True

        db.session.remove()  # release table locks (PostgreSQL DDL would otherwise wait on this session)
        # downgrade is also lossless for pre-existing data
        downgrade(revision=V2)
        with db.engine.connect() as c:
            assert c.execute(sa.text("SELECT article_type FROM articles WHERE slug='old-data-story'")).scalar() == "data-story"
            assert c.execute(sa.text("SELECT COUNT(*) FROM articles")).scalar() == 2
        upgrade()  # and re-upgrades cleanly
        with db.engine.connect() as c:
            assert c.execute(sa.text("SELECT COUNT(*) FROM articles")).scalar() == 2


def test_head_matches_models_no_drift():
    """`flask db check` equivalent: the migrations produce exactly the schema the models declare."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    app, path, is_pg = make_app()
    with app.app_context():
        from negarit.extensions import db
        wipe(db.engine, is_pg)
        upgrade()
        with db.engine.connect() as c:
            diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": False}), db.metadata)
        assert diff == [], diff
