"""Categories, tags, authors and static pages: all use the shared list/form templates."""
from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from markupsafe import Markup, escape
from sqlalchemy import func, select

from ...extensions import db
from ...models import Article, Author, Category, Media, Page, Tag, User
from ...security import permission_required
from ...services import activity
from ...utils import slug_taken, slugify, unique_slug, utcnow
from . import bp, render_form, render_list
from .helpers import f, flash_all, page_arg, parse


def _slug_for(model, raw: str, fallback: str, obj=None, top_level=False) -> tuple[str, str | None]:
    slug = slugify(raw or fallback)
    if not slug:
        return "", "A URL slug could not be generated."
    if top_level:
        clash = slug_taken(slug, exclude=obj)
    else:
        row = model.query.filter_by(slug=slug).first()
        clash = bool(row and row is not obj)
    return slug, ("That URL is already used or reserved — choose another." if clash else None)


# --------------------------------------------------------------- categories
def _cat_fields(c: Category):
    tops = [("", "None (top-level section)")] + [(str(x.id), x.name) for x in Category.query.filter(Category.parent_id.is_(None)).order_by(Category.name)
                                                 if x.id != c.id] if not c.children else [("", "None (has subcategories, so it stays top-level)")]
    return [
        f("name", "Name", value=c.name, required=True, maxlength=80),
        f("parent_id", "Parent category", "select", str(c.parent_id or ""), options=tops,
          help="Subcategories appear under their parent. One level only."),
        f("slug", "URL slug", value=c.slug, help="Forms article URLs: /slug/article-title. Changing it redirects old article links.", maxlength=100),
        f("description", "Description", "textarea", c.description, maxlength=300, rows=3),
        f("is_primary", "Show in the sections bar (otherwise under “More”)", "bool", c.is_primary if c.id else True),
        f("active", "Active", "bool", c.active if c.id else True,
          help="Inactive categories disappear from navigation, pickers and the homepage; their existing article URLs keep working."),
        f("sort_order", "Sort order (lower first)", "number", c.sort_order if c.sort_order is not None else 100),
        f("seo_title", "SEO title", value=c.seo_title, maxlength=120),
        f("meta_description", "Meta description", "textarea", c.meta_description, maxlength=320, rows=2),
    ]


@bp.get("/categories")
@permission_required("taxonomy.manage")
def categories():
    counts = dict(db.session.execute(select(Article.category_id, func.count()).group_by(Article.category_id)).all())
    rows = [{"cells": [("\u21b3 " if c.parent_id else "") + c.name, f"/{c.slug}",
                       "Inactive" if not c.active else ("Subcategory" if c.parent_id else ("Sections bar" if c.is_primary else "More")),
                       c.sort_order, counts.get(c.id, 0)], "link": url_for("admin.category_edit", id=c.id)}
            for c in sorted(Category.query.all(), key=lambda x: ((x.parent.sort_order if x.parent else x.sort_order), x.parent_id or 0, x.sort_order, x.name))]
    return render_list("Categories", rows, ["Name", "URL", "Navigation", "Order", "Articles"],
                       new_url=url_for("admin.category_edit", id=0), new_label="New category")


@bp.route("/categories/<int:id>", methods=["GET", "POST"])
@permission_required("taxonomy.manage")
def category_edit(id):
    c = db.get_or_404(Category, id) if id else Category(sort_order=100)
    fields = _cat_fields(c)
    if request.method == "POST":
        vals, errors = parse(fields)
        slug, err = _slug_for(Category, vals["slug"], vals["name"], c if id else None, top_level=True)
        if err:
            errors.append(err)
        dup = Category.query.filter(func.lower(Category.name) == vals["name"].lower(), Category.id != (c.id or 0)).first()
        if dup:
            errors.append("A category with that name already exists.")
        if not errors:
            for k in ("name", "description", "is_primary", "active", "sort_order", "seo_title", "meta_description"):
                setattr(c, k, vals[k])
            pid = int(vals["parent_id"]) if str(vals["parent_id"]).isdigit() else None
            c.parent_id = pid if pid and pid != c.id else None
            c.slug = slug
            db.session.add(c)
            db.session.flush()
            activity.log("category_save", "category", c.id, c.name)
            db.session.commit()
            flash("Category saved.", "ok")
            return redirect(url_for("admin.categories"))
        flash_all(errors)
        for fld in fields:
            fld["value"] = vals.get(fld["name"], fld["value"])
    extra = []
    if id:
        extra.append(("Delete category", url_for("admin.category_delete", id=c.id), "Delete this category? It must have no articles.", True))
    return render_form("Edit category" if id else "New category", fields, back=url_for("admin.categories"), extra_actions=extra)


