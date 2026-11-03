import os
import re
import time
from urllib.parse import urlparse

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_from_directory,
                   url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select

from ..extensions import csrf, db, limiter
from ..forms import CommentForm, ContactForm
from ..mailer import delivery_enabled, send_email
from ..models import (ARTICLE_TYPES, ROUTE_TYPES, TYPE_LABELS, TYPE_ROUTES, Advertisement, Article, Author, Category, Comment, ContactMessage,
                      Page, Subscriber, Tag)
from ..services import ads, analytics, content, homepage, settings
from ..utils import slugify, utcnow
from . import cached

bp = Blueprint("public", __name__)
TYPE_SLUGS = dict(ARTICLE_TYPES)


def _page_num() -> int:
    return min(max(request.args.get("page", 1, type=int) or 1, 1), 500)


def _paginate(stmt):
    return db.paginate(stmt, page=_page_num(), per_page=current_app.config["PER_PAGE"], error_out=False)


def canonical(path: str, page: int = 1) -> str:
    return current_app.config["SITE_URL"] + path + (f"?page={page}" if page > 1 else "")


def _breadcrumbs(*crumbs):
    return [("Home", "/")] + list(crumbs)


@bp.get("/")
@cached()
def index():
    return render_template("public/index.html", canonical=canonical("/"), **homepage.build())


# ------------------------------------------------------------------ articles
@bp.get("/article/<slug>")
def legacy_article(slug):
    a = content.get_live(slug)
    if not a or not a.category:
        abort(404)
    return redirect(a.url_path, 301)


@bp.get("/<category_slug>/<article_slug>")
@cached()
def article(category_slug, article_slug):
    a = content.get_live(article_slug)
    if not a or not a.category:
        abort(404)
    if a.category.slug != category_slug:
        return redirect(a.url_path, 301)  # category changed or wrong prefix → canonical URL
    comments = []
    if settings.flag("comments_enabled"):
        comments = Comment.query.filter_by(article_id=a.id, status="approved").order_by(Comment.created_at).all()
    older, newer = content.neighbors(a)
    return render_template(
        "public/article.html", a=a, related=content.related(a, 3), most_read=content.most_read(5, exclude=[a.id]),
        older=older, newer=newer,
        comments=comments, comment_form=CommentForm(), comment_state=request.args.get("comment"),
        canonical=a.canonical_override or canonical(a.url_path), url=canonical(a.url_path),
        breadcrumbs=_breadcrumbs(*([(a.category.name, f"/{a.category.slug}")] if a.category.active else []), (a.title, None)))


# ------------------------------------------------------------------ listings
@bp.get("/author/<slug>")
@cached()
def author(slug):
    au = Author.query.filter_by(slug=slug, active=True).first_or_404()
    p = _paginate(content.live_stmt(Article.author_id == au.id))
    return render_template("public/listing.html", kind="author", author=au, heading=au.name, intro=au.bio, pagination=p,
                           base_path=f"/author/{au.slug}", canonical=canonical(f"/author/{au.slug}", p.page),
                           breadcrumbs=_breadcrumbs(("Authors", "/authors"), (au.name, None)))


@bp.get("/authors")
@cached()
def authors():
    rows = db.session.execute(
        select(Author, func.count(Article.id)).join(Article, Article.author_id == Author.id)
        .where(Author.active.is_(True), Article.live_filter()).group_by(Author.id).order_by(Author.name)).all()
    return render_template("public/authors.html", rows=rows, canonical=canonical("/authors"),
                           breadcrumbs=_breadcrumbs(("Authors", None)))


@bp.get("/tag/<slug>")
@cached()
def tag(slug):
    t = Tag.query.filter_by(slug=slug).first_or_404()
    p = _paginate(content.live_stmt(Article.tags.any(Tag.id == t.id)))
    return render_template("public/listing.html", kind="tag", heading=f"#{t.name}", intro=f"Stories tagged “{t.name}”.",
                           pagination=p, base_path=f"/tag/{t.slug}", canonical=canonical(f"/tag/{t.slug}", p.page),
                           breadcrumbs=_breadcrumbs(("Tags", None), (t.name, None)))


@bp.get("/type/<slug>")
def legacy_type(slug):
    if slug not in TYPE_SLUGS:
        abort(404)
    return redirect("/" + TYPE_ROUTES[slug], 301)


def _type_page(atype: str):
    label = TYPE_SLUGS[atype]
    path = "/" + TYPE_ROUTES[atype]
    p = _paginate(content.live_stmt(Article.article_type == atype))
    intros = {"opinion": "Commentary and viewpoints. Opinion pieces reflect the authors' own views.",
              "sponsored": "Paid content from our partners. Always labelled; produced separately from our newsroom.",
              "data-report": "Reporting and analysis built on data."}
    return render_template("public/listing.html", kind="type", heading=label if atype != "sponsored" else "Sponsored Content",
                           intro=intros.get(atype, ""), pagination=p, base_path=path, canonical=canonical(path, p.page),
                           breadcrumbs=_breadcrumbs((label, None)))


