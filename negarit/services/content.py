"""Public-site queries. All results are live (time-based) articles with relationships eager-loaded."""
from datetime import timedelta

from sqlalchemy import case, desc, exists, func, or_, select, true
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import Article, Author, Category, PageView, Tag, article_tags
from ..utils import escape_like, utcnow

EAGER = (joinedload(Article.category), joinedload(Article.author).joinedload(Author.image),
         joinedload(Article.featured_media))


def live_stmt(*conds):
    return (select(Article).options(*EAGER).where(Article.live_filter(), *conds)
            .order_by(Article.published_at.desc(), Article.id.desc()))


def fetch(limit: int, *conds, exclude=(), offset: int = 0) -> list[Article]:
    stmt = live_stmt(*conds)
    if exclude:
        stmt = stmt.where(Article.id.notin_(list(exclude)))
    return list(db.session.scalars(stmt.offset(offset).limit(limit)))


def get_live(slug: str) -> Article | None:
    return db.session.scalar(live_stmt(Article.slug == slug).options(selectinload(Article.tags),
                                                                     selectinload(Article.gallery)))


def _ordered(ids: list[int]) -> list[Article]:
    if not ids:
        return []
    rows = {a.id: a for a in db.session.scalars(live_stmt(Article.id.in_(ids)))}
    return [rows[i] for i in ids if i in rows]


def most_read(limit: int = 5, days: int = 7, exclude=()) -> list[Article]:
    since = utcnow() - timedelta(days=days)
    q = (select(PageView.article_id, func.count().label("n")).where(PageView.article_id.isnot(None),
                                                                   PageView.created_at >= since)
         .group_by(PageView.article_id).order_by(desc("n")).limit(limit * 3))
    ids = [r[0] for r in db.session.execute(q) if r[0] not in exclude]
    out = _ordered(ids)[:limit]
    if len(out) < limit:  # new site / quiet week: fall back to all-time views, then recency
        have = {a.id for a in out} | set(exclude)
        stmt = live_stmt().where(Article.id.notin_(list(have)) if have else true()).order_by(None) \
            .order_by(Article.view_count.desc(), Article.published_at.desc()).limit(limit - len(out))
        out += list(db.session.scalars(stmt))
    return out


def trending(limit: int = 5, exclude=()) -> list[Article]:
    return most_read(limit=limit, days=2, exclude=exclude)


def related(a: Article, limit: int = 3) -> list[Article]:
    tag_ids = [t.id for t in a.tags]
    same_tag = exists().where(article_tags.c.article_id == Article.id, article_tags.c.tag_id.in_(tag_ids)) if tag_ids else None
    cond = or_(Article.category_id == a.category_id, same_tag) if same_tag is not None else (Article.category_id == a.category_id)
    cands = list(db.session.scalars(live_stmt(cond, Article.id != a.id).options(selectinload(Article.tags)).limit(20)))
    mine = set(tag_ids)

    def score(c):
        return 2 * len(mine & {t.id for t in c.tags}) + (1 if c.category_id == a.category_id else 0)
    cands.sort(key=lambda c: (-score(c), -(c.published_at.timestamp() if c.published_at else 0)))
    return cands[:limit]


def neighbors(a: Article) -> tuple:
    """(older, newer) live articles in the same category, for previous/next navigation."""
    if not a.category_id or not a.published_at:
        return None, None
    older = db.session.scalar(live_stmt(Article.category_id == a.category_id, Article.published_at < a.published_at,
                                        Article.id != a.id).limit(1))
    newer = db.session.scalar(select(Article).options(*EAGER).where(
        Article.live_filter(), Article.category_id == a.category_id, Article.published_at > a.published_at,
        Article.id != a.id).order_by(Article.published_at.asc()).limit(1))
    return older, newer


def breaking(hours: int = 24, limit: int = 3) -> list[Article]:
    return fetch(limit, Article.is_breaking.is_(True), Article.published_at >= utcnow() - timedelta(hours=hours))


PERIODS = {"7": 7, "30": 30, "365": 365}


def search_stmt(q: str, category_id=None, article_type: str = "", period: str = "", sort: str = "relevance"):
    like = f"%{escape_like(q)}%"
    text_match = or_(Article.title.ilike(like, escape="\\"), Article.subtitle.ilike(like, escape="\\"),
                     Article.excerpt.ilike(like, escape="\\"), Article.body_text.ilike(like, escape="\\"))
    author_match = Article.author.has(Author.name.ilike(like, escape="\\"))
    cat_match = Article.category.has(Category.name.ilike(like, escape="\\"))
    tag_match = Article.tags.any(Tag.name.ilike(like, escape="\\"))
    stmt = select(Article).options(*EAGER).where(Article.live_filter(), or_(text_match, author_match, cat_match, tag_match))
    if category_id:
        stmt = stmt.where(Article.category_id == category_id)
    if article_type:
        stmt = stmt.where(Article.article_type == article_type)
    if period in PERIODS:
        stmt = stmt.where(Article.published_at >= utcnow() - timedelta(days=PERIODS[period]))
    if sort == "date":
        return stmt.order_by(Article.published_at.desc())
    rank = case((Article.title.ilike(like, escape="\\"), 0), (tag_match, 1), (author_match, 1), (cat_match, 1), else_=2)
    return stmt.order_by(rank, Article.published_at.desc())


def search_facets(q: str) -> dict:
    like = f"%{escape_like(q)}%"
    return {
        "authors": list(db.session.scalars(select(Author).where(Author.active.is_(True), Author.name.ilike(like, escape="\\")).limit(4))),
        "categories": list(db.session.scalars(select(Category).where(Category.active.is_(True), Category.name.ilike(like, escape="\\")).limit(4))),
        "tags": list(db.session.scalars(select(Tag).where(Tag.name.ilike(like, escape="\\")).limit(6))),
    }
