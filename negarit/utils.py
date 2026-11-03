import re
import secrets
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import markdown as _markdown
import nh3
from flask import current_app

ALLOWED_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4", "strong", "em", "b", "i", "u", "s", "blockquote", "code", "pre",
    "ul", "ol", "li", "a", "img", "figure", "figcaption", "table", "thead", "tbody", "tr", "th", "td", "sup", "sub",
    "mark", "small", "cite",
}
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height", "loading"},
    "th": {"align"}, "td": {"align"},
}


def utcnow() -> datetime:
    """Naive UTC 'now' (all DB datetimes are naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def site_tz() -> ZoneInfo:
    return ZoneInfo(current_app.config["SITE_TIMEZONE"])


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(site_tz())


def from_local_input(value: str) -> datetime | None:
    """Parse an <input type=datetime-local> value (site timezone) -> naive UTC."""
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value).replace(tzinfo=site_tz())
    except ValueError:
        return None
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def slugify(text: str, max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)  # keeps Ge'ez/Amharic letters
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len].strip("-")


def unique_slug(model, base: str, exclude_id: int | None = None) -> str:
    base = slugify(base) or secrets.token_hex(4)
    slug, n = base, 2
    while True:
        q = model.query.filter(model.slug == slug)
        if exclude_id:
            q = q.filter(model.id != exclude_id)
        if not q.first():
            return slug
        slug = f"{base}-{n}"
        n += 1


def render_markdown(md_text: str) -> str:
    html = _markdown.markdown(md_text or "", extensions=["extra", "sane_lists"])
    return nh3.clean(
        html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS,
        url_schemes={"http", "https", "mailto"}, link_rel="noopener noreferrer",
    )


def clean_editor_html(html: str) -> str:
    """Sanitise HTML from the rich-text editor (allow-list), drop empty paragraphs, force safe link attributes."""
    cleaned = nh3.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, url_schemes={"http", "https", "mailto"},
                        link_rel="noopener noreferrer")
    cleaned = re.sub(r"<p>(\s|&nbsp;|<br\s*/?>)*</p>", "", cleaned)
    return cleaned.strip()


def html_to_text(html: str) -> str:
    return re.sub(r"\s+", " ", nh3.clean(html or "", tags=set())).strip()


def reading_minutes(text: str) -> int:
    return max(1, round(len((text or "").split()) / 220))


RESERVED_SLUGS = {"data", "news", "analysis", "opinion", "explainers", "interviews", "data-reports", "sponsored", "features",
                  "admin", "api", "static", "search", "contact", "author", "authors", "tag", "tags", "type", "media",
                  "newsletter", "cron", "rss", "rss.xml", "sitemap", "sitemap.xml", "robots.txt", "theme.css", "ad",
                  "comments", "article", "story", "favicon.svg", "news-sitemap.xml", "healthz", "login", "logout"}


def slug_taken(slug: str, exclude=None) -> bool:
    """Category and Page slugs share the top-level URL namespace."""
    from .models import Category, Page
    if slug in RESERVED_SLUGS:
        return True
    for model in (Category, Page):
        row = model.query.filter_by(slug=slug).first()
        if row and row is not exclude:
            return True
    return False


def is_safe_next(target: str | None) -> bool:
    return bool(target) and target.startswith("/") and not target.startswith("//") and "\\" not in target


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
