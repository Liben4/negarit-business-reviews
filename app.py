"""
Negarit Business Review
A small Flask + SQLite news site with an admin panel for posting articles.

Run locally:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000

On first run, a SQLite database is created automatically and seeded with
sample categories and articles, plus one admin account. The admin username
and a randomly generated password are printed to the terminal ONCE — save
that password immediately.
"""

import os
import re
import sqlite3
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, g, request, session, redirect, url_for,
    render_template, flash, abort, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "negarit.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
ARTICLES_PER_PAGE = 6

app = Flask(__name__)
# In production, set a persistent SECRET_KEY environment variable, otherwise
# every restart invalidates admin sessions (people just get logged out).
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

SITE_NAME = "Negarit Business News"

# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS categories (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    slug    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS admin_users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    slug           TEXT NOT NULL UNIQUE,
    summary        TEXT NOT NULL DEFAULT '',
    content        TEXT NOT NULL DEFAULT '',
    category_id    INTEGER,
    image_filename TEXT,
    author         TEXT NOT NULL DEFAULT 'Staff Writer',
    status         TEXT NOT NULL DEFAULT 'published',
    featured       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
);
"""

DEFAULT_CATEGORIES = ["Markets", "Economy", "Companies", "Technology", "Policy"]

# Seed content uses fictional company names on purpose, so it reads
# unmistakably as placeholder copy rather than real reporting.
SEED_ARTICLES = [
    {
        "title": "Highland Coffee Exports Reports Record Quarter",
        "category": "Companies",
        "summary": "The exporter says diversified buyers and steadier logistics lifted shipment volumes to a new high.",
        "content": "<p>Highland Coffee Exports Ltd. says its latest quarter was its strongest on record, "
                    "crediting a broader base of overseas buyers and fewer delays moving containers out of port.</p>"
                    "<p>Executives told reporters that the company had spent the past year diversifying its client "
                    "base across new regions, reducing its reliance on any single market. Warehouse upgrades and "
                    "tighter coordination with shipping partners also helped cut turnaround times.</p>"
                    "<p>Industry watchers say the results reflect a wider trend among exporters investing in "
                    "logistics resilience after a stretch of unpredictable shipping costs.</p>",
        "featured": 1,
    },
    {
        "title": "Addis Fintech Startups Draw New Investment",
        "category": "Technology",
        "summary": "A wave of early-stage funding is targeting payments and lending platforms built for small merchants.",
        "content": "<p>A handful of early-stage technology firms focused on digital payments and small-business "
                    "lending have closed new funding rounds in recent weeks, according to people familiar with the "
                    "deals.</p><p>Investors say they are drawn to platforms that serve merchants who have "
                    "historically had limited access to formal banking tools. Several of the startups say they plan "
                    "to use the capital to expand their engineering teams and broaden merchant onboarding.</p>"
                    "<p>Analysts caution that competition in the space is intensifying, and that user growth alone "
                    "will not be enough to guarantee long-term investor confidence.</p>",
        "featured": 0,
    },
    {
        "title": "Central Bank Signals Steady Policy Stance",
        "category": "Economy",
        "summary": "Officials indicate no imminent change to the current policy rate, citing a cautious wait-and-see approach.",
        "content": "<p>Central bank officials indicated this week that current monetary policy settings are likely to "
                    "remain in place for the near term, as authorities continue to monitor inflation and currency "
                    "conditions.</p><p>In prepared remarks, an official said the approach reflects a preference for "
                    "stability while broader economic data is assessed. Business groups have generally welcomed the "
                    "predictability, though some importers continue to flag access to foreign exchange as a "
                    "pressure point.</p><p>Economists say further guidance is expected at the next scheduled policy "
                    "review.</p>",
        "featured": 0,
    },
    {
        "title": "Government Unveils Draft Trade Policy Framework",
        "category": "Policy",
        "summary": "The proposal aims to simplify licensing for exporters while tightening standards enforcement.",
        "content": "<p>Officials have circulated a draft framework intended to simplify licensing procedures for "
                    "export-oriented businesses, while introducing stricter enforcement of product standards.</p>"
                    "<p>Business associations have broadly welcomed the simplification measures, though some members "
                    "have asked for a longer transition period before new compliance requirements take effect.</p>"
                    "<p>A public comment period is expected before the framework is finalized.</p>",
        "featured": 0,
    },
    {
        "title": "Great Rift Logistics Expands Regional Network",
        "category": "Companies",
        "summary": "The freight operator is adding routes and warehouse capacity to meet rising cross-border demand.",
        "content": "<p>Great Rift Logistics says it is adding new freight routes and warehouse capacity as demand for "
                    "regional cross-border shipping continues to climb.</p><p>The company's operations lead said "
                    "the expansion was planned around corridors that have seen the fastest growth in trade volume "
                    "over the past two years. The firm expects the added capacity to shorten average delivery "
                    "times for its retail and manufacturing clients.</p>",
        "featured": 0,
    },
    {
        "title": "Mobile Money Adoption Continues to Climb",
        "category": "Technology",
        "summary": "New usage data points to steady growth in digital wallets among small and informal businesses.",
        "content": "<p>Usage of mobile money services among small and informal businesses continues to grow, "
                    "according to industry data reviewed this week.</p><p>Vendors cite convenience and reduced "
                    "cash-handling risk as leading reasons for adoption. Providers, meanwhile, are competing to "
                    "add features aimed at merchants, including simple bookkeeping tools and short-term working "
                    "capital advances.</p><p>Analysts note that reliable network coverage remains the biggest "
                    "barrier to adoption in more remote areas.</p>",
        "featured": 0,
    },
]

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    """Return a request-scoped SQLite connection."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables and seed sample data the very first time the app runs."""
    first_run = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)

    if first_run:
        now = datetime.utcnow().isoformat()

        category_ids = {}
        for name in DEFAULT_CATEGORIES:
            cur = conn.execute(
                "INSERT INTO categories (name, slug) VALUES (?, ?)",
                (name, slugify(name)),
            )
            category_ids[name] = cur.lastrowid

        for i, art in enumerate(SEED_ARTICLES):
            slug = slugify(art["title"])
            conn.execute(
                """INSERT INTO articles
                   (title, slug, summary, content, category_id, author,
                    status, featured, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'published', ?, ?, ?)""",
                (
                    art["title"], slug, art["summary"], art["content"],
                    category_ids[art["category"]], "Staff Writer",
                    art["featured"], now, now,
                ),
            )

        admin_password = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO admin_users (username, password_hash, created_at) VALUES (?, ?, ?)",
            ("admin", generate_password_hash(admin_password), now),
        )
        conn.commit()
        conn.close()

        banner = "=" * 64
        print(banner)
        print(f"  {SITE_NAME} — first-time setup complete")
        print(f"  Admin login URL : /admin/login")
        print(f"  Admin username  : admin")
        print(f"  Admin password  : {admin_password}")
        print("  This password is shown ONCE. Save it now, then change it")
        print("  from the admin dashboard after logging in.")
        print(banner)
    else:
        conn.close()


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text or "post"


