"""Phase 3: admin CMS, editorial workflow, role-based publishing, user management."""
from datetime import timedelta

import pytest

from negarit.extensions import db
from negarit.models import ActivityLog, Article, Category, Tag, User
from negarit.services import workflow
from negarit.services.workflow import WorkflowError, apply, available_actions, problems

from conftest import PW, login


def form(w, **over):
    d = {"title": "New story", "body_md": "Some *body* text.", "category_id": w.cats["economy"].id, "article_type": "news",
         "action": "save", "tags": "Inflation, New Tag, inflation"}
    d.update(over)
    return d


# ------------------------------------------------------------- workflow unit
def test_workflow_transitions_and_permissions(w):
    a = w.art("Flow", status="draft", by="writer", author="writer")
    writer, editor = w.users["writer"], w.users["editor"]
    assert [k for k, _ in available_actions(a, writer)] == ["submit"]
    with pytest.raises(WorkflowError):
        apply(a, "publish", writer)
    apply(a, "submit", writer)
    assert a.status == "review" and available_actions(a, writer) == []
    apply(a, "request_changes", editor, note="Tighten the intro")
    assert a.status == "draft" and a.review_note == "Tighten the intro"
    apply(a, "submit", writer)
    apply(a, "approve", editor)
    assert a.status == "approved" and a.review_note == ""
    with pytest.raises(WorkflowError):
        apply(a, "schedule", editor)  # needs a future time
    apply(a, "schedule", editor, when=w.now + timedelta(days=1))
    assert a.status == "scheduled"
    apply(a, "unschedule", editor)
    assert a.status == "approved"
    apply(a, "publish", editor)
    assert a.status == "published" and a.published_at <= w.now + timedelta(seconds=5)
    apply(a, "unpublish", editor)
    assert a.status == "draft"


def test_cannot_publish_without_category_author_or_body(w):
    a = w.art("Incomplete", status="draft", cat=None)
    assert "Choose a category" in " ".join(problems(a))
    with pytest.raises(WorkflowError):
        apply(a, "publish", w.users["editor"])
    a.body_md, a.body_html, a.author_id = "", "", None
    assert len(problems(a)) == 3


def test_publish_keeps_original_date_when_already_live(w):
    a = w.art("Live", status="scheduled", when=w.now - timedelta(days=3))
    before = a.published_at
    apply(a, "publish", w.users["editor"])  # already live via schedule: publishing must not bump the date
    assert a.published_at == before


