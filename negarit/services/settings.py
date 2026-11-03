"""DB-backed site settings with in-code defaults and a short per-process cache."""
import time

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import SiteSetting
from ..utils import utcnow

# (group, key, label, kind, default, help)   kind: text|textarea|url|email|color|bool|select:a,b,c|media_url
FIELDS = [
    ("General", "site_name", "Site name", "text", "Negarit Business Review", ""),
    ("General", "tagline", "Tagline", "text", "Navigating Business Dynamics", ""),
    ("General", "logo_url", "Logo", "media_url", "", "Leave empty to use the text wordmark."),
    ("General", "favicon_url", "Favicon", "media_url", "", "Square PNG/WebP. Empty = built-in icon."),
    ("General", "contact_email", "Contact email", "email", "", "Contact-form messages are emailed here."),
    ("General", "contact_phone", "Contact phone", "text", "", ""),
    ("General", "address", "Address", "textarea", "", ""),
    ("Social media", "social_facebook", "Facebook URL", "url", "", ""),
    ("Social media", "social_x", "X (Twitter) URL", "url", "", ""),
    ("Social media", "social_linkedin", "LinkedIn URL", "url", "", ""),
    ("Social media", "social_youtube", "YouTube URL", "url", "", ""),
    ("Social media", "social_telegram", "Telegram URL", "url", "", ""),
    ("SEO", "site_description", "Site description", "textarea",
     "Business, economy, finance and technology news, analysis and data from Ethiopia and Africa.", "Used as the default meta description."),
    ("SEO", "default_seo_title", "Default SEO title", "text", "Negarit Business Review — Navigating Business Dynamics", ""),
    ("SEO", "default_og_image", "Default social image", "media_url", "", "1200×630 recommended. Empty = built-in image."),
    ("SEO", "google_site_verification", "Google Search Console verification code", "text", "", "Only the content value of the meta tag."),
    ("SEO", "bing_site_verification", "Bing verification code", "text", "", ""),
    ("SEO", "ga_measurement_id", "Google Analytics 4 measurement ID", "text", "", "e.g. G-XXXXXXXXXX. Loads only after cookie consent."),
    ("Appearance", "default_theme", "Default theme", "select:system,light,dark", "system", "Visitors can still switch."),
    ("Appearance", "brand_primary", "Brand colour (ink/navy)", "color", "#10233F", ""),
    ("Appearance", "brand_accent", "Accent colour (light theme)", "color", "#9A5B00", "Must keep contrast ≥ 4.5:1 on white."),
    ("Appearance", "brand_accent_dark", "Accent colour (dark theme)", "color", "#F0B429", ""),
    ("Newsletter", "newsletter_enabled", "Show newsletter forms", "bool", "1", ""),
    ("Newsletter", "newsletter_heading", "Heading", "text", "Stay Ahead of Business Dynamics", ""),
    ("Newsletter", "newsletter_text", "Supporting text", "textarea",
     "A concise weekly briefing on the markets, policies and companies shaping business in Ethiopia and Africa.", ""),
    ("Newsletter", "newsletter_double_opt_in", "Require email confirmation (recommended)", "bool", "1", ""),
    ("Homepage", "breaking_enabled", "Show the breaking-news banner", "bool", "1", "Shows articles flagged as Breaking in the editor."),
    ("Homepage", "breaking_label", "Banner label", "text", "Breaking", ""),
    ("Homepage", "breaking_hours", "Show breaking stories for (hours, 1-168)", "text", "24", ""),
    ("Site", "comments_enabled", "Enable reader comments (moderated)", "bool", "1", ""),
    ("Site", "ads_enabled", "Show advertisements", "bool", "1", ""),
    ("Site", "maintenance_mode", "Maintenance mode", "bool", "0", "Public site returns 503; signed-in staff and /admin still work."),
    ("Site", "maintenance_message", "Maintenance message", "textarea", "We're making improvements and will be back shortly.", ""),
]
DEFAULTS = {k: d for _g, k, _l, _kind, d, _h in FIELDS}
KIND = {k: kind for _g, k, _l, kind, _d, _h in FIELDS}
BOOLS = {k for k, kind in KIND.items() if kind == "bool"}

_cache = {"at": 0.0, "data": None}
TTL = 30


def invalidate():
    _cache.update(at=0.0, data=None)


def all_settings() -> dict:
    now = time.monotonic()
    if _cache["data"] is not None and now - _cache["at"] < TTL and not current_app.config.get("TESTING"):
        return _cache["data"]
    data = dict(DEFAULTS)
    try:
        for row in SiteSetting.query.all():
            if row.key in DEFAULTS:
                data[row.key] = row.value
    except SQLAlchemyError:  # table missing (fresh DB / mid-migration): fall back to defaults
        db.session.rollback()
    _cache.update(at=now, data=data)
    return data


def get(key: str, default=None):
    return all_settings().get(key, DEFAULTS.get(key, default))


def flag(key: str) -> bool:
    return str(get(key, "0")) in ("1", "true", "on", "yes")


def save(values: dict) -> int:
    """Persist submitted values (caller commits). Bools arrive as present/absent checkboxes."""
    changed = 0
    for _g, key, _l, kind, default, _h in FIELDS:
        if kind == "bool":
            new = "1" if values.get(key) in ("on", "1", True) else "0"
        elif key in values:
            new = str(values.get(key) or "").strip()
            if kind == "color" and not _valid_color(new):
                new = default
            if kind in ("url", "media_url") and new and not new.startswith(("https://", "http://", "/")):
                new = ""
        else:
            continue
        row = db.session.get(SiteSetting, key)
        if row is None:
            db.session.add(SiteSetting(key=key, value=new, updated_at=utcnow()))
            changed += 1
        elif row.value != new:
            row.value, row.updated_at = new, utcnow()
            changed += 1
    invalidate()
    return changed


def _valid_color(v: str) -> bool:
    import re
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", v))
