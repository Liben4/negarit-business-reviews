"""Phase 5: SEO, analytics, security details, seeding, and a full admin smoke crawl."""
import re
import xml.dom.minidom as minidom
from datetime import timedelta

import pytest

from negarit.extensions import db
from negarit.models import Advertisement, Article, Category, Comment, ContactMessage, PageView, Page, Subscriber
from negarit.services import analytics, seed
from negarit.utils import slugify

from conftest import login

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


# -------------------------------------------------------------------- SEO feeds
def test_sitemap_contents(client, w):
    real = w.art("Real story", tag_names=["Inflation"])
    w.art("Demo story", demo=True)
    w.art("Draft story", status="draft")
    w.art("Future story", status="scheduled", when=w.now + timedelta(days=1))
    db.session.add(Page(title="About", slug="about", published=True))
    db.session.commit()
    r = client.get("/sitemap.xml")
    body = r.data.decode()
    minidom.parseString(r.data)
    assert r.mimetype == "application/xml"
    base = "https://negaritbusinessreview.com"
    for loc in (f"{base}/economy/real-story", f"{base}/economy<", f"{base}/tag/inflation", f"{base}/about", f"{base}/authors",
                f"{base}/author/{w.authors['editor'].slug}", "https://cdn.example.com/c-1600.webp"):
        assert loc in body, loc
    for gone in ("demo-story", "draft-story", "future-story", "/admin", "/cron", "/search"):
        assert gone not in body, gone


def test_robots_news_sitemap_and_rss(client, w):
    w.art("Fresh news", slug="fresh-news", when=w.now - timedelta(hours=2))
    w.art("Old news", slug="old-news", when=w.now - timedelta(days=5))
    w.art("Demo news", slug="demo-news", demo=True)
    robots = client.get("/robots.txt").data.decode()
    assert "Disallow: /admin" in robots and "/sitemap.xml" in robots and "/news-sitemap.xml" in robots and "Disallow: /ad/" in robots
    news = client.get("/news-sitemap.xml")
    minidom.parseString(news.data)
    assert b"fresh-news" in news.data and b"old-news" not in news.data and b"demo-news" not in news.data and b"<news:title>" in news.data
    rss = client.get("/rss.xml")
    minidom.parseString(rss.data)
    assert rss.mimetype == "application/rss+xml" and b"/economy/fresh-news" in rss.data and b"demo-news" not in rss.data
    assert b"<content:encoded>" in rss.data


def test_preview_deployments_are_never_indexed(w):
    from negarit import create_app
    prev = create_app({"SQLALCHEMY_DATABASE_URI": db.engine.url.render_as_string(hide_password=False), "RATELIMIT_ENABLED": False, "SCHEMA_GUARD": False,
                       "ON_VERCEL": True, "IS_PRODUCTION_DEPLOY": False})
    c = prev.test_client()
    assert "Disallow: /" in c.get("/robots.txt").data.decode()
    assert "noindex" in c.get("/").headers["X-Robots-Tag"]


def test_google_analytics_and_verification_only_when_configured(client, w):
    assert b"data-ga=" not in client.get("/").data and b'id="consent"' not in client.get("/").data
    login(client, "admin")
    client.post("/admin/seo", data={"google_site_verification": "abc123", "ga_measurement_id": "G-TEST12345", "site_description": "Desc",
                                    "default_seo_title": "Title"})
    client.post("/admin/logout")
    r = client.get("/")
    html = r.data.decode()
    assert 'name="google-site-verification" content="abc123"' in html and 'data-ga="G-TEST12345"' in html and 'id="consent"' in html
    assert "googletagmanager.com" in r.headers["Content-Security-Policy"] and "script-src 'self' https://www.googletagmanager.com" in r.headers["Content-Security-Policy"]


def test_seo_admin_audit_lists_gaps(client, w):
    w.art("Needs SEO", image=False)
    login(client, "admin")
    html = client.get("/admin/seo").data.decode()
    assert "No meta description" in html and "No featured image" in html and "Needs SEO" in html


def test_article_seo_fields_are_used(client, w):
    a = w.art("Plain headline", seo_title="Custom SEO Title", meta_description="Custom meta", og_title="Social title",
              og_description="Social desc", canonical_override="https://partner.example/orig")
    html = client.get(a.url_path).data.decode()
    assert "<title>Custom SEO Title | " in html and 'name="description" content="Custom meta"' in html
    assert 'property="og:title" content="Social title"' in html and 'rel="canonical" href="https://partner.example/orig"' in html