def unique_article_slug(db, title, ignore_id=None):
    base = slugify(title)
    slug = base
    n = 2
    while True:
        if ignore_id:
            row = db.execute(
                "SELECT id FROM articles WHERE slug = ? AND id != ?", (slug, ignore_id)
            ).fetchone()
        else:
            row = db.execute("SELECT id FROM articles WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return slug
        slug = f"{base}-{n}"
        n += 1


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file_storage):
    """Save an uploaded image with a collision-proof name; return the filename or None."""
    if not file_storage or file_storage.filename == "":
        return None
    if not allowed_file(file_storage.filename):
        flash("Image must be PNG, JPG, WEBP, or GIF.", "error")
        return None
    safe_name = secure_filename(file_storage.filename)
    unique_name = f"{secrets.token_hex(6)}_{safe_name}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], unique_name))
    return unique_name


def delete_upload(filename):
    if not filename:
        return
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def format_date(iso_string):
    try:
        return datetime.fromisoformat(iso_string).strftime("%B %-d, %Y")
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(iso_string).strftime("%B %d, %Y").replace(" 0", " ")
        except (ValueError, TypeError):
            return iso_string


app.jinja_env.filters["format_date"] = format_date


# ---------------------------------------------------------------------------
# Auth + CSRF
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def get_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    return session["_csrf_token"]


