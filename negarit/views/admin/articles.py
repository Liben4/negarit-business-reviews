from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup, escape
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from ...cli import ensure_author
from ...extensions import db, limiter
from ...models import (ARTICLE_TYPES, STATUS_LABELS, STATUSES, Article, ArticleMedia, ArticleRevision, Author, Category,
                       Comment, EditorialNote, Media, Subscriber, Tag, User)
from ...security import permission_required
from ...services import activity, analytics, content, workflow
from ...utils import escape_like, from_local_input, unique_slug, utcnow
from . import bp
from .helpers import ids_from_csv, page_arg

TEXT_FIELDS = {"subtitle": 300, "excerpt": 400, "cover_caption": 300, "seo_title": 120, "meta_description": 320,
               "og_title": 120, "og_description": 320, "sponsor_name": 160}
SORTS = {"updated": Article.updated_at, "title": Article.title, "status": Article.status, "published": Article.published_at,
         "views": Article.view_count, "type": Article.article_type}
PICKABLE = ("draft", "review", "approved")  # statuses where autosave is allowed (never live content)


def _mine_filter():
    """Non-editors only ever see their own articles."""
    if current_user.can("article.edit_any"):
        return None
    return or_(Article.created_by_id == current_user.id, Article.author.has(Author.user_id == current_user.id))


def _get_article(id) -> Article:
    a = db.get_or_404(Article, id)
    if not (current_user.can("article.edit_any") or workflow.owns(a, current_user)):
        abort(403)
    return a


@bp.get("/")
def dashboard():
    mine = _mine_filter()
    q = select(Article.status, func.count()).group_by(Article.status)
    if mine is not None:
        q = q.where(mine)
    counts = dict(db.session.execute(q).all())
    stats = analytics.overview() if current_user.can("dashboard.full") else None
    recent_q = select(Article).options(joinedload(Article.category), joinedload(Article.author)).order_by(Article.updated_at.desc()).limit(8)
    review_q = select(Article).options(joinedload(Article.author)).where(Article.status == "review").order_by(Article.updated_at).limit(8)
    if mine is not None:
        recent_q, review_q = recent_q.where(mine), review_q.where(mine)
    from ...models import ActivityLog
    activity_rows = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(8).all() if current_user.can("article.edit_any") else []
    people = None
    if current_user.can("dashboard.full"):
        people = {"authors": Author.query.filter_by(active=True).count(),
                  "editors": User.query.filter(User.role.in_(("editor", "admin", "super_admin")), User.active.is_(True)).count()}
    return render_template(
        "admin/dashboard.html", counts=counts, stats=stats, people=people, recent=db.session.scalars(recent_q).all(),
        review=db.session.scalars(review_q).all(), activity=activity_rows,
        most_read=content.most_read(5, days=30) if stats else [],
        pending_comments=Comment.query.filter_by(status="pending").count() if current_user.can("comments.moderate") else None)


@bp.get("/articles")
def articles():
    stmt = select(Article).options(joinedload(Article.category), joinedload(Article.author))
    mine = _mine_filter()
    if mine is not None:
        stmt = stmt.where(mine)
    status = request.args.get("status", "")
    if status in STATUSES:
        stmt = stmt.where(Article.status == status)
    elif not status:
        stmt = stmt.where(Article.status != "archived")  # archive is its own tab
    atype = request.args.get("type", "")
    if atype in dict(ARTICLE_TYPES):
        stmt = stmt.where(Article.article_type == atype)
    cat = request.args.get("category", type=int)
    if cat:
        stmt = stmt.where(Article.category_id == cat)
    auth = request.args.get("author", type=int)
    if auth and mine is None:
        stmt = stmt.where(Article.author_id == auth)
    term = (request.args.get("q") or "").strip()[:100]
    if term:
        stmt = stmt.where(Article.title.ilike(f"%{escape_like(term)}%", escape="\\"))
    sort = request.args.get("sort", "updated")
    sort = sort if sort in SORTS else "updated"
    direction = "asc" if request.args.get("dir") == "asc" else "desc"
    col = SORTS[sort]
    stmt = stmt.order_by(col.asc() if direction == "asc" else col.desc(), Article.id.desc())
    p = db.paginate(stmt, page=page_arg(), per_page=25, error_out=False)
    return render_template("admin/articles.html", pagination=p, status=status, atype=atype, cat=cat, auth=auth, term=term,
                           sort=sort, direction=direction, categories=Category.query.order_by(Category.name).all(),
                           authors=Author.query.order_by(Author.name).all() if mine is None else [], statuses=STATUSES,
                           types=ARTICLE_TYPES, STATUS_LABELS=STATUS_LABELS, can_edit=workflow.can_edit)


