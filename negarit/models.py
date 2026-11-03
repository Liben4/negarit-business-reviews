import json
import secrets

from flask_login import UserMixin
from sqlalchemy import Index, and_, or_
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager
from .permissions import ROLES, has_permission
from .utils import html_to_text, reading_minutes, render_markdown, utcnow

ARTICLE_TYPES = (("news", "News"), ("analysis", "Analysis"), ("opinion", "Opinion"), ("explainer", "Explainer"),
                 ("interview", "Interview"), ("data-report", "Data Report"), ("sponsored", "Sponsored Content"),
                 ("feature", "Feature"))
TYPE_LABELS = dict(ARTICLE_TYPES)
# URL slug of each type's listing page (top-level, e.g. /news, /opinion)
TYPE_ROUTES = {"news": "news", "analysis": "analysis", "opinion": "opinion", "explainer": "explainers",
               "interview": "interviews", "data-report": "data-reports", "sponsored": "sponsored", "feature": "features"}
ROUTE_TYPES = {v: k for k, v in TYPE_ROUTES.items()}
STATUSES = ("draft", "review", "approved", "scheduled", "published", "archived")
STATUS_LABELS = {"draft": "Draft", "review": "Submitted for review", "approved": "Approved", "scheduled": "Scheduled",
                 "published": "Published", "archived": "Archived"}
DATA_FREQUENCIES = (("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"), ("quarterly", "Quarterly"),
                    ("annual", "Annual"))
# Days since the period START beyond which a series is flagged "not recent". Allows for normal publication lag
# (a monthly figure for September usually appears in October).
FREQ_DAYS = {"daily": 7, "weekly": 21, "monthly": 75, "quarterly": 165, "annual": 450}
HOME_KINDS = (("latest", "Latest news"), ("top", "Top stories"), ("picks", "Editor's picks"), ("category", "Category"),
              ("type", "Content type"), ("data", "Economic data & reports"), ("most_read", "Most read"))
AD_PLACEMENTS = (("banner", "Banner (top of pages)"), ("sidebar", "Sidebar"), ("article", "Inside articles"),
                 ("homepage", "Homepage"), ("sponsored", "Sponsored placement"))
INQUIRY_TYPES = (("general", "General Inquiry"), ("tip", "News Tip"), ("advertising", "Advertising"),
                 ("partnership", "Partnership"), ("careers", "Careers"), ("editorial", "Editorial"),
                 ("pitch", "Write for Us / Story Pitch"), ("correction", "Correction Request"))


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="writer")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    author = db.relationship("Author", back_populates="user", uselist=False)

    @property
    def is_active(self):  # Flask-Login
        return self.active

    def can(self, perm: str) -> bool:
        return self.active and has_permission(self.role, perm)

    def set_password(self, pw: str):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)


@login_manager.user_loader
def load_user(user_id):
    u = db.session.get(User, int(user_id)) if str(user_id).isdigit() else None
    return u if u and u.active else None


class Media(db.Model):
    __tablename__ = "media"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False, default="")
    alt_text = db.Column(db.String(300), nullable=False, default="")
    caption = db.Column(db.String(300), nullable=False, default="")
    credit = db.Column(db.String(200), nullable=False, default="")
    url = db.Column(db.String(600), nullable=False)          # largest variant
    variants = db.Column(db.Text, nullable=False, default="{}")  # {"480": url, ...}
    storage_keys = db.Column(db.Text, nullable=False, default="[]")
    width = db.Column(db.Integer, nullable=False, default=0)
    height = db.Column(db.Integer, nullable=False, default=0)
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    content_type = db.Column(db.String(50), nullable=False, default="image/webp")
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    uploaded_by = db.relationship("User")

    @property
    def variant_map(self) -> dict:
        try:
            return {int(k): v for k, v in json.loads(self.variants or "{}").items()}
        except ValueError:
            return {}

    @property
    def keys(self) -> list:
        try:
            return json.loads(self.storage_keys or "[]")
        except ValueError:
            return []

    @property
    def srcset(self) -> str:
        return ", ".join(f"{u} {w}w" for w, u in sorted(self.variant_map.items()))

    def url_for_width(self, width: int) -> str:
        m = self.variant_map
        for w in sorted(m):
            if w >= width:
                return m[w]
        return self.url

    @property
    def thumb_url(self) -> str:
        return self.url_for_width(480)