def test_heading_hierarchy_single_h1(client, w):
    a = w.art("Structure")
    for path in ("/", a.url_path, "/economy", "/search?q=structure", "/authors"):
        assert len(re.findall(r"<h1[ >]", client.get(path).data.decode())) == 1, path


def test_images_have_dimensions_and_lazy_loading(client, w):
    w.art("Img one", slug="img-one")
    w.art("Img two", slug="img-two")
    html = client.get("/").data.decode()
    imgs = re.findall(r"<img [^>]+>", html)
    assert imgs and all("width=" in i and "height=" in i and "alt=" in i for i in imgs)
    assert 'fetchpriority="high"' in html and 'loading="lazy"' in html


# ------------------------------------------------------------------- analytics
def beacon(client, path, article_id=None, referrer="", ua=UA["User-Agent"]):
    return client.post("/api/pv", json={"p": path, "a": article_id, "r": referrer}, headers={"User-Agent": ua})


def test_page_view_recording(client, w):
    a = w.art("Counted")
    assert beacon(client, a.url_path, a.id, "https://www.google.com/").status_code == 204
    beacon(client, a.url_path, a.id, "https://t.co/xyz")
    beacon(client, a.url_path, a.id, "https://acme.example/post")
    beacon(client, a.url_path, a.id, "")
    beacon(client, a.url_path, a.id, "http://localhost/economy")
    views = PageView.query.order_by(PageView.id).all()
    assert [v.source for v in views] == ["search", "social", "referral", "direct", "internal"]
    art = db.session.get(Article, a.id)
    assert art.view_count == 5 and views[0].category_id == a.category_id and views[0].author_id == a.author_id
    assert "s-maxage" not in client.post("/api/pv", json={}).headers.get("Cache-Control", "")


def test_page_view_filters_bots_hidden_and_private_paths(client, w):
    a, d = w.art("Live"), w.art("Hidden", status="draft")
    beacon(client, a.url_path, a.id, ua="Googlebot/2.1 (+http://www.google.com/bot.html)")
    beacon(client, a.url_path, a.id, ua="")
    beacon(client, d.url_path, d.id)
    beacon(client, "/admin/articles", None)
    beacon(client, "javascript:alert(1)", None)
    beacon(client, a.url_path, "not-an-int")
    assert PageView.query.filter(PageView.article_id.isnot(None)).count() == 0
    assert db.session.get(Article, a.id).view_count == 0


def test_source_classification():
    c = analytics.classify
    assert c("", "x.com") == ("direct", "") and c("https://www.bing.com/search?q=x", "nbr.com")[0] == "search"
    assert c("https://www.linkedin.com/feed", "nbr.com")[0] == "social" and c("https://nbr.com/a", "nbr.com")[0] == "internal"
    assert c("https://www.nbr.com/a", "nbr.com")[0] == "internal" and c("garbage", "nbr.com")[0] == "direct"


def test_analytics_dashboard_and_reports(client, w):
    a = w.art("Popular", author="writer")
    for _ in range(3):
        beacon(client, a.url_path, a.id, "https://google.com/")
    login(client, "editor")
    dash = client.get("/admin/").data.decode()
    assert "Total views" in dash and "Today&#39;s views" in dash or "Today's views" in dash
    page = client.get("/admin/analytics?days=7")
    html = page.data.decode()
    assert page.status_code == 200 and "Popular" in html and "Economy" in html and "Writer" in html and "Search" in html
    assert "<svg" in html and 'style="' not in html  # chart is SVG: no inline styles (CSP)
    client.post("/admin/logout")
    login(client, "writer")
    assert client.get("/admin/analytics").status_code == 403


# ----------------------------------------------------------------- misc / i18n
def test_amharic_slugs_survive():
    assert slugify("ኢትዮጵያ ኢኮኖሚ") == "ኢትዮጵያ-ኢኮኖሚ" and slugify("Birr — new high!") == "birr-new-high"


def test_no_inline_scripts_or_styles_in_public_html(client, w):
    a = w.art("CSP check")
    for path in ("/", a.url_path, "/economy", "/contact", "/search?q=x", "/no-such-page"):
        html = client.get(path).data.decode()
        assert not re.search(r"<script(?![^>]*(src=|application/ld\+json))", html), path
        assert " style=" not in html and " onclick=" not in html, path