app.jinja_env.globals["csrf_token"] = get_csrf_token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.get("_csrf_token")
        submitted = request.form.get("csrf_token")
        if not token or not submitted or token != submitted:
            abort(403)


@app.errorhandler(403)
def forbidden(e):
    return render_template("404.html", message="That request could not be verified. Please try again."), 403


# ---------------------------------------------------------------------------
# Shared template context
# ---------------------------------------------------------------------------

@app.context_processor
def inject_globals():
    db = get_db()
    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    return {
        "site_name": SITE_NAME,
        "nav_categories": categories,
        "current_year": datetime.utcnow().year,
        "is_admin": bool(session.get("admin_id")),
    }


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    db = get_db()
    featured = db.execute(
        """SELECT articles.*, categories.name AS category_name, categories.slug AS category_slug
           FROM articles LEFT JOIN categories ON articles.category_id = categories.id
           WHERE articles.status = 'published' AND articles.featured = 1
           ORDER BY articles.created_at DESC LIMIT 1"""
    ).fetchone()

    if featured is None:
        featured = db.execute(
            """SELECT articles.*, categories.name AS category_name, categories.slug AS category_slug
               FROM articles LEFT JOIN categories ON articles.category_id = categories.id
               WHERE articles.status = 'published'
               ORDER BY articles.created_at DESC LIMIT 1"""
        ).fetchone()

    exclude_id = featured["id"] if featured else -1
    latest = db.execute(
        """SELECT articles.*, categories.name AS category_name, categories.slug AS category_slug
           FROM articles LEFT JOIN categories ON articles.category_id = categories.id
           WHERE articles.status = 'published' AND articles.id != ?
           ORDER BY articles.created_at DESC LIMIT 8""",
        (exclude_id,),
    ).fetchall()

    return render_template("index.html", featured=featured, latest=latest)


@app.route("/category/<slug>")
def category(slug):
    db = get_db()
    cat = db.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()
    if cat is None:
        abort(404)

    page = max(request.args.get("page", 1, type=int), 1)
    offset = (page - 1) * ARTICLES_PER_PAGE

    total = db.execute(
        "SELECT COUNT(*) FROM articles WHERE category_id = ? AND status = 'published'", (cat["id"],)
    ).fetchone()[0]

    articles = db.execute(
        """SELECT articles.*, categories.name AS category_name, categories.slug AS category_slug
           FROM articles LEFT JOIN categories ON articles.category_id = categories.id
           WHERE articles.category_id = ? AND articles.status = 'published'
           ORDER BY articles.created_at DESC LIMIT ? OFFSET ?""",
        (cat["id"], ARTICLES_PER_PAGE, offset),
    ).fetchall()

    has_next = offset + ARTICLES_PER_PAGE < total
    has_prev = page > 1

    return render_template(
        "category.html", category=cat, articles=articles,
        page=page, has_next=has_next, has_prev=has_prev,
    )


@app.route("/article/<slug>")
def article(slug):
    db = get_db()
    art = db.execute(
        """SELECT articles.*, categories.name AS category_name, categories.slug AS category_slug
           FROM articles LEFT JOIN categories ON articles.category_id = categories.id
           WHERE articles.slug = ?""",
        (slug,),
    ).fetchone()

    if art is None:
        abort(404)
    if art["status"] != "published" and not session.get("admin_id"):
        abort(404)

    related = db.execute(
        """SELECT articles.*, categories.name AS category_name, categories.slug AS category_slug
           FROM articles LEFT JOIN categories ON articles.category_id = categories.id
           WHERE articles.category_id = ? AND articles.id != ? AND articles.status = 'published'
           ORDER BY articles.created_at DESC LIMIT 3""",
        (art["category_id"], art["id"]),
    ).fetchall()

    return render_template("article.html", article=art, related=related)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


# ---------------------------------------------------------------------------
# Admin: auth
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_id"):
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["admin_id"] = user["id"]
            session["admin_username"] = user["username"]
            flash("Logged in.", "success")
            return redirect(url_for("admin_dashboard"))

        flash("Incorrect username or password.", "error")

    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin/change-password", methods=["GET", "POST"])