@bp.get("/search")
@limiter.limit("60/minute")
@cached(30, 60)
def search():
    q = (request.args.get("q") or "").strip()[:100]
    cat_slug = request.args.get("category", "")
    atype = request.args.get("type", "")
    period = request.args.get("period", "")
    sort = "date" if request.args.get("sort") == "date" else "relevance"
    cat = Category.query.filter_by(slug=cat_slug).first() if cat_slug else None
    pagination = facets = None
    if q:
        stmt = content.search_stmt(q, cat.id if cat else None, atype if atype in TYPE_SLUGS else "", period, sort)
        pagination = _paginate(stmt)
        facets = content.search_facets(q)
    qs = "".join(f"&{k}={v}" for k, v in (("category", cat_slug), ("type", atype), ("period", period), ("sort", sort if sort == "date" else "")) if v)
    return render_template("public/search.html", q=q, pagination=pagination, facets=facets, cat_slug=cat_slug,
                           atype=atype, period=period, sort=sort, categories=Category.query.filter(Category.active.is_(True)).order_by(Category.name).all(),
                           types=ARTICLE_TYPES, base_path="/search", extra=f"&q={q}{qs}", canonical=canonical("/search"),
                           breadcrumbs=_breadcrumbs(("Search", None)))


# ------------------------------------------------------- top-level category / page
@bp.get("/<slug>")
@cached()
def category_or_page(slug):
    if "." in slug:  # root static files (favicon etc.) in local dev; on Vercel the CDN serves public/ directly
        root = os.path.join(current_app.root_path, "..", "public")
        if re.fullmatch(r"[\w.-]+", slug) and os.path.isfile(os.path.join(root, slug)):
            return send_from_directory(root, slug)
        abort(404)
    if slug in ROUTE_TYPES:
        return _type_page(ROUTE_TYPES[slug])
    c = Category.query.filter_by(slug=slug).first()
    if c and c.active:
        p = _paginate(content.live_stmt(Article.category_id.in_(c.family_ids)))
        crumbs = [(c.parent.name, f"/{c.parent.slug}")] if c.parent else []
        return render_template("public/listing.html", kind="category", category=c, heading=c.name, intro=c.description,
                               pagination=p, base_path=f"/{c.slug}", canonical=canonical(f"/{c.slug}", p.page),
                               subcategories=[x for x in c.children if x.active],
                               breadcrumbs=_breadcrumbs(*crumbs, (c.name, None)))
    if c:
        abort(404)
    page = Page.query.filter_by(slug=slug, published=True).first_or_404()
    return render_template("public/page.html", page=page, canonical=canonical(f"/{page.slug}"),
                           breadcrumbs=_breadcrumbs((page.title, None)))


# ------------------------------------------------------------------ contact
def _ts_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="contact-form")


@bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("5/hour;20/day", methods=["POST"])
def contact():
    form = ContactForm()
    sent = False
    if request.method == "POST":
        spam = bool(form.website.data)  # honeypot
        try:
            _payload, ts = _ts_serializer().loads(form.ts.data or "", max_age=7200, return_timestamp=True)
            if (time.time() - ts.timestamp()) < 3:
                spam = True  # submitted faster than a human can
        except (BadSignature, SignatureExpired):
            spam = True
        if spam:
            sent = True  # pretend success; give bots nothing to learn from
        elif form.validate():
            msg = ContactMessage(name=form.name.data.strip(), email=form.email.data.strip(), subject=form.subject.data.strip(),
                                 inquiry_type=form.inquiry_type.data, message=form.message.data.strip())
            db.session.add(msg)
            db.session.commit()
            to = settings.get("contact_email")
            if to:
                send_email(to, f"[Website] {msg.subject}", f"<p>From {msg.name} &lt;{msg.email}&gt; ({msg.inquiry_type})</p><p>{msg.message}</p>",
                           f"From {msg.name} <{msg.email}> ({msg.inquiry_type})\n\n{msg.message}")
            sent = True
    token = _ts_serializer().dumps("form")
    resp = current_app.make_response(render_template("public/contact.html", form=form, sent=sent, ts=token,
                                                     canonical=canonical("/contact"), breadcrumbs=_breadcrumbs(("Contact", None))))
    resp.status_code = 200 if (sent or request.method == "GET" or not form.errors) else 400
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ comments
@bp.post("/comments/<int:article_id>")
@csrf.exempt
@limiter.limit("5/hour;20/day")
def comment(article_id):
    a = db.session.get(Article, article_id)
    if not a or not a.is_live or not a.category or not settings.flag("comments_enabled"):
        abort(404)
    form = CommentForm()
    if form.website.data:
        return redirect(f"{a.url_path}?comment=pending#comments")  # honeypot tripped: silently drop
    if not form.validate():
        return redirect(f"{a.url_path}?comment=invalid#comments")
    db.session.add(Comment(article_id=a.id, name=form.name.data.strip(), email=(form.email.data or "").strip(),
                           body=form.body.data.strip(), status="pending"))
    db.session.commit()
    return redirect(f"{a.url_path}?comment=pending#comments")