class Author(db.Model):
    __tablename__ = "authors"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(140), nullable=False, unique=True)
    position = db.Column(db.String(160), nullable=False, default="")
    expertise = db.Column(db.String(300), nullable=False, default="")
    bio = db.Column(db.Text, nullable=False, default="")
    image_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="SET NULL"))
    twitter = db.Column(db.String(300), nullable=False, default="")
    linkedin = db.Column(db.String(300), nullable=False, default="")
    facebook = db.Column(db.String(300), nullable=False, default="")
    telegram = db.Column(db.String(300), nullable=False, default="")
    website = db.Column(db.String(300), nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    image = db.relationship("Media")
    user = db.relationship("User", back_populates="author")
    articles = db.relationship("Article", back_populates="author")

    @property
    def socials(self):
        pairs = (("X", self.twitter), ("LinkedIn", self.linkedin), ("Facebook", self.facebook),
                 ("Telegram", self.telegram), ("Website", self.website))
        return [(label, url) for label, url in pairs if url]


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    slug = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(300), nullable=False, default="")
    is_primary = db.Column(db.Boolean, nullable=False, default=False)  # shown in main nav (else "More")
    sort_order = db.Column(db.Integer, nullable=False, default=100)
    seo_title = db.Column(db.String(120), nullable=False, default="")
    meta_description = db.Column(db.String(320), nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True)  # inactive: hidden from nav/pickers; old URLs keep working
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id", ondelete="SET NULL"))
    articles = db.relationship("Article", back_populates="category")
    parent = db.relationship("Category", remote_side="Category.id", back_populates="children")
    children = db.relationship("Category", back_populates="parent", order_by="Category.sort_order")

    @property
    def family_ids(self) -> list:
        return [self.id] + [c.id for c in self.children]


article_tags = db.Table(
    "article_tags",
    db.Column("article_id", db.Integer, db.ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(db.Model):
    __tablename__ = "tags"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False, unique=True)
    slug = db.Column(db.String(80), nullable=False, unique=True)
    description = db.Column(db.String(300), nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True)
    articles = db.relationship("Article", secondary=article_tags, back_populates="tags")


class ArticleMedia(db.Model):
    __tablename__ = "article_media"
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    media_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="CASCADE"), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    media = db.relationship("Media")


