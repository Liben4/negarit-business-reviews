"""Editorial workflow: Draft -> Submitted for review -> Approved -> Scheduled / Published (+ archive)."""
from ..extensions import db
from ..models import ArticleMedia, ArticleRevision, EditorialNote
from ..utils import unique_slug, utcnow


class WorkflowError(Exception):
    pass


# key: (label, allowed_from, to, required_permission or None)
ACTIONS = {
    "submit": ("Submit for review", {"draft"}, "review", None),
    "request_changes": ("Request revisions", {"review", "approved"}, "draft", "article.publish"),
    "approve": ("Approve", {"review"}, "approved", "article.publish"),
    "schedule": ("Schedule", {"draft", "review", "approved"}, "scheduled", "article.publish"),
    "publish": ("Publish now", {"draft", "review", "approved", "scheduled"}, "published", "article.publish"),
    "unschedule": ("Unschedule", {"scheduled"}, "approved", "article.publish"),
    "unpublish": ("Unpublish", {"published"}, "draft", "article.publish"),
    "archive": ("Archive", {"draft", "review", "approved", "scheduled", "published"}, "archived", "article.archive"),
    "restore": ("Restore to draft", {"archived"}, "draft", "article.archive"),
}
REVISION_LIMIT = 60


def owns(article, user) -> bool:
    return bool(article.created_by_id == user.id or (article.author and article.author.user_id == user.id))


def can_edit(article, user) -> bool:
    if user.can("article.edit_any"):
        return True
    return user.can("article.edit_own") and owns(article, user) and article.status == "draft"


def can_delete(article, user) -> bool:
    if user.can("article.delete_any"):
        return True
    return user.can("article.delete_own_draft") and owns(article, user) and article.status == "draft"


def available_actions(article, user) -> list[tuple[str, str]]:
    if not article.id and not user.can("article.publish"):
        return []  # brand-new article: save first
    out = []
    for key, (label, allowed, _to, perm) in ACTIONS.items():
        if article.status not in allowed or (perm and not user.can(perm)):
            continue
        if key == "submit" and not (user.can("article.submit") and (owns(article, user) or user.can("article.edit_any"))):
            continue
        if not article.id and key in ("archive", "restore", "unpublish", "unschedule", "request_changes", "approve"):
            continue
        out.append((key, label))
    return out


def problems(article) -> list[str]:
    """Hard requirements to submit/approve/publish/schedule."""
    errs = []
    if not (article.title or "").strip():
        errs.append("A headline is required.")
    if not (article.body_html or article.body_md or "").strip():
        errs.append("The article body is empty.")
    if not article.category_id:
        errs.append("Choose a category (it forms the article URL).")
    if not article.author_id:
        errs.append("Choose an author.")
    if article.article_type == "sponsored" and not (article.sponsor_name or "").strip():
        errs.append("Sponsored content needs a sponsor name for the reader-facing disclosure.")
    return errs


def warnings(article) -> list[str]:
    """Soft checks shown in the editor (never block)."""
    w = []
    if not article.featured_media_id:
        w.append("No featured image (needed for social cards and Google Discover).")
    elif article.featured_media and not article.featured_media.alt_text:
        w.append("Featured image has no alt text.")
    if not article.meta_description:
        w.append("No meta description: a summary will be used.")
    if not article.excerpt:
        w.append("No excerpt for cards and search results.")
    if not article.tags:
        w.append("No tags.")
    return w


def apply(article, action: str, user, when=None, note: str = "") -> None:
    if action not in ACTIONS:
        raise WorkflowError("Unknown action.")
    label, allowed, to, perm = ACTIONS[action]
    if perm and not user.can(perm):
        raise WorkflowError("You do not have permission to do that.")
    if action == "submit" and not user.can("article.submit"):
        raise WorkflowError("You do not have permission to submit articles.")
    if article.status not in allowed:
        raise WorkflowError(f"Cannot \u201c{label.lower()}\u201d an article that is {article.status}.")
    if action in ("submit", "approve", "schedule", "publish"):
        errs = problems(article)
        if errs:
            raise WorkflowError(" ".join(errs))
    now = utcnow()
    if action == "schedule":
        if not when or when <= now:
            raise WorkflowError("Pick a schedule time in the future.")
        article.published_at = when
    elif action == "publish":
        already_live = article.status in ("published", "scheduled") and article.published_at and article.published_at <= now
        if not already_live:
            article.published_at = now
    elif action == "request_changes":
        article.review_note = (note or "").strip()[:500]
    if action in ("approve", "schedule", "publish"):
        article.review_note = ""
    if action == "restore":
        article.is_featured = article.is_breaking = False
    article.status = to


# ------------------------------------------------------------- revisions & notes
def snapshot(article, user, kind: str = "save") -> ArticleRevision | None:
    """Record the current content. Skips duplicates of the previous snapshot; keeps the newest REVISION_LIMIT."""
    last = (ArticleRevision.query.filter_by(article_id=article.id).order_by(ArticleRevision.id.desc()).first()
            if article.id else None)
    same = last and (last.title, last.subtitle, last.excerpt, last.body_html, last.sources_md, last.status) == (
        article.title, article.subtitle, article.excerpt, article.body_html, article.sources_md, article.status)
    if same:
        return None
    rev = ArticleRevision(article_id=article.id, user_id=user.id, user_name=user.name, kind=kind, status=article.status,
                          title=article.title, subtitle=article.subtitle, excerpt=article.excerpt,
                          body_html=article.body_html, sources_md=article.sources_md)
    db.session.add(rev)
    db.session.flush()
    old = (ArticleRevision.query.filter_by(article_id=article.id).order_by(ArticleRevision.id.desc())
           .offset(REVISION_LIMIT).all())
    for o in old:
        db.session.delete(o)
    return rev


def restore_revision(article, rev: ArticleRevision, user) -> None:
    snapshot(article, user, "before-restore")
    article.title, article.subtitle, article.excerpt = rev.title, rev.subtitle, rev.excerpt
    article.set_body_html(rev.body_html)
    article.set_sources(rev.sources_md)
    article.updated_at = utcnow()


def add_note(article, user, body: str, kind: str = "note") -> EditorialNote | None:
    body = (body or "").strip()[:2000]
    if not body:
        return None
    n = EditorialNote(article_id=article.id, user_id=user.id, user_name=user.name, kind=kind, body=body)
    db.session.add(n)
    return n


def duplicate(article, user):
    """A new DRAFT copy (never published/featured); the caller commits."""
    from ..models import Article
    copy = Article(
        title=f"Copy of {article.title}"[:255], slug="", subtitle=article.subtitle, excerpt=article.excerpt,
        body_md=article.body_md, body_html=article.body_html, body_text=article.body_text, sources_md=article.sources_md,
        sources_html=article.sources_html, article_type=article.article_type, status="draft", access_level=article.access_level,
        featured_media_id=article.featured_media_id, cover_caption=article.cover_caption, seo_title=article.seo_title,
        meta_description=article.meta_description, og_title=article.og_title, og_description=article.og_description,
        sponsor_name=article.sponsor_name, category_id=article.category_id, author_id=article.author_id,
        created_by_id=user.id, is_demo=False)
    with db.session.no_autoflush:  # the copy is incomplete until every field is set
        copy.slug = unique_slug(type(article), copy.title)
        copy.tags = list(article.tags)
        copy.gallery = [ArticleMedia(media_id=g.media_id, position=g.position) for g in article.gallery]
    db.session.add(copy)
    db.session.flush()
    return copy