def _parse_tags(raw: str) -> list[Tag]:
    tags, seen = [], set()
    for name in (raw or "").split(","):
        name = " ".join(name.split())[:60]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        t = Tag.query.filter(func.lower(Tag.name) == name.lower()).first()
        if not t:
            t = Tag(name=name, slug=unique_slug(Tag, name))
            db.session.add(t)
        tags.append(t)
        if len(tags) >= 10:
            break
    return tags


def _apply_content(a: Article, d) -> None:
    """Headline, deck, excerpt, body and sources (shared by manual save and autosave)."""
    a.title = (d.get("title") or "").strip()[:255]
    for k in ("subtitle", "excerpt"):
        setattr(a, k, (d.get(k) or "").strip()[:TEXT_FIELDS[k]])
    if "body_html" in d:
        a.set_body_html(d.get("body_html") or "")
    elif "body_md" in d:  # legacy / API clients
        a.set_body(d.get("body_md") or "")
    if "sources_md" in d:
        a.set_sources(d.get("sources_md") or "")
    a.updated_at = utcnow()


def _apply_fields(a: Article, form) -> None:
    user = current_user
    _apply_content(a, form)
    for k in ("cover_caption", "seo_title", "meta_description", "og_title", "og_description", "sponsor_name"):
        setattr(a, k, (form.get(k) or "").strip()[:TEXT_FIELDS[k]])
    a.article_type = form.get("article_type") if form.get("article_type") in dict(ARTICLE_TYPES) else "news"
    cid = form.get("category_id", type=int)
    a.category_id = cid if cid and db.session.get(Category, cid) else None
    mid = form.get("featured_media_id", type=int)
    a.featured_media_id = mid if mid and db.session.get(Media, mid) else None
    canon = (form.get("canonical_override") or "").strip()[:500]
    a.canonical_override = canon if canon.startswith(("https://", "http://")) else ""
    a.tags = _parse_tags(form.get("tags", ""))
    a.gallery = [ArticleMedia(media_id=m.id, position=i) for i, m in enumerate(
        [db.session.get(Media, i) for i in ids_from_csv(form.get("gallery_ids", ""))[:24]]) if m]
    if user.can("article.edit_any"):
        aid = form.get("author_id", type=int)
        a.author_id = aid if aid and db.session.get(Author, aid) else a.author_id
        a.is_featured = form.get("is_featured") == "on"
        a.is_breaking = form.get("is_breaking") == "on"
    else:  # journalists/contributors always write under their own byline
        a.author = ensure_author(user)
    slug_in = (form.get("slug") or "").strip()
    if not a.slug or slug_in:
        a.slug = unique_slug(Article, slug_in or a.title, a.id)


OK_MESSAGES = {"save": "Saved.", "submit": "Submitted for editorial review.", "approve": "Approved.", "publish": "Published.",
               "schedule": "Scheduled.", "unpublish": "Unpublished \u2014 back to draft.", "unschedule": "Unscheduled \u2014 now approved.",
               "request_changes": "Revisions requested \u2014 sent back to the writer.", "archive": "Archived.", "restore": "Restored to draft."}


