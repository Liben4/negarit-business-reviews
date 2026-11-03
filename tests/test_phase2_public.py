"""Phase 2: homepage, categories, article system, search, SEO markup, cacheability."""
from datetime import timedelta

from negarit.extensions import db
from negarit.models import Article, Category, Page
from negarit.services import content
from negarit.utils import slug_taken

from conftest import login


def test_homepage_blocks(client, w):
    w.art("Lead story", featured=True, tag_names=["Inflation"])
    for i in range(6):
        w.art(f"Story {i}", cat="business")
    w.art("An opinion piece", cat="opinion", atype="opinion")
    w.art("A sit-down", atype="interview")
    r = client.get("/")
    html = r.data.decode()
    assert r.status_code == 200 and "Lead story" in html and "Analysis &amp; opinion" in html and "An opinion piece" in html
    assert "Trending" in html and "Most read" in html and "Latest news" in html and "Negarit" in html
    assert 'name="q"' in html and "Stay Ahead of Business Dynamics" in html and "Navigating Business Dynamics" in html
    assert 'href="/news"' in html and 'href="/analysis"' in html and 'href="/opinion"' in html and 'href="/data"' in html
    assert '<details class="more">' in html and "Energy" in html  # secondary categories live under More


def test_empty_homepage_is_ok(client, w):
    assert client.get("/").status_code == 200


def test_article_url_scheme_and_redirects(client, w):
    a = w.art("Birr strengthens", slug="birr-strengthens")
    assert a.url_path == "/economy/birr-strengthens"
    assert client.get("/economy/birr-strengthens").status_code == 200
    r = client.get("/business/birr-strengthens")  # wrong category prefix → canonical
    assert r.status_code == 301 and r.headers["Location"].endswith("/economy/birr-strengthens")
    assert client.get("/article/birr-strengthens").status_code == 301
    assert client.get("/economy/nope").status_code == 404


def test_only_live_articles_are_public(client, w):
    now = w.now
    w.art("Secret draft", status="draft")
    w.art("In review", status="review", when=None)
    w.art("Future", status="scheduled", when=now + timedelta(days=1))
    w.art("Due", status="scheduled", when=now - timedelta(minutes=1))
    for slug in ("secret-draft", "in-review", "future"):
        assert client.get(f"/economy/{slug}").status_code == 404
    assert client.get("/economy/due").status_code == 200  # time-based: live before any cron ran
    assert b"Secret draft" not in client.get("/economy").data and b"Future" not in client.get("/").data


def test_article_page_contents_and_markup(client, w):
    a = w.art("Deep dive", slug="deep-dive", tag_names=["Inflation", "Banking"], atype="analysis",
              sources_html="<p>Source: NBR</p>", cover_caption="Custom caption")
    a.set_sources("Source: **NBR data desk**")
    db.session.commit()
    html = client.get(a.url_path).data.decode()
    assert '<link rel="canonical" href="https://negaritbusinessreview.com/economy/deep-dive">' in html
    assert "AnalysisNewsArticle" in html and "BreadcrumbList" in html and '"keywords"' in html
    assert 'property="og:type" content="article"' in html and 'property="og:image" content="https://cdn.example.com/c-1600.webp"' in html
    assert 'name="twitter:card"' in html and "max-image-preview:large" in html
    assert 'srcset="https://cdn.example.com/c-480.webp 480w' in html and 'width="1600"' in html and 'alt="A cover"' in html
    assert "Deck for Deep dive" in html and "min read" in html and "Custom caption" in html and "Credit:" in html or "credit" in html
    assert 'href="/tag/inflation"' in html and "Sources &amp; attribution" in html and "NBR data desk" in html
    assert "Share this article" in html and "facebook.com/sharer" in html and "About the author" in html
    assert 'data-article="%d"' % a.id in html


def test_updated_date_shown_only_when_meaningfully_later(client, w):
    a = w.art("Fresh", slug="fresh")
    a.updated_at = a.published_at
    db.session.commit()
    assert "Updated" not in client.get(a.url_path).data.decode()
    a.updated_at = a.published_at + timedelta(days=2)
    db.session.commit()
    assert "Updated" in client.get(a.url_path).data.decode()


def test_demo_content_is_labelled_and_noindex(client, w):
    a = w.art("SAMPLE story", slug="sample-story", demo=True)
    html = client.get(a.url_path).data.decode()
    assert 'content="noindex, nofollow"' in html and "Sample content" in html


