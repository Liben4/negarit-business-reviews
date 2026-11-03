"""Users, site settings, SEO tools, activity logs and the signed-in user's account."""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup, escape
from sqlalchemy import or_

from ...cli import ensure_author
from ...extensions import db
from ...mailer import send_email
from ...models import ActivityLog, Article, User
from ...permissions import ROLE_LABELS, assignable_roles
from ...security import permission_required
from ...services import activity, settings
from ...utils import escape_like, to_local, utcnow
from . import bp, render_form, render_list
from .helpers import f, flash_all, page_arg, parse

MIN_PW = 12


# -------------------------------------------------------------------- users
def _guard_target(u: User):
    """Admins may not touch super admins."""
    if u.role == "super_admin" and current_user.role != "super_admin":
        abort(403)


def _last_super_admin(u: User) -> bool:
    return u.role == "super_admin" and u.active and User.query.filter(
        User.role == "super_admin", User.active.is_(True), User.id != u.id).count() == 0


@bp.get("/users")
@permission_required("users.manage")
def users():
    q = (request.args.get("q") or "").strip()[:100]
    stmt = db.select(User).order_by(User.created_at)
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(User.name.ilike(like, escape="\\"), User.email.ilike(like, escape="\\")))
    p = db.paginate(stmt, page=page_arg(), per_page=50, error_out=False)
    rows = [{"cells": [u.name, u.email, ROLE_LABELS.get(u.role, u.role),
                       Markup('<span class="pill published">Active</span>') if u.active else Markup('<span class="pill draft">Disabled</span>'),
                       to_local(u.last_login_at).strftime("%Y-%m-%d %H:%M") if u.last_login_at else "Never"],
             "link": url_for("admin.user_edit", id=u.id)} for u in p.items]
    return render_list("Users", rows, ["Name", "Email", "Role", "Status", "Last sign-in"], pagination=p, q=q,
                       new_url=url_for("admin.user_edit", id=0), new_label="New user")


@bp.route("/users/<int:id>", methods=["GET", "POST"])
@permission_required("users.manage")
def user_edit(id):
    u = db.get_or_404(User, id) if id else User(role="writer", active=True)
    if id:
        _guard_target(u)
    roles = [(r, ROLE_LABELS[r]) for r in assignable_roles(current_user.role)]
    self_edit = bool(id) and u.id == current_user.id
    fields = [
        f("name", "Full name", value=u.name, required=True, maxlength=120),
        f("email", "Email", "email", u.email, required=True),
        f("role", "Role", "select", u.role, options=roles, required=True,
          help="Contributor: submit only · Writer: own articles · Editor: review & publish · Admin: site management · Super Admin: everything."),
        f("active", "Account active", "bool", u.active if id else True),
        f("password", "New password" if id else "Password", "password", None, help=f"Minimum {MIN_PW} characters." + (" Leave blank to keep the current password." if id else "")),
    ]
    if request.method == "POST":
        vals, errors = parse(fields)
        pw = request.form.get("password", "")
        if (not id or pw) and len(pw) < MIN_PW:
            errors.append(f"Password must be at least {MIN_PW} characters.")
        if vals["role"] not in dict(roles):
            errors.append("You cannot assign that role.")
        other = User.query.filter(User.email == vals["email"], User.id != (u.id or 0)).first()
        if other:
            errors.append("That email is already registered.")
        if self_edit and (vals["role"] != u.role or not vals["active"]):
            errors.append("You cannot change your own role or deactivate yourself.")
        if id and _last_super_admin(u) and (vals["role"] != "super_admin" or not vals["active"]):
            errors.append("At least one active Super Admin must remain.")
        if not errors:
            u.name, u.email, u.role, u.active = vals["name"], vals["email"], vals["role"], vals["active"]
            if pw:
                u.set_password(pw)
            db.session.add(u)
            db.session.flush()
            if u.role in ("editor", "writer", "contributor", "admin", "super_admin"):
                ensure_author(u)
            activity.log("user_save", "user", u.id, f"{u.email} ({u.role}{'' if u.active else ', disabled'})")
            db.session.commit()
            flash("User saved.", "ok")
            return redirect(url_for("admin.users"))
        flash_all(errors)
        for fld in fields:
            if fld["name"] != "password":
                fld["value"] = vals.get(fld["name"], fld["value"])
    extra = []
    if id and not self_edit:
        extra.append(("Send password-reset email", url_for("admin.user_reset", id=u.id), "Email this user a password-reset link?", False))
    return render_form("Edit user" if id else "New user", fields, back=url_for("admin.users"), extra_actions=extra)