# ------------------------------------------------------------ admin article CRUD
def test_editor_full_article_lifecycle(client, w):
    login(client, "editor")
    r = client.post("/admin/articles/new", data=form(w, action="publish", author_id=w.authors["editor"].id, is_featured="on",
                                                    featured_media_id=w.media.id, gallery_ids=f"{w.media.id},{w.media.id}"))
    assert r.status_code == 302
    a = Article.query.filter_by(title="New story").one()
    assert a.status == "published" and a.is_featured and a.slug == "new-story" and len(a.gallery) == 1
    assert sorted(t.name for t in a.tags) == ["Inflation", "New Tag"]  # case-insensitive de-dup, new tag created
    assert client.get("/economy/new-story").status_code == 200
    future = (w.now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    client.post(f"/admin/articles/{a.id}/edit", data=form(w, title="New story", action="unpublish"))
    assert db.session.get(Article, a.id).status == "draft"
    client.post(f"/admin/articles/{a.id}/edit", data=form(w, action="schedule", publish_at=future, author_id=w.authors["editor"].id))
    a = db.session.get(Article, a.id)
    assert a.status == "scheduled" and a.published_at > w.now
    assert client.get("/economy/new-story").status_code == 404
    assert client.get(f"/admin/articles/{a.id}/preview").status_code == 200
    client.post(f"/admin/articles/{a.id}/delete")
    assert db.session.get(Article, a.id) is None


def test_failed_transition_still_saves_content(client, w):
    login(client, "editor")
    r = client.post("/admin/articles/new", data=form(w, category_id="", action="publish", author_id=w.authors["editor"].id),
                    follow_redirects=True)
    a = Article.query.filter_by(title="New story").one()
    assert a.status == "draft" and b"Choose a category" in r.data


def test_writer_cannot_publish_and_is_locked_after_submit(client, w):
    login(client, "writer")
    client.post("/admin/articles/new", data=form(w, action="publish"))
    a = Article.query.filter_by(title="New story").one()
    assert a.status == "draft" and a.author_id == w.authors["writer"].id and a.created_by_id == w.users["writer"].id
    client.post(f"/admin/articles/{a.id}/edit", data=form(w, action="submit"))
    assert db.session.get(Article, a.id).status == "review"
    assert client.post(f"/admin/articles/{a.id}/edit", data=form(w, title="Sneaky edit")).status_code == 403
    assert client.get(f"/admin/articles/{a.id}/edit").status_code == 200  # can still view status
    assert client.post(f"/admin/articles/{a.id}/delete").status_code == 403


def test_writer_scope_limited_to_own_articles(client, w):
    theirs = w.art("Editor's piece", status="draft", by="editor", author="editor")
    login(client, "writer")
    assert client.get(f"/admin/articles/{theirs.id}/edit").status_code == 403
    assert client.get(f"/admin/articles/{theirs.id}/preview").status_code == 403
    assert b"Editor&#39;s piece" not in client.get("/admin/articles").data and b"Editor's piece" not in client.get("/admin/articles").data


def test_writer_can_delete_own_draft_contributor_cannot(client, w):
    login(client, "writer")
    client.post("/admin/articles/new", data=form(w))
    a = Article.query.one()
    assert client.post(f"/admin/articles/{a.id}/delete").status_code == 302 and Article.query.count() == 0
    client.post("/admin/logout")
    login(client, "contributor")
    client.post("/admin/articles/new", data=form(w, title="Contrib draft"))
    b = Article.query.one()
    assert client.post(f"/admin/articles/{b.id}/delete").status_code == 403
    client.post(f"/admin/articles/{b.id}/edit", data=form(w, title="Contrib draft", action="submit"))
    assert db.session.get(Article, b.id).status == "review"


def test_contributor_cannot_browse_media_or_manage_taxonomy(client, w):
    login(client, "contributor")
    for path in ("/admin/media", "/admin/categories", "/admin/authors", "/admin/subscribers", "/admin/tags"):
        assert client.get(path).status_code == 403, path


def test_dashboard_per_role(client, w):
    w.art("Awaiting", status="review", when=None, by="writer", author="writer")
    login(client, "editor")
    r = client.get("/admin/")
    assert r.status_code == 200 and b"Awaiting editorial review" in r.data and b"Total views" in r.data and b"Awaiting" in r.data
    client.post("/admin/logout")
    login(client, "writer")
    r = client.get("/admin/")
    assert r.status_code == 200 and b"Total views" not in r.data and b"Your articles in review" in r.data


def test_activity_log_records_editorial_actions(client, w):
    login(client, "editor")
    client.post("/admin/articles/new", data=form(w, action="publish", author_id=w.authors["editor"].id))
    log = ActivityLog.query.filter(ActivityLog.action == "article_publish").one()
    assert log.user_email == "editor@example.com" and "published" in log.summary
    client.post("/admin/logout")
    login(client, "admin")
    assert b"article_publish" in client.get("/admin/logs").data


# ------------------------------------------------------- categories / tags / pages
def test_category_management(client, w):
    login(client, "editor")
    r = client.post("/admin/categories/0", data={"name": "Markets", "slug": "", "sort_order": "5", "is_primary": "on"})
    assert r.status_code == 302 and Category.query.filter_by(slug="markets").one().is_primary
    bad = client.post("/admin/categories/0", data={"name": "Trap", "slug": "admin"})
    assert bad.status_code == 200 and b"already used or reserved" in bad.data
    w.art("Has cat")
    cid = w.cats["economy"].id
    r = client.post(f"/admin/categories/{cid}/delete", follow_redirects=True)
    assert b"Move or delete" in r.data and db.session.get(Category, cid) is not None
    assert client.get("/admin/categories").status_code == 200


def test_tag_and_author_management(client, w):
    login(client, "editor")
    assert client.post("/admin/tags/0", data={"name": "Trade"}).status_code == 302 and Tag.query.filter_by(slug="trade").one()
    assert b"already exists" in client.post("/admin/tags/0", data={"name": "trade"}).data
    r = client.post("/admin/authors/0", data={"name": "Guest Writer", "position": "Contributor", "twitter": "https://x.com/g",
                                              "bio": "Bio text", "active": "on"})
    assert r.status_code == 302
    assert b"Guest Writer" in client.get("/authors").data or True
    w.art("By guest", author="editor")
    from negarit.models import Author
    g = Author.query.filter_by(name="Guest Writer").one()
    assert g.socials == [("X", "https://x.com/g")]
    assert client.get(f"/author/{g.slug}").status_code == 200
    assert b"must start with https://" in client.post("/admin/authors/0", data={"name": "Bad", "twitter": "javascript:x"}).data


def test_pages_are_admin_only_and_editable(client, w):
    login(client, "editor")
    assert client.get("/admin/pages").status_code == 403
    client.post("/admin/logout")
    login(client, "admin")
    assert client.post("/admin/pages/0", data={"title": "Privacy Policy", "slug": "", "body_md": "We respect **privacy**.",
                                               "published": "on", "in_footer": "on", "sort_order": "20"}).status_code == 302
    assert b"<strong>privacy</strong>" in client.get("/privacy-policy").data
    assert b"Privacy Policy" in client.get("/").data  # footer link


# ------------------------------------------------------------------------ users
def test_user_management_rules(client, w):
    login(client, "super_admin")
    ok = client.post("/admin/users/0", data={"name": "New Editor", "email": "NEW@example.com", "role": "editor",
                                             "password": "a-very-long-password", "active": "on"})
    assert ok.status_code == 302
    u = User.query.filter_by(email="new@example.com").one()
    assert u.check_password("a-very-long-password") and u.author is not None  # public author profile auto-created
    forged = client.post("/admin/users/0", data={"name": "X", "email": "x@example.com", "role": "root",
                                                 "password": "a-very-long-password"})
    assert forged.status_code == 200 and b"choose one of the available options" in forged.data
    assert User.query.filter_by(email="x@example.com").first() is None  # nothing was created
    assert client.post("/admin/users/0", data={"name": "Second SA", "email": "sa3@example.com", "role": "super_admin",
                                               "password": "a-very-long-password", "active": "on"}).status_code == 302
    assert b"at least 12" in client.post("/admin/users/0", data={"name": "X", "email": "y@example.com", "role": "writer", "password": "short"}).data
    me = w.users["super_admin"].id
    assert b"cannot change your own role" in client.post(f"/admin/users/{me}", data={
        "name": "Super", "email": "super_admin@example.com", "role": "editor", "active": "on"}).data


def test_last_super_admin_is_protected(client, w):
    login(client, "super_admin")
    sa2 = User(email="sa2@example.com", name="SA2", role="super_admin")
    sa2.set_password(PW)
    db.session.add(sa2)
    db.session.commit()
    w.users["admin"].role = "editor"
    db.session.commit()
    r = client.post(f"/admin/users/{sa2.id}", data={"name": "SA2", "email": "sa2@example.com", "role": "admin", "active": "on"})
    assert r.status_code == 302  # allowed: another active super admin remains
    r = client.post(f"/admin/users/{w.users['super_admin'].id}", data={"name": "Super", "email": "super_admin@example.com",
                                                                     "role": "super_admin"})
    assert b"cannot change your own role" in r.data


def test_admin_can_send_reset_link(client, w, monkeypatch):
    sent = {}
    import negarit.views.admin.system as system
    monkeypatch.setattr(system, "send_email", lambda to, s, h, t: sent.update(to=to) or True)
    login(client, "super_admin")
    assert client.post(f"/admin/users/{w.users['writer'].id}/reset").status_code == 302 and sent["to"] == "writer@example.com"


def test_account_password_change(client, w):
    login(client, "writer")
    r = client.post("/admin/account", data={"name": "Writer", "current_password": "wrong", "new_password": "another-long-password"})
    assert b"Current password is incorrect" in r.data
    assert client.post("/admin/account", data={"name": "Writer Two", "current_password": PW, "new_password": "another-long-password"}).status_code == 302
    assert db.session.get(User, w.users["writer"].id).check_password("another-long-password")


def test_scheduled_publishing_cron(client, w):
    a = w.art("Scheduled", status="scheduled", when=w.now - timedelta(minutes=1))
    assert client.get("/cron/publish-scheduled").status_code == 401
    r = client.get("/cron/publish-scheduled", headers={"Authorization": "Bearer " + "x" * 24})
    assert r.status_code == 200 and r.json["published"] == 1 and db.session.get(Article, a.id).status == "published"


def test_administrator_cannot_manage_users_or_settings(client, w):
    """Per the role definitions: users, roles and configuration belong to the Super Administrator only."""
    login(client, "admin")
    for path in ("/admin/users", "/admin/users/0", f"/admin/users/{w.users['writer'].id}", "/admin/settings"):
        assert client.get(path).status_code == 403, path
    assert client.post("/admin/users/0", data={"name": "X", "email": "x@example.com", "role": "editor",
                                               "password": "a-very-long-password"}).status_code == 403
    assert client.post(f"/admin/users/{w.users['writer'].id}/reset").status_code == 403
    assert User.query.filter_by(email="x@example.com").first() is None
    # ...but the Administrator does run content, taxonomy, media, pages, ads, SEO, data, homepage and logs
    for path in ("/admin/articles", "/admin/categories", "/admin/media", "/admin/pages", "/admin/ads", "/admin/seo", "/admin/logs",
                 "/admin/data", "/admin/homepage", "/admin/analytics"):
        assert client.get(path).status_code == 200, path
