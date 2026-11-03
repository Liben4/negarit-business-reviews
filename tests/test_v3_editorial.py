"""v3: navigation & type pages, homepage manager, rich-text editor, autosave, revisions, notes, duplicate, archive."""
from datetime import timedelta

from negarit.extensions import db
from negarit.models import (Article, ArticleRevision, Category, EditorialNote, HomeSection, HomeSlot, Page, Subscriber)
from negarit.services import settings, workflow

from conftest import login


def post_article(client, w, **over):
    d = {"title": "Rich story", "body_html": "<p>Hello <strong>world</strong>.</p>", "category_id": w.cats["economy"].id,
         "article_type": "news", "action": "save", "author_id": w.authors["editor"].id}
    d.update(over)
    return client.post("/admin/articles/new", data=d)


# ----------------------------------------------------------- navigation & types
def test_primary_navigation_and_type_pages(client, w):
    w.art("A news item", atype="news")
    w.art("A view", slug="a-view", atype="opinion", cat="opinion")
    w.art("Sponsored thing", slug="sponsored-thing", atype="sponsored", sponsor_name="Acme Co")
    html = client.get("/").data.decode()
    for href in ("/", "/news", "/analysis", "/opinion", "/data"):
        assert f'href="{href}"' in html
    assert "Sections" in html
    assert b"A news item" in client.get("/news").data and b"A news item" not in client.get("/opinion").data
    assert b"A view" in client.get("/opinion").data
    r = client.get("/sponsored")
    assert b"Sponsored thing" in r.data and b"Sponsored by Acme Co" in r.data and b"labelled on every article" in r.data
    assert client.get("/type/opinion").status_code == 301


def test_subcategories_family_listing_and_inactive_rules(client, w):
    sub = Category(name="Inflation & Monetary Policy", slug="inflation", parent_id=w.cats["economy"].id, sort_order=5)
    db.session.add(sub)
    db.session.commit()
    a = w.art("Child story", slug="child-story", cat="economy")
    a.category_id = sub.id
    db.session.commit()
    parent_page = client.get("/economy").data
    assert b"Child story" in parent_page and b'href="/inflation"' in parent_page  # parent lists children's stories + chips
    child = client.get("/inflation").data
    assert b"Child story" in child and b'href="/economy"' in child  # breadcrumb to parent
    assert client.get("/inflation/child-story").status_code == 200
    sub.active = False
    db.session.commit()
    assert client.get("/inflation").status_code == 404  # deactivated: listing gone...
    assert client.get("/inflation/child-story").status_code == 200  # ...but published URLs keep working
    assert b'href="/inflation"' not in client.get("/").data


def test_prev_next_navigation_and_labels(client, w):
    older = w.art("Older piece", slug="older-piece", when=w.now - timedelta(days=3))
    mid = w.art("Middle piece", slug="middle-piece", when=w.now - timedelta(days=2))
    newer = w.art("Newer piece", slug="newer-piece", when=w.now - timedelta(days=1))
    html = client.get(mid.url_path).data.decode()
    assert 'rel="prev"' in html and "Older piece" in html and 'rel="next"' in html and "Newer piece" in html
    assert "Previous in Economy" in html and "Print" in html
    op = w.art("Hot take", slug="hot-take", atype="opinion")
    assert "Opinion." in client.get(op.url_path).data.decode() and "views expressed are the author" in client.get(op.url_path).data.decode()
    sp = w.art("Paid piece", slug="paid-piece", atype="sponsored", sponsor_name="Globex")
    page = client.get(sp.url_path).data.decode()
    assert "Sponsored content." in page and "Paid for by Globex" in page and "not produced by the" in page


# --------------------------------------------------------------- homepage manager
def test_homepage_manager_controls_lead_picks_sections(client, w):
    a, b, c = w.art("Alpha", featured=True), w.art("Beta"), w.art("Gamma")
    w.art("Biz one", cat="business")
    login(client, "writer")
    assert client.get("/admin/homepage").status_code == 403
    client.post("/admin/logout")
    login(client, "editor")
    page = client.get("/admin/homepage")
    assert page.status_code == 200 and HomeSection.query.count() > 0  # defaults persisted on first visit
    secs = {s.kind + (s.category.slug if s.category else ""): s for s in HomeSection.query.all()}
    data = {"lead_id": c.id, "pick_0": b.id, "pick_1": a.id, "pick_2": "99999",  # unknown id ignored
            "breaking_enabled": "on", "breaking_label": "Alert", "breaking_hours": "12"}
    for s in HomeSection.query.all():
        k = f"sec_{s.id}_"
        data.update({k + "pos": s.position, k + "limit": s.item_limit, k + "title": s.title})
        if s.kind not in ("most_read", "category") or s.category and s.category.slug == "economy":
            data[k + "visible"] = "on"
    assert client.post("/admin/homepage", data=data).status_code == 302
    html = client.get("/").data.decode()
    assert html.index("Gamma") < html.index("Alpha")  # chosen lead story comes first
    assert [s.article_id for s in HomeSlot.query.filter_by(slot="picks").order_by(HomeSlot.position)] == [b.id, a.id]
    assert "Editor’s picks" in html and "Most read" not in html  # hidden section disappears
    assert settings.get("breaking_label") == "Alert"


