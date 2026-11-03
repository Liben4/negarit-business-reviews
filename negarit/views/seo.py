from datetime import timezone, timedelta
from email.utils import format_datetime
from xml.sax.saxutils import escape

from flask import Blueprint, Response, abort, current_app, jsonify, send_from_directory
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import TYPE_ROUTES, Article, Author, Category, Dataset, Page, Tag
from ..services import content, settings
from ..utils import utcnow
from . import cached

bp = Blueprint("seo", __name__)
PUBLIC_DIR = None


def _xml(body: str, ctype="application/xml") -> Response:
    return Response(body, mimetype=ctype, headers={"Content-Type": f"{ctype}; charset=utf-8"})


def _is_preview() -> bool:
    c = current_app.config
    return c["ON_VERCEL"] and not c["IS_PRODUCTION_DEPLOY"]


def _real_live():
    return (Article.live_filter(), Article.is_demo.is_(False))  # demo/sample content is never syndicated


@bp.get("/robots.txt")
@cached(3600, 86400)
def robots():
    base = current_app.config["SITE_URL"]
    if _is_preview():
        return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")
    return Response("User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nDisallow: /cron/\nDisallow: /ad/\n"
                    "Disallow: /search\nDisallow: /newsletter/\nDisallow: /media/\nDisallow: /comments/\n\n"
                    f"Sitemap: {base}/sitemap.xml\nSitemap: {base}/news-sitemap.xml\n", mimetype="text/plain")


@bp.get("/theme.css")
def theme_css():
    s = settings.all_settings()
    css = (f":root{{--brand:{s['brand_primary']};--accent:{s['brand_accent']};}}"
           f":root[data-theme=dark]{{--accent:{s['brand_accent_dark']};}}"
           f"@media(prefers-color-scheme:dark){{:root:not([data-theme=light]){{--accent:{s['brand_accent_dark']};}}}}")
    r = Response(css, mimetype="text/css")
    r.headers["Cache-Control"] = "public, max-age=300, s-maxage=300"
    return r


@bp.get("/sitemap.xml")
@cached(600, 3600)
def sitemap():
    base = current_app.config["SITE_URL"]
    live = _real_live()
    urls = [(f"{base}/", None, None), (f"{base}/authors", None, None), (f"{base}/contact", None, None), (f"{base}/data", None, None)]
    for atype, route in TYPE_ROUTES.items():
        if db.session.scalar(select(func.count()).select_from(Article).where(*live, Article.article_type == atype)):
            urls.append((f"{base}/{route}", None, None))
    for ds in Dataset.query.filter_by(published=True, is_demo=False):
        if ds.points:
            urls.append((f"{base}/data/{ds.slug}", ds.updated_at, None))
    for c, last in db.session.execute(select(Category, func.max(Article.updated_at)).join(Article).where(*live, Category.active.is_(True)).group_by(Category.id)):
        urls.append((f"{base}/{c.slug}", last, None))
    for au, last in db.session.execute(select(Author, func.max(Article.updated_at)).join(Article, Article.author_id == Author.id)
                                       .where(*live, Author.active.is_(True)).group_by(Author.id)):
        urls.append((f"{base}/author/{au.slug}", last, None))
    for t, last in db.session.execute(select(Tag, func.max(Article.updated_at)).join(Tag.articles).where(*live).group_by(Tag.id)):
        urls.append((f"{base}/tag/{t.slug}", last, None))
    for p in Page.query.filter_by(published=True):
        urls.append((f"{base}/{p.slug}", p.updated_at, None))
    arts = db.session.scalars(select(Article).options(joinedload(Article.category), joinedload(Article.featured_media))
                              .where(*live).order_by(Article.published_at.desc()).limit(45000)).all()
    for a in arts:
        if a.category:
            img = a.featured_media.url if a.featured_media and a.featured_media.url.startswith("https://") else None
            urls.append((f"{base}{a.url_path}", a.updated_at, img))
    rows = "".join(
        f"<url><loc>{escape(loc)}</loc>" + (f"<lastmod>{last.strftime('%Y-%m-%dT%H:%M:%SZ')}</lastmod>" if last else "")
        + (f"<image:image><image:loc>{escape(img)}</image:loc></image:image>" if img else "") + "</url>"
        for loc, last, img in urls)
    return _xml('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                f'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">{rows}</urlset>')


@bp.get("/news-sitemap.xml")
@cached(300, 900)
def news_sitemap():
    """Google News sitemap: articles from the last 48 hours."""
    base = current_app.config["SITE_URL"]
    name = escape(settings.get("site_name"))
    since = utcnow() - timedelta(hours=48)
    arts = db.session.scalars(select(Article).options(joinedload(Article.category)).where(*_real_live(), Article.published_at >= since)
                              .order_by(Article.published_at.desc()).limit(1000)).all()
    rows = "".join(
        f"<url><loc>{escape(base + a.url_path)}</loc><news:news><news:publication><news:name>{name}</news:name>"
        f"<news:language>en</news:language></news:publication><news:publication_date>{a.published_at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"</news:publication_date><news:title>{escape(a.title)}</news:title></news:news></url>" for a in arts if a.category)
    return _xml('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                f'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">{rows}</urlset>')


@bp.get("/rss.xml")
@cached(300, 1800)
def rss():
    cfg = current_app.config
    base = cfg["SITE_URL"]
    arts = db.session.scalars(content.live_stmt(Article.is_demo.is_(False)).limit(30)).all()
    items = []
    for a in arts:
        if not a.category:
            continue
        url = base + a.url_path
        items.append(
            f"<item><title>{escape(a.title)}</title><link>{escape(url)}</link><guid isPermaLink=\"true\">{escape(url)}</guid>"
            f"<pubDate>{format_datetime(a.published_at.replace(tzinfo=timezone.utc))}</pubDate>"
            f"<dc:creator>{escape(a.author.name if a.author else '')}</dc:creator><category>{escape(a.category.name)}</category>"
            f"<description>{escape(a.summary)}</description><content:encoded>{escape(a.body_html)}</content:encoded></item>")
    built = format_datetime(utcnow().replace(tzinfo=timezone.utc))
    return _xml(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<channel><title>{escape(settings.get('site_name'))}</title><link>{escape(base)}/</link>"
        f"<description>{escape(settings.get('site_description'))}</description><language>en</language>"
        f"<lastBuildDate>{built}</lastBuildDate><atom:link href=\"{escape(base)}/rss.xml\" rel=\"self\" type=\"application/rss+xml\"/>"
        + "".join(items) + "</channel></rss>", "application/rss+xml")


@bp.get("/api/health")
def health():
    from sqlalchemy import text
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify(status="ok")
    except Exception:
        current_app.logger.exception("health check failed")
        return jsonify(status="error"), 503