class Article(db.Model):
    __tablename__ = "articles"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(200), nullable=False, unique=True)
    subtitle = db.Column(db.String(300), nullable=False, default="")       # deck
    excerpt = db.Column(db.String(400), nullable=False, default="")        # cards/search
    body_md = db.Column(db.Text, nullable=False, default="")
    body_html = db.Column(db.Text, nullable=False, default="")
    body_text = db.Column(db.Text, nullable=False, default="")
    sources_md = db.Column(db.Text, nullable=False, default="")
    sources_html = db.Column(db.Text, nullable=False, default="")
    article_type = db.Column(db.String(16), nullable=False, default="news")
    status = db.Column(db.String(12), nullable=False, default="draft")
    access_level = db.Column(db.String(10), nullable=False, default="free")  # future NBR Premium
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    is_breaking = db.Column(db.Boolean, nullable=False, default=False)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    sponsor_name = db.Column(db.String(160), nullable=False, default="")  # sponsored content disclosure
    review_note = db.Column(db.String(500), nullable=False, default="")
    featured_media_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="SET NULL"))
    cover_caption = db.Column(db.String(300), nullable=False, default="")
    seo_title = db.Column(db.String(120), nullable=False, default="")
    meta_description = db.Column(db.String(320), nullable=False, default="")
    canonical_override = db.Column(db.String(500), nullable=False, default="")
    og_title = db.Column(db.String(120), nullable=False, default="")
    og_description = db.Column(db.String(320), nullable=False, default="")
    view_count = db.Column(db.Integer, nullable=False, default=0)
    published_at = db.Column(db.DateTime)  # naive UTC
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)  # set on editorial edits only
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"))
    author_id = db.Column(db.Integer, db.ForeignKey("authors.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    category = db.relationship("Category", back_populates="articles")
    author = db.relationship("Author", back_populates="articles")
    featured_media = db.relationship("Media", foreign_keys=[featured_media_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    tags = db.relationship("Tag", secondary=article_tags, back_populates="articles", order_by="Tag.name")
    gallery = db.relationship("ArticleMedia", order_by="ArticleMedia.position", cascade="all, delete-orphan")
    comments = db.relationship("Comment", cascade="all, delete-orphan", back_populates="article")

    __table_args__ = (Index("ix_articles_status_published_at", "status", "published_at"),
                      Index("ix_articles_category_published_at", "category_id", "published_at"),
                      Index("ix_articles_type_published_at", "article_type", "published_at"))

    @staticmethod
    def live_filter():
        """Visibility is decided by TIME, not by a background job."""
        return and_(Article.status.in_(("published", "scheduled")), Article.published_at.isnot(None),
                    Article.published_at <= utcnow())

    @property
    def is_live(self):
        return self.status in ("published", "scheduled") and self.published_at is not None \
            and self.published_at <= utcnow()

    @property
    def url_path(self) -> str:
        return f"/{self.category.slug}/{self.slug}" if self.category else f"/story/{self.slug}"

    @property
    def type_label(self) -> str:
        return TYPE_LABELS.get(self.article_type, "News")

    @property
    def minutes(self) -> int:
        return reading_minutes(self.body_text)

    @property
    def summary(self) -> str:
        return self.excerpt or self.subtitle or (self.body_text[:180].rsplit(" ", 1)[0] + "…" if self.body_text else "")

    @property
    def description(self) -> str:
        return self.meta_description or self.excerpt or self.subtitle or self.body_text[:155]

    def set_body(self, md_text: str):
        """Legacy/API path: Markdown in, sanitised HTML out."""
        self.body_md = md_text
        self.body_html = render_markdown(md_text)
        self.body_text = html_to_text(self.body_html)

    def set_body_html(self, html: str):
        """Rich-text editor path: HTML is sanitised against an allow-list before it is stored."""
        from .utils import clean_editor_html
        self.body_html = clean_editor_html(html)
        self.body_text = html_to_text(self.body_html)

    def set_sources(self, md_text: str):
        self.sources_md = md_text
        self.sources_html = render_markdown(md_text) if md_text.strip() else ""


class ArticleRevision(db.Model):
    """Snapshot taken on every manual save and workflow action (autosaves do not create revisions)."""
    __tablename__ = "article_revisions"
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    user_name = db.Column(db.String(120), nullable=False, default="")
    kind = db.Column(db.String(24), nullable=False, default="save")
    status = db.Column(db.String(12), nullable=False, default="draft")
    title = db.Column(db.String(255), nullable=False, default="")
    subtitle = db.Column(db.String(300), nullable=False, default="")
    excerpt = db.Column(db.String(400), nullable=False, default="")
    body_html = db.Column(db.Text, nullable=False, default="")
    sources_md = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class EditorialNote(db.Model):
    """Internal notes between writers and editors (never public)."""
    __tablename__ = "editorial_notes"
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    user_name = db.Column(db.String(120), nullable=False, default="")
    kind = db.Column(db.String(20), nullable=False, default="note")  # note | revision_request | approval
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class HomeSection(db.Model):
    """Editor-controlled homepage blocks: order, visibility, size."""
    __tablename__ = "home_sections"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(12), nullable=False)
    title = db.Column(db.String(120), nullable=False, default="")
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id", ondelete="CASCADE"))
    article_type = db.Column(db.String(60), nullable=False, default="")  # comma-separated for kind="type"
    position = db.Column(db.Integer, nullable=False, default=100)
    visible = db.Column(db.Boolean, nullable=False, default=True)
    item_limit = db.Column(db.Integer, nullable=False, default=4)
    category = db.relationship("Category")


class HomeSlot(db.Model):
    """Hand-picked homepage stories: the lead story and the ordered editor's picks."""
    __tablename__ = "home_slots"
    id = db.Column(db.Integer, primary_key=True)
    slot = db.Column(db.String(8), nullable=False)  # lead | picks
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    article = db.relationship("Article")
    __table_args__ = (db.UniqueConstraint("slot", "article_id", name="uq_home_slots_slot_article"),)


class Dataset(db.Model):
    """An economic/business indicator series. Values are entered by staff; nothing is fabricated or fetched silently."""
    __tablename__ = "datasets"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(120), nullable=False, unique=True)
    topic = db.Column(db.String(60), nullable=False, default="General")
    description = db.Column(db.Text, nullable=False, default="")
    unit = db.Column(db.String(80), nullable=False, default="")
    frequency = db.Column(db.String(12), nullable=False, default="monthly")
    decimals = db.Column(db.Integer, nullable=False, default=1)
    source_name = db.Column(db.String(200), nullable=False, default="")
    source_url = db.Column(db.String(500), nullable=False, default="")
    context = db.Column(db.Text, nullable=False, default="")
    published = db.Column(db.Boolean, nullable=False, default=True)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    points = db.relationship("DataPoint", order_by="DataPoint.period", cascade="all, delete-orphan", back_populates="dataset")


class DataPoint(db.Model):
    __tablename__ = "data_points"
    id = db.Column(db.Integer, primary_key=True)
    dataset_id = db.Column(db.Integer, db.ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    period = db.Column(db.Date, nullable=False)
    value = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(200), nullable=False, default="")
    dataset = db.relationship("Dataset", back_populates="points")
    __table_args__ = (db.UniqueConstraint("dataset_id", "period", name="uq_data_points_dataset_period"),)


class Report(db.Model):
    """A downloadable report: a link to a file hosted on your storage/CDN or the publisher's site."""
    __tablename__ = "reports"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text, nullable=False, default="")
    topic = db.Column(db.String(60), nullable=False, default="General")
    file_url = db.Column(db.String(600), nullable=False)
    source_name = db.Column(db.String(200), nullable=False, default="")
    published_on = db.Column(db.Date)
    published = db.Column(db.Boolean, nullable=False, default=True)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Page(db.Model):
    """CMS-managed static/legal pages."""
    __tablename__ = "pages"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(120), nullable=False, unique=True)
    body_md = db.Column(db.Text, nullable=False, default="")
    body_html = db.Column(db.Text, nullable=False, default="")
    seo_title = db.Column(db.String(120), nullable=False, default="")
    meta_description = db.Column(db.String(320), nullable=False, default="")
    published = db.Column(db.Boolean, nullable=False, default=True)
    in_footer = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=100)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def set_body(self, md_text: str):
        self.body_md = md_text
        self.body_html = render_markdown(md_text)