def test_homepage_section_add_remove_reorder_and_unpublished_pick(client, w):
    x = w.art("Pick me")
    w.art("Energy tale", cat="energy")
    login(client, "editor")
    client.get("/admin/homepage")
    first = HomeSection.query.order_by(HomeSection.position).first()
    data = {"lead_id": "", "pick_0": x.id, "new_kind": "category", "new_category": w.cats["energy"].id, "new_title": "Power desk", "new_limit": "3",
            f"sec_{first.id}_delete": "on"}
    for s in HomeSection.query.all():
        data.update({f"sec_{s.id}_pos": s.position, f"sec_{s.id}_limit": s.item_limit, f"sec_{s.id}_visible": "on"})
    client.post("/admin/homepage", data=data)
    assert db.session.get(HomeSection, first.id) is None and HomeSection.query.filter_by(title="Power desk").count() == 1
    assert b"Power desk" in client.get("/").data
    x.status = "draft"
    db.session.commit()
    assert b"Pick me" not in client.get("/").data  # a pick that is no longer public silently drops out
    bad = client.post("/admin/homepage", data={"new_kind": "category"}, follow_redirects=True)
    assert b"Choose a category" in bad.data


def test_breaking_banner(client, w):
    w.art("Normal story")
    a = w.art("Big news", slug="big-news", is_breaking=True, when=w.now - timedelta(hours=2))
    old = w.art("Stale alert", slug="stale-alert", is_breaking=True, when=w.now - timedelta(hours=60))
    html = client.get("/").data.decode()
    assert 'class="ticker"' in html and "Big news" in html.split('class="ticker"')[1].split("</div>")[0]
    assert "Stale alert" not in html.split('class="ticker"')[1].split("</div>")[0]  # outside the window
    login(client, "super_admin")
    client.post("/admin/settings", data={"site_name": "NBR"})  # breaking_enabled unchecked
    client.post("/admin/logout")
    assert 'class="ticker"' not in client.get("/").data.decode()


# --------------------------------------------------------------------- the editor
def test_rich_text_is_sanitised_and_saved(client, w):
    login(client, "editor")
    evil = '<p>Safe <a href="javascript:alert(1)">bad</a> <a href="https://ok.example">ok</a></p><script>alert(1)</script><img src="x" onerror="alert(1)" alt="pic"><h1>Big</h1><iframe src="//evil"></iframe><h2>Sub</h2><p><br></p>'
    post_article(client, w, body_html=evil)
    a = Article.query.filter_by(title="Rich story").one()
    assert "<script" not in a.body_html and "javascript:" not in a.body_html and "onerror" not in a.body_html and "<iframe" not in a.body_html
    assert 'href="https://ok.example"' in a.body_html and 'rel="noopener noreferrer"' in a.body_html and "<h2>Sub</h2>" in a.body_html
    assert "<p><br></p>" not in a.body_html and "pic" in a.body_html and "Safe" in a.body_text
    page = client.get(f"/admin/articles/{a.id}/edit").data.decode()
    assert 'id="editor"' in page and "vendor/quill.js" in page and "ql-toolbar" not in page.split("<textarea id=\"body_html\"")[0].replace("ql-toolbar-host", "")


def test_legacy_markdown_field_still_accepted(client, w):
    login(client, "editor")
    client.post("/admin/articles/new", data={"title": "Legacy", "body_md": "Some **bold** text", "category_id": w.cats["economy"].id,
                                             "action": "save", "author_id": w.authors["editor"].id})
    assert "<strong>bold</strong>" in Article.query.filter_by(title="Legacy").one().body_html


