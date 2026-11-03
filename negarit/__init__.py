import os
from datetime import timezone
from email.utils import format_datetime

from flask import Flask, jsonify, render_template, request
from flask.sessions import SecureCookieSessionInterface
from flask_wtf.csrf import CSRFError

from .config import load_config
from .extensions import csrf, db, limiter, login_manager, migrate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GA_SCRIPT = " https://www.googletagmanager.com"
GA_CONNECT = " https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com"


class PublicCacheSessionInterface(SecureCookieSessionInterface):
    """Responses marked CDN-cacheable never depend on the session: skip cookie handling so they stay cacheable."""

    def save_session(self, app, session, response):
        if response.headers.get("Cache-Control", "").startswith("public"):
            return
        return super().save_session(app, session, response)


def build_csp(ga: bool) -> str:
    return ("default-src 'self'; img-src 'self' https: data:; style-src 'self'; font-src 'self'; object-src 'none'; "
            f"script-src 'self'{GA_SCRIPT if ga else ''}; connect-src 'self'{GA_CONNECT if ga else ''}; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def create_app(overrides: dict | None = None) -> Flask:
    if not os.environ.get("VERCEL"):
        from dotenv import load_dotenv
        load_dotenv()

    # On Vercel, /static is served from public/ by the CDN; Flask serves the same folder in local dev.
    app = Flask(__name__, static_folder=os.path.join(ROOT, "public", "static"), static_url_path="/static",
                instance_path=os.path.join(ROOT, "instance"))
    app.config.update(load_config())
    app.config.update(overrides or {})
    app.session_interface = PublicCacheSessionInterface()
    app.debug = False if app.config["STRICT"] else app.debug
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):  # local dev only
        os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(ROOT, "migrations"), render_as_batch=True)  # batch: SQLite ALTER support
    csrf.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    if app.config["STRICT"] and app.config["RATELIMIT_STORAGE_URI"].startswith("memory://"):
        app.logger.warning("RATELIMIT_STORAGE_URI not set: rate limits are per-instance only.")

    from . import models  # noqa: F401  (register tables for Alembic)
    from .cli import register_cli
    from .views import auth, cron, data as data_views, public, seo
    from .views import admin as admin_views
    for bp in (public.bp, data_views.bp, seo.bp, auth.bp, admin_views.bp, cron.bp):
        app.register_blueprint(bp)
    register_cli(app)
    register_helpers(app)
    register_hooks(app)
    register_errors(app)
    return app


def register_helpers(app: Flask):
    from flask_login import current_user
    from sqlalchemy.exc import SQLAlchemyError

    from .models import STATUS_LABELS, TYPE_LABELS, TYPE_ROUTES, Category, Page
    from .services import ads, settings
    from .utils import to_local

    @app.template_filter("localdate")
    def localdate(dt):
        d = to_local(dt)
        return d.strftime("%b %d, %Y").replace(" 0", " ") if d else ""

    @app.template_filter("localdatetime")
    def localdatetime(dt):
        d = to_local(dt)
        return d.strftime("%Y-%m-%d %H:%M") if d else ""

    @app.template_filter("inputdt")
    def inputdt(dt):
        d = to_local(dt)
        return d.strftime("%Y-%m-%dT%H:%M") if d else ""

    @app.template_filter("isoz")
    def isoz(dt):
        return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""

    @app.template_filter("rfc822")
    def rfc822(dt):
        return format_datetime(dt.replace(tzinfo=timezone.utc)) if dt else ""

    @app.template_filter("num")
    def num(n):
        return f"{int(n or 0):,}"

    from .services import data as data_service
    app.jinja_env.globals["ad"] = ads.pick  # global (not context) so imported macros can use it
    app.jinja_env.globals["fmt_num"] = data_service.fmt
    app.jinja_env.globals["period_label"] = data_service.period_label

    @app.template_filter("split_after")
    def split_after(html, n=3):
        """Split sanitised body HTML after the n-th paragraph (for in-article ad placement)."""
        parts = (html or "").split("</p>")
        if len(parts) <= n + 1:
            return (html or "", "")
        return ("</p>".join(parts[:n]) + "</p>", "</p>".join(parts[n:]))

    def absolute(url):
        return url if url.startswith(("http://", "https://")) else app.config["SITE_URL"] + url

    def breadcrumb_ld(crumbs, canonical=""):
        from flask.json import dumps
        from markupsafe import Markup
        base = app.config["SITE_URL"]
        items = [{"@type": "ListItem", "position": i, "name": label, "item": (base + href) if href else canonical}
                 for i, (label, href) in enumerate(crumbs, 1)]
        data = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}
        return Markup(dumps(data).replace("<", "\\u003c"))

    @app.context_processor
    def inject():
        cfg = app.config
        ver = cfg["ASSET_VERSION"]
        S = settings.all_settings()
        ctx = {
            "S": S, "SITE_NAME": S["site_name"], "SITE_TAGLINE": S["tagline"], "SITE_URL": cfg["SITE_URL"],
            "TYPE_LABELS": TYPE_LABELS, "STATUS_LABELS": STATUS_LABELS,
            "asset": lambda p: f"/static/{p}?v={ver}",
            "abs_url": lambda path="/": cfg["SITE_URL"] + path,
            "can": lambda perm: current_user.is_authenticated and current_user.can(perm),
            "TYPE_ROUTES": TYPE_ROUTES, "nav_primary": [], "nav_more": [], "nav_children": {}, "footer_pages": [], "absolute": absolute,
            "breadcrumb_ld": breadcrumb_ld, "hide_footer_nl": False,
            "theme_v": __import__("hashlib").md5((S["brand_primary"] + S["brand_accent"] + S["brand_accent_dark"]).encode()).hexdigest()[:6],
        }
        if request.blueprint == "admin" and current_user.is_authenticated:
            try:
                from .models import Article, Comment, ContactMessage
                ctx["admin_counts"] = {
                    "review": Article.query.filter_by(status="review").count() if current_user.can("article.publish") else 0,
                    "comments": Comment.query.filter_by(status="pending").count() if current_user.can("comments.moderate") else 0,
                    "messages": ContactMessage.query.filter_by(status="new").count() if current_user.can("messages.view") else 0}
            except SQLAlchemyError:
                db.session.rollback()
        if request.blueprint not in ("admin", "auth"):
            try:
                cats = Category.query.filter(Category.active.is_(True)).order_by(Category.sort_order, Category.name).all()
                top = [c for c in cats if c.parent_id is None]
                ctx["nav_primary"] = [c for c in top if c.is_primary]
                ctx["nav_more"] = [c for c in top if not c.is_primary]
                kids: dict = {}
                for c in cats:
                    if c.parent_id:
                        kids.setdefault(c.parent_id, []).append(c)
                ctx["nav_children"] = kids
                ctx["footer_pages"] = Page.query.filter_by(published=True, in_footer=True).order_by(Page.sort_order).all()
            except SQLAlchemyError:
                db.session.rollback()
        return ctx