@bp.post("/categories/<int:id>/delete")
@permission_required("taxonomy.manage")
def category_delete(id):
    c = db.get_or_404(Category, id)
    if c.children:
        flash("Move or delete this category's subcategories first.", "error")
        return redirect(url_for("admin.category_edit", id=c.id))
    if Article.query.filter_by(category_id=c.id).count():
        flash("Move or delete this category's articles first — the category forms their URLs.", "error")
        return redirect(url_for("admin.category_edit", id=c.id))
    activity.log("category_delete", "category", c.id, c.name)
    db.session.delete(c)
    db.session.commit()
    flash("Category deleted.", "ok")
    return redirect(url_for("admin.categories"))


# --------------------------------------------------------------------- tags
@bp.get("/tags")
@permission_required("taxonomy.manage")
def tags():
    counts = dict(db.session.execute(select(Article.tags.property.secondary.c.tag_id, func.count())
                                     .group_by(Article.tags.property.secondary.c.tag_id)).all())
    rows = [{"cells": [t.name, f"/tag/{t.slug}", counts.get(t.id, 0), "Active" if t.active else "Inactive"],
             "link": url_for("admin.tag_edit", id=t.id)} for t in Tag.query.order_by(Tag.name)]
    return render_list("Tags", rows, ["Tag", "URL", "Articles", "Status"], new_url=url_for("admin.tag_edit", id=0), new_label="New tag")


@bp.route("/tags/<int:id>", methods=["GET", "POST"])
@permission_required("taxonomy.manage")
def tag_edit(id):
    t = db.get_or_404(Tag, id) if id else Tag()
    fields = [f("name", "Name", value=t.name, required=True, maxlength=60),
              f("slug", "URL slug", value=t.slug, maxlength=80),
              f("description", "Description", "textarea", t.description, maxlength=300, rows=2, help="Shown on the tag page and used as its meta description."),
              f("active", "Active", "bool", t.active if id else True, help="Inactive tags are hidden from the site; articles keep them.")]
    if request.method == "POST":
        vals, errors = parse(fields)
        slug, err = _slug_for(Tag, vals["slug"], vals["name"], t if id else None)
        if err:
            errors.append(err)
        dup = Tag.query.filter(func.lower(Tag.name) == vals["name"].lower(), Tag.id != (t.id or 0)).first()
        if dup:
            errors.append("A tag with that name already exists.")
        if not errors:
            t.name, t.slug, t.description, t.active = vals["name"], slug, vals["description"], vals["active"]
            db.session.add(t)
            db.session.flush()
            activity.log("tag_save", "tag", t.id, t.name)
            db.session.commit()
            flash("Tag saved.", "ok")
            return redirect(url_for("admin.tags"))
        flash_all(errors)
    extra = [("Delete tag", url_for("admin.tag_delete", id=t.id), "Delete this tag from all articles?", True)] if id else []
    return render_form("Edit tag" if id else "New tag", fields, back=url_for("admin.tags"), extra_actions=extra)


@bp.post("/tags/<int:id>/delete")
@permission_required("taxonomy.manage")
def tag_delete(id):
    t = db.get_or_404(Tag, id)
    activity.log("tag_delete", "tag", t.id, t.name)
    db.session.delete(t)
    db.session.commit()
    flash("Tag deleted.", "ok")
    return redirect(url_for("admin.tags"))


# ------------------------------------------------------------------ authors
def _author_fields(a: Author):
    users = [("", "— not linked to a login —")] + [
        (str(u.id), f"{u.name} ({u.email})") for u in User.query.order_by(User.name)
        if not u.author or u.author.id == a.id]
    return [
        f("name", "Name", value=a.name, required=True, maxlength=120),
        f("slug", "URL slug", value=a.slug, maxlength=140, help="Profile URL: /author/slug"),
        f("position", "Position / title", value=a.position, maxlength=160),
        f("expertise", "Areas of expertise", value=a.expertise, maxlength=300, help="Comma-separated, e.g. Monetary policy, Banking, Trade."),
        f("image_id", "Profile image", "media", a.image_id, preview=a.image.thumb_url if a.image else None),
        f("bio", "Biography", "textarea", a.bio, rows=6, maxlength=3000),
        f("twitter", "X (Twitter) URL", "url", a.twitter), f("linkedin", "LinkedIn URL", "url", a.linkedin),
        f("facebook", "Facebook URL", "url", a.facebook), f("telegram", "Telegram URL", "url", a.telegram),
        f("website", "Website URL", "url", a.website),
        f("user_id", "Linked login (lets a writer write under this profile)", "select", str(a.user_id or ""), options=users),
        f("active", "Active (visible on the site)", "bool", a.active if a.id else True),
    ]