# ---------------------------------------------------------------- newsletter
@bp.post("/newsletter/subscribe")
@csrf.exempt
@limiter.limit("5/hour;20/day")
def subscribe():
    from email_validator import EmailNotValidError, validate_email
    wants_json = request.accept_mimetypes.best == "application/json"
    if request.form.get("website"):  # honeypot
        return jsonify(ok=True, message="Check your inbox to confirm your subscription.") if wants_json else \
            render_template("public/newsletter_result.html", ok=True, message="Check your inbox to confirm your subscription.")
    email = (request.form.get("email") or "").strip()[:255]
    try:
        email = validate_email(email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        msg = "Please enter a valid email address."
        return (jsonify(ok=False, message=msg), 400) if wants_json else (
            render_template("public/newsletter_result.html", ok=False, message=msg), 400)
    double = settings.flag("newsletter_double_opt_in")
    sub = Subscriber.query.filter_by(email=email).first()
    if sub is None:
        sub = Subscriber(email=email)
        db.session.add(sub)
    if sub.status != "confirmed":
        sub.status = "pending" if double else "confirmed"
        if not double:
            sub.confirmed_at = utcnow()
    db.session.commit()
    msg = "You're subscribed. Thank you!"
    if sub.status == "pending":
        link = f"{current_app.config['SITE_URL']}/newsletter/confirm/{sub.token}"
        name = settings.get("site_name")
        sent = send_email(email, f"Confirm your subscription to {name}", render_template("email/confirm.html", link=link, name=name),
                          f"Confirm your subscription to {name}: {link}")
        # Never claim an email went out unless one really did.
        msg = ("Check your inbox to confirm your subscription." if sent else
               "Thanks \u2014 we've saved your address, but we couldn't send the confirmation email right now, so it is not confirmed yet.")
    return jsonify(ok=True, message=msg) if wants_json else render_template("public/newsletter_result.html", ok=True, message=msg)


def _no_store(html):
    resp = current_app.make_response(html)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/newsletter/confirm/<token>")
def confirm(token):
    sub = Subscriber.query.filter_by(token=token).first_or_404()
    if sub.status != "confirmed":
        sub.status, sub.confirmed_at = "confirmed", utcnow()
        db.session.commit()
    return _no_store(render_template("public/newsletter_result.html", ok=True, message="Subscription confirmed. Thank you!"))


@bp.get("/newsletter/unsubscribe/<token>")
def unsubscribe(token):
    sub = Subscriber.query.filter_by(token=token).first_or_404()
    sub.status = "unsubscribed"
    db.session.commit()
    return _no_store(render_template("public/newsletter_result.html", ok=True, message="You have been unsubscribed."))


# ------------------------------------------------------------ ads, analytics, dev media
@bp.get("/ad/<int:ad_id>")
@limiter.limit("60/minute")
def ad_click(ad_id):
    ad = db.get_or_404(Advertisement, ad_id)
    if urlparse(ad.dest_url).scheme not in ("http", "https"):
        abort(404)
    if ad.is_running:
        db.session.query(Advertisement).filter_by(id=ad.id).update({"clicks": Advertisement.clicks + 1})
        db.session.commit()
    resp = redirect(ad.dest_url, 302)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@bp.post("/api/pv")
@csrf.exempt
@limiter.limit("120/minute")
def page_view():
    data = request.get_json(silent=True) or {}
    path = str(data.get("p") or "/")
    aid = data.get("a")
    aid = aid if isinstance(aid, int) else None
    if not path.startswith("/") or path.startswith(("/admin", "/api", "/cron")):
        return ("", 204)
    counted = analytics.record(path.split("?")[0], aid, str(data.get("r") or "")[:300], request.host, request.headers.get("User-Agent", ""))
    ad_ids = [i for i in (data.get("ads") or []) if isinstance(i, int)][:12]
    if counted and ad_ids:  # impressions = page loads on which the ad was rendered (not viewability); bots excluded
        db.session.query(Advertisement).filter(Advertisement.id.in_(ad_ids), Advertisement.running_filter()).update(
            {"impressions": Advertisement.impressions + 1}, synchronize_session=False)
        db.session.commit()
    return ("", 204)


@bp.get("/media/<path:key>")
def local_media(key):
    """Dev-only file serving for STORAGE_PROVIDER=local. Disabled in production."""
    if current_app.config["STRICT"] or current_app.config["STORAGE_PROVIDER"] != "local":
        abort(404)
    return send_from_directory(os.path.join(current_app.instance_path, "uploads"), key)
