"""First-party page-view analytics: cookieless, no IP stored."""
import re
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import desc, func, select, update

from ..extensions import db
from ..models import Article, Author, Category, PageView, Subscriber
from ..utils import utcnow

BOT = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|headless|lighthouse|pingdom|monitor|curl|wget|python-requests",
                 re.I)
SEARCH = ("google.", "bing.", "duckduckgo.", "yahoo.", "yandex.", "baidu.", "ecosia.", "brave.")
SOCIAL = ("facebook.", "fb.", "t.co", "twitter.", "x.com", "linkedin.", "lnkd.in", "t.me", "telegram.", "whatsapp.",
          "instagram.", "youtube.", "youtu.be", "tiktok.")


def classify(referrer: str, own_host: str) -> tuple[str, str]:
    if not referrer:
        return "direct", ""
    host = (urlparse(referrer).hostname or "").lower().removeprefix("www.")
    if not host:
        return "direct", ""
    if host == own_host.lower().removeprefix("www."):
        return "internal", host
    if any(s in host for s in SEARCH):
        return "search", host[:120]
    if any(s in host for s in SOCIAL):
        return "social", host[:120]
    return "referral", host[:120]


def record(path: str, article_id, referrer: str, own_host: str, user_agent: str) -> bool:
    if not user_agent or BOT.search(user_agent):
        return False
    art = db.session.get(Article, article_id) if article_id else None
    if art is not None and not art.is_live:
        return False
    source, host = classify(referrer or "", own_host)
    db.session.add(PageView(path=(path or "/")[:300], article_id=art.id if art else None,
                            category_id=art.category_id if art else None, author_id=art.author_id if art else None,
                            source=source, referrer_host=host))
    if art:
        db.session.execute(update(Article).where(Article.id == art.id).values(view_count=Article.view_count + 1))
    db.session.commit()
    return True


def overview() -> dict:
    now = utcnow()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total_views = db.session.scalar(select(func.count()).select_from(PageView)) or 0
    today = db.session.scalar(select(func.count()).select_from(PageView).where(PageView.created_at >= day0)) or 0
    return {"total_views": total_views, "today_views": today,
            "subscribers": db.session.scalar(select(func.count()).select_from(Subscriber).where(Subscriber.status == "confirmed")) or 0}


def report(start=None, end=None, days: int = 30) -> dict:
    """Aggregates for [start, end] (UTC dates, inclusive). Defaults to the last `days` days."""
    from datetime import date as _date
    end = end or utcnow().date()
    start = start or (end - timedelta(days=days - 1))
    if start > end:
        start, end = end, start
    if (end - start).days > 730:
        start = end - timedelta(days=730)
    lo = datetime_start(start)
    hi = datetime_start(end + timedelta(days=1))
    span = (end - start).days + 1
    daily = {}
    for r in db.session.execute(select(func.date(PageView.created_at), func.count()).where(PageView.created_at >= lo, PageView.created_at < hi)
                                .group_by(func.date(PageView.created_at))):
        daily[str(r[0])] = r[1]
    series = [((start + timedelta(days=i)).isoformat(), daily.get((start + timedelta(days=i)).isoformat(), 0)) for i in range(span)]

    def top(col, model, limit=8):
        q = (select(model, func.count().label("n")).join(PageView, PageView.__table__.c[col] == model.id)
             .where(PageView.created_at >= lo, PageView.created_at < hi).group_by(model.id).order_by(desc("n")).limit(limit))
        return [(m, n) for m, n in db.session.execute(q)]

    sources = list(db.session.execute(select(PageView.source, func.count().label("n")).where(PageView.created_at >= lo, PageView.created_at < hi)
                                      .group_by(PageView.source).order_by(desc("n"))))
    referrers = list(db.session.execute(select(PageView.referrer_host, func.count().label("n"))
                                        .where(PageView.created_at >= lo, PageView.created_at < hi, PageView.referrer_host != "")
                                        .group_by(PageView.referrer_host).order_by(desc("n")).limit(8)))
    # publishing activity: articles that went live in the range, per ISO week
    weeks = {}
    for (pub,) in db.session.execute(select(Article.published_at).where(Article.live_filter(), Article.published_at >= lo, Article.published_at < hi)):
        monday = (pub.date() - timedelta(days=pub.weekday())).isoformat()
        weeks[monday] = weeks.get(monday, 0) + 1
    # subscriber growth: sign-ups per day, confirmed total at the end of each day
    signups, total_before = {}, db.session.scalar(select(func.count()).select_from(Subscriber).where(Subscriber.created_at < lo)) or 0
    for (created,) in db.session.execute(select(Subscriber.created_at).where(Subscriber.created_at >= lo, Subscriber.created_at < hi)):
        signups[created.date().isoformat()] = signups.get(created.date().isoformat(), 0) + 1
    growth, running = [], total_before
    for d, _v in series:
        running += signups.get(d, 0)
        growth.append((d, signups.get(d, 0), running))
    return {"start": start, "end": end, "series": series, "total": sum(v for _d, v in series),
            "articles": top("article_id", Article), "categories": top("category_id", Category), "authors": top("author_id", Author),
            "sources": sources, "referrers": referrers, "publishing": sorted(weeks.items()),
            "published_total": sum(weeks.values()), "growth": growth, "new_subscribers": sum(signups.values())}


def datetime_start(d):
    from datetime import datetime as _dt
    return _dt(d.year, d.month, d.day)
