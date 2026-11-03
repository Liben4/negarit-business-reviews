import os
import tempfile
from datetime import timedelta
from types import SimpleNamespace

import pytest

from negarit import create_app
from negarit.cli import ensure_author
from negarit.config import normalize_db_url
from negarit.extensions import db
from negarit.models import Article, Category, Media, Tag, User
from negarit.permissions import ROLES
from negarit.utils import utcnow

PW = "correct-horse-battery"


@pytest.fixture()
def app(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    app = create_app({"SQLALCHEMY_DATABASE_URI": normalize_db_url(url) if url else f"sqlite:///{path}", "TESTING": True,
                      "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False, "STORAGE_PROVIDER": "local", "SCHEMA_GUARD": False,
                      "SITE_URL": "https://negaritbusinessreview.com", "CRON_SECRET": "x" * 24,
                      "SQLALCHEMY_ENGINE_OPTIONS": {}})
    app.instance_path = str(tmp_path)
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
    os.unlink(path)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def w(app):
    """A small newsroom: one user per role, categories, tags, a media row, and an article factory."""
    users = {}
    for role in ROLES:
        u = User(email=f"{role}@example.com", name=role.replace("_", " ").title(), role=role)
        u.set_password(PW)
        db.session.add(u)
        users[role] = u
    db.session.flush()
    authors = {r: ensure_author(u) for r, u in users.items()}
    cats = {}
    for slug, name, primary in (("economy", "Economy", True), ("business", "Business", True), ("opinion", "Opinion", True),
                                ("energy", "Energy", False)):
        c = Category(name=name, slug=slug, is_primary=primary, sort_order=len(cats) * 10)
        db.session.add(c)
        cats[slug] = c
    tags = {n: Tag(name=n, slug=n.lower()) for n in ("Inflation", "Banking")}
    db.session.add_all(tags.values())
    media = Media(filename="cover.webp", alt_text="A cover", caption="Cover caption", credit="Staff", width=1600, height=900,
                  url="https://cdn.example.com/c-1600.webp",
                  variants='{"480": "https://cdn.example.com/c-480.webp", "960": "https://cdn.example.com/c-960.webp", "1600": "https://cdn.example.com/c-1600.webp"}')
    db.session.add(media)
    db.session.commit()
    now = utcnow()

    def art(title, slug=None, status="published", when=None, cat="economy", atype="news", author="editor", tag_names=(),
            featured=False, demo=False, image=True, by="editor", **kw):
        a = Article(title=title, slug=slug or title.lower().replace(" ", "-"), status=status, article_type=atype,
                    published_at=when if when is not None else (now - timedelta(hours=1) if status != "draft" else None),
                    category_id=cats[cat].id if cat else None, author_id=authors[author].id, is_featured=featured, is_demo=demo,
                    created_by_id=users[by].id, subtitle="Deck for " + title, excerpt="Excerpt of " + title,
                    featured_media_id=media.id if image else None, **kw)
        a.set_body(f"Body of {title}. Second paragraph about birr.\n\nThird paragraph.\n\nFourth paragraph.\n\nFifth paragraph.")
        a.tags = [tags[t] for t in tag_names]
        db.session.add(a)
        db.session.commit()
        return a

    return SimpleNamespace(users=users, authors=authors, cats=cats, tags=tags, media=media, art=art, now=now)


def login(client, role="admin", pw=PW, email=None):
    return client.post("/admin/login", data={"email": email or f"{role}@example.com", "password": pw})