# ----------------------------------------------------------------------- seeding
def test_seed_core_is_idempotent(app):
    assert seed.seed_core() == (25, 9) and seed.seed_core() == (0, 0)
    assert Category.query.filter_by(is_primary=True).count() == 6 and Category.query.filter_by(parent_id=None).count() == 13
    assert {p.slug for p in Page.query.all()} == {"about", "privacy-policy", "terms-of-use", "cookie-policy", "editorial-policy", "corrections-policy", "advertise", "write-for-us", "advertising-policy"}


def test_seed_demo_labelled_and_purgeable(client, app):
    seed.seed_core()
    assert seed.seed_demo(images=False) == 16 and seed.seed_demo(images=False) == 0
    a = Article.query.filter_by(is_demo=True).first()
    assert a.title.startswith("SAMPLE") and "Sample content" in a.body_html
    home = client.get("/").data.decode()
    assert "SAMPLE" in home and ">Sample<" in home
    assert client.get("/sitemap.xml").data.count(b"<loc>") < 25 and b"sample-" not in client.get("/sitemap.xml").data
    assert {a.article_type for a in Article.query.all()} >= {"news", "analysis", "opinion", "interview", "explainer", "data-report", "sponsored", "feature"}
    assert seed.purge_demo() == 16 and Article.query.count() == 0


def test_seed_demo_with_images(app):
    seed.seed_core()
    assert seed.seed_demo(images=True) == 16
    from negarit.models import Media
    assert Media.query.count() == 6 and Article.query.first().featured_media.srcset
    seed.purge_demo()
    assert Media.query.count() == 0


# --------------------------------------------------------------- admin smoke crawl
def test_every_admin_page_renders(client, w):
    """Render each admin screen for the roles allowed to see it, with real data present (catches template errors)."""
    a = w.art("Crawl", tag_names=["Inflation"])
    ad = Advertisement(title="Ad", advertiser="X", placement="banner", dest_url="https://x.example", media_id=w.media.id)
    page = Page(title="Legal", slug="legal", published=True)
    db.session.add_all([ad, page, Comment(article_id=a.id, name="N", body="B"), Subscriber(email="s@example.com"),
                        ContactMessage(name="N", email="n@example.com", subject="S", message="M message body")])
    db.session.commit()
    login(client, "super_admin")
    urls = ["/admin/", "/admin/articles", "/admin/articles?status=published&q=cr&type=news", "/admin/articles/new",
            f"/admin/articles/{a.id}/edit", f"/admin/articles/{a.id}/preview", "/admin/categories", f"/admin/categories/{w.cats['economy'].id}",
            "/admin/categories/0", "/admin/tags", f"/admin/tags/{w.tags['Inflation'].id}", "/admin/tags/0", "/admin/authors",
            f"/admin/authors/{w.authors['editor'].id}", "/admin/authors/0", "/admin/media", f"/admin/media/{w.media.id}",
            "/admin/media/picker", "/admin/pages", f"/admin/pages/{page.id}", "/admin/pages/0", "/admin/subscribers",
            "/admin/subscribers?status=pending&q=s", "/admin/comments", "/admin/comments?status=approved", "/admin/messages",
            "/admin/ads", f"/admin/ads/{ad.id}", "/admin/ads/0", "/admin/analytics", "/admin/analytics?days=90", "/admin/seo",
            "/admin/settings", "/admin/users", f"/admin/users/{w.users['writer'].id}", "/admin/users/0", "/admin/logs",
            "/admin/logs?q=login", "/admin/account", "/admin/subscribers.csv"]
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, f"{u} -> {r.status_code}"
        if r.mimetype == "text/html":
            assert b"Traceback" not in r.data and b"jinja2" not in r.data.lower(), u
    assert all(re.search(r'name="csrf-token"', client.get(u).data.decode()) for u in ("/admin/", "/admin/articles/new"))


def test_writer_admin_pages_render(client, w):
    a = w.art("Mine", status="draft", by="writer", author="writer")
    login(client, "writer")
    for u in ("/admin/", "/admin/articles", "/admin/articles/new", f"/admin/articles/{a.id}/edit", "/admin/media", "/admin/account"):
        assert client.get(u).status_code == 200, u
    html = client.get(f"/admin/articles/{a.id}/edit").data.decode()
    assert "Submit for review" in html and "Publish now" not in html and "Schedule" not in html