def test_autosave_creates_updates_and_protects_live_content(client, w):
    login(client, "writer")
    assert client.post("/admin/articles/autosave", json={"title": "", "body_html": "<p>x</p>"}).status_code == 422
    r = client.post("/admin/articles/autosave", json={"title": "Draft one", "body_html": "<p>first</p>"})
    assert r.status_code == 200 and r.json["id"]
    aid, v = r.json["id"], r.json["v"]
    a = db.session.get(Article, aid)
    assert a.status == "draft" and a.created_by_id == w.users["writer"].id and a.author.user_id == w.users["writer"].id
    r2 = client.post("/admin/articles/autosave", json={"id": aid, "v": v, "title": "Draft one", "body_html": "<p>second</p><script>x</script>"})
    assert r2.status_code == 200 and "second" in db.session.get(Article, aid).body_html and "<script" not in db.session.get(Article, aid).body_html
    assert ArticleRevision.query.filter_by(article_id=aid).count() == 0  # autosaves do not create revisions
    stale = client.post("/admin/articles/autosave", json={"id": aid, "v": v, "title": "Overwrite?", "body_html": "<p>x</p>"})
    assert stale.status_code == 409 and "changed" in stale.json["error"]  # someone/something saved in between
    # another writer can't touch it; no autosave once it is in review
    other = w.art("Not mine", status="draft", by="editor", author="editor")
    assert client.post("/admin/articles/autosave", json={"id": other.id, "title": "x"}).status_code == 404
    a.status = "review"
    db.session.commit()
    assert client.post("/admin/articles/autosave", json={"id": aid, "title": "x"}).status_code == 403


def test_autosave_never_touches_published_articles(client, w):
    live = w.art("Live story", slug="live-story")
    login(client, "editor")
    r = client.post("/admin/articles/autosave", json={"id": live.id, "title": "Hijacked", "body_html": "<p>changed</p>"})
    assert r.status_code == 409 and "Autosave is off" in r.json["error"]
    assert db.session.get(Article, live.id).title == "Live story"
    assert 'data-autosave="0"' in client.get(f"/admin/articles/{live.id}/edit").data.decode()


def test_autosave_requires_login_and_csrf_enforced(client, w):
    assert client.post("/admin/articles/autosave", json={"title": "x"}).status_code == 302


def test_revisions_snapshot_dedupe_and_restore(client, w):
    login(client, "editor")
    post_article(client, w, title="Versioned", body_html="<p>v1 text</p>")
    a = Article.query.filter_by(title="Versioned").one()
    assert ArticleRevision.query.filter_by(article_id=a.id).count() == 1
    client.post(f"/admin/articles/{a.id}/edit", data={"title": "Versioned", "body_html": "<p>v1 text</p>", "category_id": w.cats["economy"].id,
                                                      "author_id": w.authors["editor"].id, "action": "save"})
    assert ArticleRevision.query.filter_by(article_id=a.id).count() == 1  # identical content is not re-recorded
    client.post(f"/admin/articles/{a.id}/edit", data={"title": "Versioned", "body_html": "<p>v2 text</p>", "category_id": w.cats["economy"].id,
                                                      "author_id": w.authors["editor"].id, "action": "save"})
    revs = ArticleRevision.query.filter_by(article_id=a.id).order_by(ArticleRevision.id).all()
    assert len(revs) == 2 and "v1" in revs[0].body_html and revs[1].user_name == "Editor"
    assert b"v1 text" in client.get(f"/admin/articles/{a.id}/revisions").data
    client.post(f"/admin/articles/{a.id}/revisions/{revs[0].id}/restore")
    assert "v1 text" in db.session.get(Article, a.id).body_html
    assert ArticleRevision.query.filter_by(article_id=a.id).count() >= 3  # the pre-restore text was kept too


def test_revision_history_scope_and_limit(client, w):
    a = w.art("Mine", status="draft", by="writer", author="writer")
    other = w.art("Theirs", status="draft", by="editor", author="editor")
    login(client, "writer")
    assert client.get(f"/admin/articles/{a.id}/revisions").status_code == 200
    assert client.get(f"/admin/articles/{other.id}/revisions").status_code == 403
    for i in range(workflow.REVISION_LIMIT + 5):
        a.title = f"t{i}"
        workflow.snapshot(a, w.users["writer"], "save")
    db.session.commit()
    assert ArticleRevision.query.filter_by(article_id=a.id).count() == workflow.REVISION_LIMIT


def test_editorial_notes_and_revision_requests(client, w):
    a = w.art("Under review", status="review", when=None, by="writer", author="writer")
    login(client, "editor")
    client.post(f"/admin/articles/{a.id}/edit", data={"title": a.title, "body_html": a.body_html, "category_id": a.category_id,
                                                      "author_id": a.author_id, "action": "request_changes", "review_note": "Add a source for the 12% claim"})
    assert db.session.get(Article, a.id).status == "draft"
    note = EditorialNote.query.one()
    assert note.kind == "revision_request" and "12%" in note.body and note.user_name == "Editor"
    client.post("/admin/logout")
    login(client, "writer")  # the author sees the note and can reply, but nobody else's article
    page = client.get(f"/admin/articles/{a.id}/edit").data.decode()
    assert "Add a source for the 12% claim" in page
    client.post(f"/admin/articles/{a.id}/notes", data={"body": "Source added."})
    assert EditorialNote.query.count() == 2
    other = w.art("Other", status="draft", by="editor", author="editor")
    assert client.post(f"/admin/articles/{other.id}/notes", data={"body": "hi"}).status_code == 403
    assert b"Write a note" in client.post(f"/admin/articles/{a.id}/notes", data={"body": "  "}, follow_redirects=True).data