class Subscriber(db.Model):
    __tablename__ = "subscribers"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    status = db.Column(db.String(14), nullable=False, default="pending")  # pending|confirmed|unsubscribed
    token = db.Column(db.String(64), nullable=False, unique=True, default=lambda: secrets.token_urlsafe(32))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    confirmed_at = db.Column(db.DateTime)


class Advertisement(db.Model):
    __tablename__ = "advertisements"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)               # internal name / alt text
    advertiser = db.Column(db.String(160), nullable=False)
    media_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="SET NULL"))
    headline = db.Column(db.String(200), nullable=False, default="")  # sponsored text placements
    dest_url = db.Column(db.String(600), nullable=False)
    placement = db.Column(db.String(16), nullable=False)
    campaign = db.Column(db.String(160), nullable=False, default="")
    start_at = db.Column(db.DateTime)
    end_at = db.Column(db.DateTime)
    active = db.Column(db.Boolean, nullable=False, default=True)
    clicks = db.Column(db.Integer, nullable=False, default=0)
    impressions = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    media = db.relationship("Media")

    @staticmethod
    def running_filter():
        now = utcnow()
        return and_(Advertisement.active.is_(True),
                    or_(Advertisement.start_at.is_(None), Advertisement.start_at <= now),
                    or_(Advertisement.end_at.is_(None), Advertisement.end_at > now))

    @property
    def is_running(self):
        now = utcnow()
        return self.active and (not self.start_at or self.start_at <= now) and (not self.end_at or self.end_at > now)


class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(255), nullable=False, default="")
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(10), nullable=False, default="pending")  # pending|approved|spam
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    article = db.relationship("Article", back_populates="comments")


class SiteSetting(db.Model):
    __tablename__ = "site_settings"
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    user_email = db.Column(db.String(255), nullable=False, default="")
    action = db.Column(db.String(60), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False, default="")
    entity_id = db.Column(db.Integer)
    summary = db.Column(db.String(400), nullable=False, default="")
    ip = db.Column(db.String(45), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user = db.relationship("User")


class PageView(db.Model):
    """First-party, cookieless, IP-less page-view record."""
    __tablename__ = "page_views"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    path = db.Column(db.String(300), nullable=False)
    article_id = db.Column(db.Integer)
    category_id = db.Column(db.Integer)
    author_id = db.Column(db.Integer)
    source = db.Column(db.String(12), nullable=False, default="direct")  # direct|search|social|referral|internal
    referrer_host = db.Column(db.String(120), nullable=False, default="")
    __table_args__ = (Index("ix_page_views_created_at", "created_at"),
                      Index("ix_page_views_article_created", "article_id", "created_at"))


class ContactMessage(db.Model):
    __tablename__ = "contact_messages"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    inquiry_type = db.Column(db.String(16), nullable=False, default="general")
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(10), nullable=False, default="new")  # new|read|archived
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class LoginAttempt(db.Model):
    """DB-backed throttle: works across serverless instances without Redis."""
    __tablename__ = "login_attempts"
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(45), nullable=False)
    email = db.Column(db.String(255), nullable=False, default="")
    success = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    __table_args__ = (Index("ix_login_attempts_ip_created_at", "ip", "created_at"),
                      Index("ix_login_attempts_email_created_at", "email", "created_at"))