def _save(a: Article, is_new: bool):
    form = request.form
    if not (form.get("title") or "").strip():
        flash("A headline is required.", "error")
        return None
    with db.session.no_autoflush:  # the article is incomplete until every field is applied
        _apply_fields(a, form)
    if is_new:
        a.created_by_id = current_user.id
        db.session.add(a)
    action = form.get("action", "save")
    err = None
    if action != "save":
        try:
            workflow.apply(a, action, current_user, when=from_local_input(form.get("publish_at", "")), note=form.get("review_note", ""))
        except workflow.WorkflowError as e:
            err = str(e)
    db.session.flush()
    if not err:
        note = (form.get("review_note") or "").strip()
        if action == "request_changes":
            workflow.add_note(a, current_user, note or "Revisions requested.", "revision_request")
        elif action in ("approve", "publish", "schedule") and note:
            workflow.add_note(a, current_user, note, "approval")
    workflow.snapshot(a, current_user, action if not err else "save")
    activity.log(f"article_{action}", "article", a.id, f"{a.title} \u2192 {a.status}")
    db.session.commit()
    if err:
        flash(err + " The article was saved without changing its status.", "error")
    else:
        flash(OK_MESSAGES.get(action, "Saved."), "ok")
    return a


def _editor_ctx(a: Article):
    staff = current_user.can("article.edit_any")
    return dict(
        a=a, categories=Category.query.filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all(),
        authors=Author.query.filter_by(active=True).order_by(Author.name).all(), types=ARTICLE_TYPES,
        actions=workflow.available_actions(a, current_user), warnings=workflow.warnings(a) if a.id else [],
        can_edit_any=staff, can_delete=bool(a.id) and workflow.can_delete(a, current_user),
        locked=bool(a.id) and not workflow.can_edit(a, current_user), can_publish=current_user.can("article.publish"),
        can_duplicate=bool(a.id), autosave_ok=a.status in PICKABLE if a.id else True,
        tags_csv=", ".join(t.name for t in a.tags), gallery_ids=",".join(str(g.media_id) for g in a.gallery),
        gallery_media=[g.media for g in a.gallery],
        notes=EditorialNote.query.filter_by(article_id=a.id).order_by(EditorialNote.created_at.desc()).limit(30).all() if a.id else [],
        revision_count=ArticleRevision.query.filter_by(article_id=a.id).count() if a.id else 0,
        version=a.updated_at.isoformat() if a.id and a.updated_at else "")


@bp.route("/articles/new", methods=["GET", "POST"])
@permission_required("article.create")
def article_new():
    a = Article(article_type="news", status="draft")
    if request.method == "POST":
        saved = _save(a, True)
        if saved:
            return redirect(url_for("admin.article_edit", id=saved.id))
        a.title = request.form.get("title", "")
    elif current_user.author:
        a.author_id = current_user.author.id
    return render_template("admin/article_form.html", **_editor_ctx(a))


@bp.route("/articles/<int:id>/edit", methods=["GET", "POST"])
def article_edit(id):
    a = _get_article(id)
    if request.method == "POST":
        if not workflow.can_edit(a, current_user):
            abort(403)
        _save(a, False)
        return redirect(url_for("admin.article_edit", id=a.id))
    return render_template("admin/article_form.html", **_editor_ctx(a))


@bp.post("/articles/autosave")
@permission_required("article.create")
@limiter.limit("600/hour")
def autosave():
    """Background draft saving. Never touches live content: only draft / in-review / approved articles."""
    d = request.get_json(silent=True) or {}
    aid = d.get("id")
    if aid:
        a = db.session.get(Article, aid) if isinstance(aid, int) else None
        if not a or not (current_user.can("article.edit_any") or workflow.owns(a, current_user)):
            return jsonify(error="Not found."), 404
        if not workflow.can_edit(a, current_user):
            return jsonify(error="You can no longer edit this article."), 403
        if a.status not in PICKABLE:
            return jsonify(error="Autosave is off for published, scheduled and archived articles. Use Save."), 409
        if d.get("v") and a.updated_at and a.updated_at.isoformat() != d.get("v"):
            return jsonify(error="Someone else changed this article. Reload before saving."), 409
    else:
        if not (d.get("title") or "").strip():
            return jsonify(error="Add a headline to start autosaving."), 422
        a = Article(article_type="news", status="draft", created_by_id=current_user.id)
        a.author = current_user.author or ensure_author(current_user)
    with db.session.no_autoflush:
        _apply_content(a, d)
        if not a.slug:
            a.slug = unique_slug(Article, a.title, a.id)
    db.session.add(a)
    db.session.commit()
    return jsonify(id=a.id, v=a.updated_at.isoformat(), saved_at=utcnow().strftime("%H:%M"),
                   edit_url=url_for("admin.article_edit", id=a.id))