@bp.get("/authors")
@permission_required("authors.manage")
def authors():
    counts = dict(db.session.execute(select(Article.author_id, func.count()).group_by(Article.author_id)).all())
    rows = [{"cells": [a.name, a.position, f"/author/{a.slug}", counts.get(a.id, 0), "Active" if a.active else "Hidden"],
             "link": url_for("admin.author_edit", id=a.id)} for a in Author.query.order_by(Author.name)]
    return render_list("Authors", rows, ["Name", "Position", "URL", "Articles", "Status"],
                       new_url=url_for("admin.author_edit", id=0), new_label="New author")


@bp.route("/authors/<int:id>", methods=["GET", "POST"])
@permission_required("authors.manage")
def author_edit(id):
    a = db.get_or_404(Author, id) if id else Author()
    fields = _author_fields(a)
    if request.method == "POST":
        vals, errors = parse(fields)
        slug, err = _slug_for(Author, vals["slug"], vals["name"], a if id else None)
        if err:
            errors.append(err)
        uid = int(vals["user_id"]) if str(vals["user_id"]).isdigit() else None
        if not errors:
            for k in ("name", "position", "expertise", "bio", "twitter", "linkedin", "facebook", "telegram", "website", "active"):
                setattr(a, k, vals[k])
            a.slug, a.image_id, a.user_id = slug, vals["image_id"], uid
            db.session.add(a)
            db.session.flush()
            activity.log("author_save", "author", a.id, a.name)
            db.session.commit()
            flash("Author saved.", "ok")
            return redirect(url_for("admin.authors"))
        flash_all(errors)
    return render_form("Edit author" if id else "New author", fields, back=url_for("admin.authors"))


# -------------------------------------------------------------------- pages
def _page_fields(p: Page):
    return [
        f("title", "Title", value=p.title, required=True, maxlength=200),
        f("slug", "URL slug", value=p.slug, maxlength=120, help="Page URL: /slug"),
        f("body_md", "Content (Markdown)", "markdown", p.body_md, rows=18, full=True),
        f("seo_title", "SEO title", value=p.seo_title, maxlength=120),
        f("meta_description", "Meta description", "textarea", p.meta_description, maxlength=320, rows=2),
        f("published", "Published", "bool", p.published if p.id else True),
        f("in_footer", "Link in site footer", "bool", p.in_footer if p.id else True),
        f("sort_order", "Footer order", "number", p.sort_order if p.id else 100),
    ]


@bp.get("/pages")
@permission_required("pages.manage")
def pages():
    rows = [{"cells": [p.title, f"/{p.slug}", "Published" if p.published else "Hidden", "Yes" if p.in_footer else "No"],
             "link": url_for("admin.page_edit", id=p.id)} for p in Page.query.order_by(Page.sort_order, Page.title)]
    return render_list("Pages", rows, ["Title", "URL", "Status", "In footer"], new_url=url_for("admin.page_edit", id=0),
                       new_label="New page", note="Legal and static pages. Starter text is generic — have counsel review before launch.")


@bp.route("/pages/<int:id>", methods=["GET", "POST"])
@permission_required("pages.manage")
def page_edit(id):
    p = db.get_or_404(Page, id) if id else Page()
    fields = _page_fields(p)
    if request.method == "POST":
        vals, errors = parse(fields)
        slug, err = _slug_for(Page, vals["slug"], vals["title"], p if id else None, top_level=True)
        if err:
            errors.append(err)
        if not errors:
            p.title, p.slug, p.seo_title, p.meta_description = vals["title"], slug, vals["seo_title"], vals["meta_description"]
            p.published, p.in_footer, p.sort_order = vals["published"], vals["in_footer"], vals["sort_order"]
            p.set_body(vals["body_md"])
            p.updated_at = utcnow()
            db.session.add(p)
            db.session.flush()
            activity.log("page_save", "page", p.id, p.title)
            db.session.commit()
            flash("Page saved.", "ok")
            return redirect(url_for("admin.pages"))
        flash_all(errors)
    extra = [("Delete page", url_for("admin.page_delete", id=p.id), "Delete this page?", True)] if id else []
    return render_form("Edit page" if id else "New page", fields, back=url_for("admin.pages"), extra_actions=extra)


@bp.post("/pages/<int:id>/delete")
@permission_required("pages.manage")
def page_delete(id):
    p = db.get_or_404(Page, id)
    activity.log("page_delete", "page", p.id, p.title)
    db.session.delete(p)
    db.session.commit()
    flash("Page deleted.", "ok")
    return redirect(url_for("admin.pages"))