def test_category_tag_type_author_pages(client, w):
    w.art("E1", tag_names=["Inflation"], atype="opinion", author="writer")
    for path in ("/economy", "/tag/inflation", "/opinion", f"/author/{w.authors['writer'].slug}", "/authors"):
        r = client.get(path)
        assert r.status_code == 200 and b"E1" in r.data or path == "/authors", path
    assert b"Writer" in client.get("/authors").data
    assert client.get("/type/nonsense").status_code == 404 and client.get("/type/opinion").headers["Location"].endswith("/opinion") and client.get("/tag/nope").status_code == 404
    assert b'rel="canonical"' in client.get("/economy?page=1").data


def test_category_pagination(client, w, app):
    app.config["PER_PAGE"] = 3
    for i in range(7):
        w.art(f"Paged {i}", slug=f"paged-{i}", when=w.now - timedelta(hours=i + 1))
    p1, p2 = client.get("/economy").data.decode(), client.get("/economy?page=2").data.decode()
    assert 'rel="next"' in p1 and 'rel="prev"' in p2 and "Paged 0" in p1 and "Paged 3" in p2
    assert client.get("/economy?page=999").status_code == 200


def test_static_pages_and_reserved_slugs(client, w):
    p = Page(title="About Us", slug="about", published=True)
    p.set_body("We cover **business**.")
    hidden = Page(title="Hidden", slug="hidden", published=False)
    db.session.add_all([p, hidden])
    db.session.commit()
    r = client.get("/about")
    assert r.status_code == 200 and b"We cover <strong>business</strong>" in r.data
    assert client.get("/hidden").status_code == 404
    assert slug_taken("admin") and slug_taken("economy") and slug_taken("about") and not slug_taken("brand-new")


def test_search_matches_articles_authors_categories_tags(client, w):
    w.art("Inflation eases", tag_names=["Banking"], author="writer")
    w.art("Unrelated", cat="energy")
    assert b"Inflation eases" in client.get("/search?q=inflation").data          # headline
    assert b"Inflation eases" in client.get("/search?q=Writer").data             # author
    assert b"Inflation eases" in client.get("/search?q=Banking").data            # tag
    assert b"Unrelated" in client.get("/search?q=Energy").data                   # category
    assert b"Also matching" in client.get("/search?q=Banking").data
    assert b"Unrelated" not in client.get("/search?q=inflation&category=economy").data
    assert b"No results" in client.get("/search?q=inflation&type=opinion").data
    assert b"No results" in client.get("/search?q=%25").data                     # wildcard is escaped
    assert client.get("/search").status_code == 200


def test_search_hides_non_public(client, w):
    w.art("Hidden needle", status="draft")
    assert b"Hidden needle" not in client.get("/search?q=needle").data


def test_public_pages_are_cdn_cacheable(client, w):
    a = w.art("Cache me")
    for path in ("/", a.url_path, "/economy", "/sitemap.xml"):
        r = client.get(path)
        assert "s-maxage" in r.headers["Cache-Control"] and "Set-Cookie" not in r.headers and "Cookie" not in r.headers.get("Vary", ""), path
    login(client)
    assert "Cookie" in client.get("/admin/").headers.get("Vary", "")


def test_body_html_is_sanitised(client, w):
    a = w.art("XSS")
    a.set_body("hi <script>alert(1)</script> [x](javascript:alert(1)) <img src=x onerror=alert(1)> <iframe src=//evil></iframe>")
    db.session.commit()
    html = client.get(a.url_path).data.decode()
    assert "<script>alert" not in html and "javascript:alert" not in html and "onerror" not in html and "<iframe" not in html


def test_related_prefers_shared_tags_and_category(app, w):
    base = w.art("Base", tag_names=["Inflation"])
    same_tag = w.art("Same tag", cat="business", tag_names=["Inflation"])
    same_cat = w.art("Same cat")
    other = w.art("Other", cat="energy")
    got = [a.id for a in content.related(base, 3)]
    assert got[0] == same_tag.id and same_cat.id in got and other.id not in got


def test_most_read_uses_recent_views_then_falls_back(app, w):
    a, b = w.art("A"), w.art("B")
    b.view_count = 500
    db.session.commit()
    assert content.most_read(2)[0].id == b.id  # no recent views → all-time counter
    from negarit.models import PageView
    for _ in range(3):
        db.session.add(PageView(path=a.url_path, article_id=a.id))
    db.session.commit()
    assert content.most_read(2)[0].id == a.id  # recent views win