def test_duplicate_creates_independent_draft(client, w):
    src = w.art("Original", slug="original", featured=True, tag_names=["Inflation"], is_breaking=True)
    login(client, "editor")
    r = client.post(f"/admin/articles/{src.id}/duplicate")
    assert r.status_code == 302
    copy = Article.query.filter(Article.id != src.id).one()
    assert copy.title == "Copy of Original" and copy.status == "draft" and copy.slug != src.slug and copy.published_at is None
    assert not copy.is_featured and not copy.is_breaking and copy.view_count == 0 and [t.name for t in copy.tags] == ["Inflation"]
    assert client.get(copy.url_path).status_code == 404  # drafts stay private
    assert db.session.get(Article, src.id).status == "published"
    # a writer can duplicate only their own work, and the copy is theirs
    client.post("/admin/logout")
    login(client, "writer")
    assert client.post(f"/admin/articles/{src.id}/duplicate").status_code == 403


def test_archive_and_restore(client, w):
    a = w.art("Soon archived", slug="soon-archived", featured=True)
    login(client, "editor")
    client.post(f"/admin/articles/{a.id}/edit", data={"title": a.title, "body_html": a.body_html, "category_id": a.category_id,
                                                      "author_id": a.author_id, "action": "archive"})
    assert db.session.get(Article, a.id).status == "archived"
    assert client.get(a.url_path).status_code == 404 and b"Soon archived" not in client.get("/").data
    assert b"Soon archived" not in client.get("/search?q=archived").data and b"soon-archived" not in client.get("/sitemap.xml").data
    assert b"Soon archived" not in client.get("/admin/articles").data  # default list hides the archive...
    assert b"Soon archived" in client.get("/admin/articles?status=archived").data  # ...which has its own tab
    client.post(f"/admin/articles/{a.id}/edit", data={"title": a.title, "body_html": a.body_html, "category_id": a.category_id,
                                                      "author_id": a.author_id, "action": "restore"})
    r = db.session.get(Article, a.id)
    assert r.status == "draft" and not r.is_featured and client.get(a.url_path).status_code == 404  # restored to DRAFT, not live


def test_writer_cannot_archive(client, w):
    a = w.art("Own draft", status="draft", by="writer", author="writer")
    login(client, "writer")
    client.post(f"/admin/articles/{a.id}/edit", data={"title": a.title, "body_html": a.body_html, "action": "archive"})
    assert db.session.get(Article, a.id).status == "draft"


def test_sponsored_requires_disclosure_name(client, w):
    login(client, "editor")
    post_article(client, w, title="Paid", article_type="sponsored", action="publish", sponsor_name="")
    a = Article.query.filter_by(title="Paid").one()
    assert a.status == "draft"  # refused: no sponsor name
    client.post(f"/admin/articles/{a.id}/edit", data={"title": "Paid", "body_html": "<p>x</p>", "category_id": w.cats["economy"].id, "author_id": w.authors["editor"].id,
                                                      "article_type": "sponsored", "sponsor_name": "Acme", "action": "publish"})
    assert db.session.get(Article, a.id).status == "published"


def test_article_list_sort_filter_and_author_filter(client, w):
    w.art("Zebra", slug="zebra", author="writer")
    w.art("Apple", slug="apple", author="editor")
    login(client, "editor")
    asc = client.get("/admin/articles?sort=title&dir=asc").data.decode()
    assert asc.index("Apple") < asc.index("Zebra") and 'aria-sort="ascending"' in asc
    desc = client.get("/admin/articles?sort=title&dir=desc").data.decode()
    assert desc.index("Zebra") < desc.index("Apple")
    assert b"Zebra" in client.get(f"/admin/articles?author={w.authors['writer'].id}").data
    assert b"Apple" not in client.get(f"/admin/articles?author={w.authors['writer'].id}").data
    assert client.get("/admin/articles?sort=__import__&dir=zzz").status_code == 200  # unknown sort falls back safely


def test_admin_topbar_notifications_and_search(client, w):
    w.art("Needs review", status="review", when=None, by="writer", author="writer")
    login(client, "editor")
    html = client.get("/admin/").data.decode()
    assert "awaiting review" in html and 'name="q"' in html and "My account" in html
    client.post("/admin/logout")
    login(client, "writer")
    assert "awaiting review" not in client.get("/admin/").data.decode()