@login_required
def admin_change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        db = get_db()
        user = db.execute("SELECT * FROM admin_users WHERE id = ?", (session["admin_id"],)).fetchone()

        if not check_password_hash(user["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new != confirm:
            flash("New passwords do not match.", "error")
        else:
            db.execute(
                "UPDATE admin_users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new), user["id"]),
            )
            db.commit()
            flash("Password changed.", "success")
            return redirect(url_for("admin_dashboard"))

    return render_template("admin/change_password.html")


# ---------------------------------------------------------------------------
# Admin: dashboard + article CRUD
# ---------------------------------------------------------------------------

@app.route("/admin")
@login_required
def admin_dashboard():
    db = get_db()
    articles = db.execute(
        """SELECT articles.*, categories.name AS category_name
           FROM articles LEFT JOIN categories ON articles.category_id = categories.id
           ORDER BY articles.updated_at DESC"""
    ).fetchall()

    stats = {
        "total": len(articles),
        "published": sum(1 for a in articles if a["status"] == "published"),
        "drafts": sum(1 for a in articles if a["status"] == "draft"),
        "categories": db.execute("SELECT COUNT(*) FROM categories").fetchone()[0],
    }

    return render_template("admin/dashboard.html", articles=articles, stats=stats)


@app.route("/admin/new", methods=["GET", "POST"])
@login_required
def admin_new_article():
    db = get_db()
    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        summary = request.form.get("summary", "").strip()
        content = request.form.get("content", "").strip()
        category_id = request.form.get("category_id") or None
        author = request.form.get("author", "").strip() or "Staff Writer"
        status = "draft" if request.form.get("status") == "draft" else "published"
        featured = 1 if request.form.get("featured") == "on" else 0

        if not title or not content:
            flash("Title and content are required.", "error")
            return render_template("admin/article_form.html", categories=categories, article=None, mode="new")

        slug = unique_article_slug(db, title)
        image_filename = save_upload(request.files.get("image"))
        now = datetime.utcnow().isoformat()

        if featured:
            db.execute("UPDATE articles SET featured = 0")

        db.execute(
            """INSERT INTO articles
               (title, slug, summary, content, category_id, image_filename,
                author, status, featured, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, slug, summary, content, category_id, image_filename,
             author, status, featured, now, now),
        )
        db.commit()
        flash("Article published." if status == "published" else "Draft saved.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin/article_form.html", categories=categories, article=None, mode="new")


@app.route("/admin/edit/<int:article_id>", methods=["GET", "POST"])
@login_required
def admin_edit_article(article_id):
    db = get_db()
    art = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if art is None:
        abort(404)
    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        summary = request.form.get("summary", "").strip()
        content = request.form.get("content", "").strip()
        category_id = request.form.get("category_id") or None
        author = request.form.get("author", "").strip() or "Staff Writer"
        status = "draft" if request.form.get("status") == "draft" else "published"
        featured = 1 if request.form.get("featured") == "on" else 0

        if not title or not content:
            flash("Title and content are required.", "error")
            return render_template("admin/article_form.html", categories=categories, article=art, mode="edit")

        slug = unique_article_slug(db, title, ignore_id=article_id)
        new_image = save_upload(request.files.get("image"))
        image_filename = art["image_filename"]
        if new_image:
            delete_upload(art["image_filename"])
            image_filename = new_image
        elif request.form.get("remove_image") == "on":
            delete_upload(art["image_filename"])
            image_filename = None

        now = datetime.utcnow().isoformat()

        if featured:
            db.execute("UPDATE articles SET featured = 0 WHERE id != ?", (article_id,))

        db.execute(
            """UPDATE articles SET title=?, slug=?, summary=?, content=?, category_id=?,
               image_filename=?, author=?, status=?, featured=?, updated_at=?
               WHERE id=?""",
            (title, slug, summary, content, category_id, image_filename,
             author, status, featured, now, article_id),
        )
        db.commit()
        flash("Article updated.", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin/article_form.html", categories=categories, article=art, mode="edit")


@app.route("/admin/delete/<int:article_id>", methods=["POST"])
@login_required
def admin_delete_article(article_id):
    db = get_db()
    art = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    if art is None:
        abort(404)
    delete_upload(art["image_filename"])
    db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    db.commit()
    flash("Article deleted.", "success")
    return redirect(url_for("admin_dashboard"))


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html", message=None), 404


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

init_db()

if __name__ == "__main__":
    app.run(debug=True)