@bp.get("/articles/<int:id>/preview")
def article_preview(id):
    a = _get_article(id)
    return render_template("public/article.html", a=a, related=[], most_read=[], comments=[], comment_form=None, comment_state=None,
                           older=None, newer=None, canonical="", url="", preview=True,
                           breadcrumbs=[("Home", "/"), (a.category.name if a.category else "Uncategorised", None), (a.title, None)])


@bp.post("/articles/<int:id>/duplicate")
@permission_required("article.create")
def article_duplicate(id):
    a = _get_article(id)
    copy = workflow.duplicate(a, current_user)
    if not current_user.can("article.edit_any"):
        copy.author = ensure_author(current_user)
    workflow.snapshot(copy, current_user, "duplicate")
    activity.log("article_duplicate", "article", copy.id, f"Copied from #{a.id}: {a.title}")
    db.session.commit()
    flash("Duplicated as a new draft.", "ok")
    return redirect(url_for("admin.article_edit", id=copy.id))


@bp.post("/articles/<int:id>/notes")
@permission_required("notes.add")
def article_note(id):
    a = _get_article(id)
    n = workflow.add_note(a, current_user, request.form.get("body", ""))
    if n:
        activity.log("article_note", "article", a.id, a.title)
        db.session.commit()
        flash("Note added.", "ok")
    else:
        flash("Write a note first.", "error")
    return redirect(url_for("admin.article_edit", id=a.id) + "#notes")


@bp.get("/articles/<int:id>/revisions")
def article_revisions(id):
    a = _get_article(id)
    revs = ArticleRevision.query.filter_by(article_id=a.id).order_by(ArticleRevision.id.desc()).all()
    return render_template("admin/revisions.html", a=a, revs=revs, can_restore=workflow.can_edit(a, current_user),
                           STATUS_LABELS=STATUS_LABELS)


@bp.post("/articles/<int:id>/revisions/<int:rid>/restore")
def article_revision_restore(id, rid):
    a = _get_article(id)
    if not workflow.can_edit(a, current_user):
        abort(403)
    rev = db.get_or_404(ArticleRevision, rid)
    if rev.article_id != a.id:
        abort(404)
    workflow.restore_revision(a, rev, current_user)
    db.session.flush()
    workflow.snapshot(a, current_user, "restore")
    activity.log("article_restore_revision", "article", a.id, f"{a.title}: restored version from {rev.created_at:%Y-%m-%d %H:%M}")
    db.session.commit()
    flash("Earlier version restored. Review it, then save or publish as normal.", "ok")
    return redirect(url_for("admin.article_edit", id=a.id))


@bp.post("/articles/<int:id>/delete")
def article_delete(id):
    a = _get_article(id)
    if not workflow.can_delete(a, current_user):
        abort(403)
    activity.log("article_delete", "article", a.id, a.title)
    db.session.delete(a)
    db.session.commit()
    flash("Article deleted.", "ok")
    return redirect(url_for("admin.articles"))


@bp.post("/preview-markdown")
@limiter.limit("120/minute")
def preview_markdown():
    from ...utils import render_markdown
    return jsonify(html=render_markdown((request.get_json(silent=True) or {}).get("md", "")[:200000]))