@bp.post("/users/<int:id>/reset")
@permission_required("users.manage")
def user_reset(id):
    from ..auth import make_reset_token
    u = db.get_or_404(User, id)
    _guard_target(u)
    link = f"{current_app.config['SITE_URL']}/admin/reset/{make_reset_token(u)}"
    name = settings.get("site_name")
    ok = send_email(u.email, f"Reset your {name} password", render_template("email/reset.html", link=link, name=name),
                    f"Reset your password (valid for 1 hour): {link}")
    activity.log("user_reset_link", "user", u.id, f"Reset link sent to {u.email}" if ok else f"Reset email failed for {u.email}")
    db.session.commit()
    flash("Reset link sent." if ok else "The email could not be sent — check EMAIL_* settings.", "ok" if ok else "error")
    return redirect(url_for("admin.user_edit", id=u.id))


# ----------------------------------------------------------------- settings
def _setting_spec(groups=None):
    S = settings.all_settings()
    spec = []
    for group, key, label, kind, _default, help_ in settings.FIELDS:
        if groups and group not in groups:
            continue
        opts = []
        if kind.startswith("select:"):
            opts = [(o, o.title()) for o in kind.split(":")[1].split(",")]
            kind = "select"
        fld = f(key, label, kind, S.get(key), options=opts, help=help_, maxlength=400 if kind in ("text", "url", "media_url") else None,
                rows=3 if kind == "textarea" else None, preview=S.get(key) if kind == "media_url" and S.get(key) else None)
        fld["group"] = group
        if kind == "bool":
            fld["value"] = S.get(key) in ("1", "true", "on")
        spec.append(fld)
    return spec


def _save_settings(spec):
    vals, errors = parse(spec)
    if errors:
        flash_all(errors)
        return False
    changed = settings.save(vals)
    activity.log("settings_save", "settings", None, f"{changed} setting(s) changed")
    db.session.commit()
    flash("Settings saved.", "ok")
    return True


@bp.route("/settings", methods=["GET", "POST"], endpoint="settings")
@permission_required("settings.manage")
def settings_page():
    spec = _setting_spec()
    if request.method == "POST" and _save_settings(spec):
        return redirect(url_for("admin.settings"))
    groups = []
    for fld in spec:
        if not groups or groups[-1][0] != fld["group"]:
            groups.append((fld["group"], []))
        groups[-1][1].append(fld)
    return render_template("admin/settings.html", groups=groups)


@bp.route("/seo", methods=["GET", "POST"])
@permission_required("seo.manage")
def seo():
    spec = _setting_spec(groups={"SEO"})
    if request.method == "POST" and _save_settings(spec):
        return redirect(url_for("admin.seo"))
    base = Article.query.filter(Article.live_filter(), Article.is_demo.is_(False))
    audit = {
        "No meta description": base.filter(Article.meta_description == "").order_by(Article.published_at.desc()).limit(25).all(),
        "No featured image": base.filter(Article.featured_media_id.is_(None)).order_by(Article.published_at.desc()).limit(25).all(),
        "No excerpt": base.filter(Article.excerpt == "").order_by(Article.published_at.desc()).limit(25).all(),
    }
    site = current_app.config["SITE_URL"]
    return render_template("admin/seo.html", fields=spec, audit=audit, site=site)


# --------------------------------------------------------------------- logs
@bp.get("/logs")
@permission_required("logs.view")
def logs():
    q = (request.args.get("q") or "").strip()[:100]
    stmt = db.select(ActivityLog).order_by(ActivityLog.created_at.desc())
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(ActivityLog.action.ilike(like, escape="\\"), ActivityLog.summary.ilike(like, escape="\\"),
                              ActivityLog.user_email.ilike(like, escape="\\")))
    p = db.paginate(stmt, page=page_arg(), per_page=50, error_out=False)
    rows = [{"cells": [to_local(l.created_at).strftime("%Y-%m-%d %H:%M:%S"), l.user_email or "—", l.action,
                       f"{l.entity_type} #{l.entity_id}" if l.entity_id else l.entity_type, l.summary, l.ip]} for l in p.items]
    return render_list("Activity logs", rows, ["When", "User", "Action", "Item", "Details", "IP"], pagination=p, q=q,
                       empty="No activity recorded yet.")


# ------------------------------------------------------------------ account
@bp.route("/account", methods=["GET", "POST"])
def account():
    u = current_user._get_current_object()
    fields = [f("name", "Display name", value=u.name, required=True, maxlength=120),
              f("current_password", "Current password (only to change it)", "password"),
              f("new_password", "New password", "password", help=f"Minimum {MIN_PW} characters.")]
    if request.method == "POST":
        vals, errors = parse(fields)
        new = request.form.get("new_password", "")
        if new:
            if not u.check_password(request.form.get("current_password", "")):
                errors.append("Current password is incorrect.")
            elif len(new) < MIN_PW:
                errors.append(f"Password must be at least {MIN_PW} characters.")
        if not errors:
            u.name = vals["name"]
            if new:
                u.set_password(new)
            activity.log("account_update", "user", u.id, "Profile/password updated" if new else "Profile updated")
            db.session.commit()
            flash("Account updated.", "ok")
            return redirect(url_for("admin.account"))
        flash_all(errors)
    note = ""
    if u.author:
        note = f"Your public author profile is managed under Authors ({u.author.name})."
    return render_form("My account", fields, note=note)