def register_hooks(app: Flask):
    from flask_login import current_user

    from .services import settings

    @app.before_request
    def schema_guard():
        if not app.config["SCHEMA_GUARD"] or app.extensions.get("_schema_ok") or request.path.startswith("/static"):
            return None
        from . import devcheck
        problem = devcheck.schema_status(os.path.join(ROOT, "migrations"))
        if problem is None:
            app.extensions["_schema_ok"] = True  # verified once per process
            return None
        return devcheck.response(app, problem)

    @app.before_request
    def maintenance():
        if not settings.flag("maintenance_mode"):
            return None
        p = request.path
        if p.startswith(("/admin", "/static", "/cron")) or p in ("/api/health", "/theme.css", "/robots.txt", "/favicon.svg"):
            return None
        if current_user.is_authenticated and current_user.can("article.create"):
            return None  # signed-in staff can browse the live site
        resp = app.make_response(render_template("maintenance.html", message=settings.get("maintenance_message")))
        resp.status_code = 503
        resp.headers["Retry-After"] = "3600"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()")
        if "Content-Security-Policy" not in resp.headers:
            resp.headers["Content-Security-Policy"] = build_csp(bool(settings.get("ga_measurement_id")))
        if app.config["STRICT"]:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
        if request.path.startswith(("/admin", "/cron", "/api")) and request.path != "/api/health":
            resp.headers["Cache-Control"] = "no-store, private"
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        if app.config["ON_VERCEL"] and not app.config["IS_PRODUCTION_DEPLOY"]:
            resp.headers["X-Robots-Tag"] = "noindex, nofollow"  # previews are never indexed
        return resp


def register_errors(app: Flask):
    def respond(code, title, message):
        if request.path.endswith(("/upload", "/pv")) or request.accept_mimetypes.best == "application/json":
            return jsonify(error=message), code
        resp = app.make_response(render_template("error.html", code=code, title=title, message=message))
        resp.status_code = code
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return respond(400, "Session expired", "Your form session expired. Reload the page and try again.")

    @app.errorhandler(400)
    def e400(e):
        return respond(400, "Bad request", "The request could not be understood.")

    @app.errorhandler(403)
    def e403(e):
        return respond(403, "Access denied", "You do not have permission to view this page.")

    @app.errorhandler(404)
    def e404(e):
        return respond(404, "Page not found", "The page you are looking for has moved, been removed, or never existed.")

    @app.errorhandler(413)
    def e413(e):
        return respond(413, "File too large", "Uploads are limited to 4 MB.")

    @app.errorhandler(429)
    def e429(e):
        return respond(429, "Too many requests", "Please slow down and try again shortly.")

    @app.errorhandler(500)
    def e500(e):
        db.session.rollback()
        return respond(500, "Something went wrong", "An unexpected error occurred. It has been logged and we are looking into it.")
