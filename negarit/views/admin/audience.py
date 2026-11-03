import csv
import io

from flask import Response, abort, flash, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from ...extensions import db
from ...models import INQUIRY_TYPES, Comment, ContactMessage, Subscriber
from ...security import permission_required
from ...services import activity, analytics
from ...utils import escape_like, to_local, utcnow
from . import bp, render_list
from .helpers import page_arg

INQUIRY = dict(INQUIRY_TYPES)


# -------------------------------------------------------------- subscribers
@bp.get("/subscribers")
@permission_required("subscribers.manage")
def subscribers():
    q = (request.args.get("q") or "").strip()[:100]
    status = request.args.get("status", "")
    stmt = db.select(Subscriber).order_by(Subscriber.created_at.desc())
    if q:
        stmt = stmt.where(Subscriber.email.ilike(f"%{escape_like(q)}%", escape="\\"))
    if status in ("pending", "confirmed", "unsubscribed"):
        stmt = stmt.where(Subscriber.status == status)
    p = db.paginate(stmt, page=page_arg(), per_page=50, error_out=False)
    rows = [{"cells": [s.email, s.status.title(), to_local(s.created_at).strftime("%Y-%m-%d %H:%M"),
                       to_local(s.confirmed_at).strftime("%Y-%m-%d") if s.confirmed_at else "—"],
             "actions": [("Remove", url_for("admin.subscriber_delete", id=s.id), "Remove this subscriber?", True)]}
            for s in p.items]
    tabs = [("All", url_for("admin.subscribers"), not status)] + [
        (s.title(), url_for("admin.subscribers", status=s), status == s) for s in ("confirmed", "pending", "unsubscribed")]
    from ...mailer import delivery_enabled
    note = "" if delivery_enabled() else ("Email delivery is NOT configured (EMAIL_PROVIDER / EMAIL_API_KEY / EMAIL_FROM): confirmation emails "
                                           "cannot be sent, so new subscribers stay Pending until it is.")
    return render_list("Subscribers", rows, ["Email", "Status", "Signed up", "Confirmed"], pagination=p, q=q, tabs=tabs, note=note,
                       toolbar=[("Export CSV", url_for("admin.subscribers_csv")),
                                ("Newsletter settings", url_for("admin.settings") + "#Newsletter")],
                       empty="No subscribers found.")


@bp.get("/subscribers.csv")
@permission_required("subscribers.manage")
def subscribers_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["email", "status", "created_at_utc", "confirmed_at_utc"])
    for s in Subscriber.query.order_by(Subscriber.created_at).yield_per(500):
        w.writerow([("'" + s.email) if s.email[:1] in "=+-@\t\r" else s.email, s.status, s.created_at, s.confirmed_at or ""])
    activity.log("subscribers_export", "subscriber", None, "Exported subscriber list")
    db.session.commit()
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=subscribers.csv"})


@bp.post("/subscribers/<int:id>/delete")
@permission_required("subscribers.manage")
def subscriber_delete(id):
    s = db.get_or_404(Subscriber, id)
    activity.log("subscriber_delete", "subscriber", s.id, "Removed subscriber")
    db.session.delete(s)
    db.session.commit()
    flash("Subscriber removed.", "ok")
    return redirect(url_for("admin.subscribers"))


# ----------------------------------------------------------------- comments
@bp.get("/comments")
@permission_required("comments.moderate")
def comments():
    status = request.args.get("status", "pending")
    stmt = db.select(Comment).order_by(Comment.created_at.desc())
    if status in ("pending", "approved", "spam"):
        stmt = stmt.where(Comment.status == status)
    p = db.paginate(stmt, page=page_arg(), per_page=30, error_out=False)
    rows = []
    for c in p.items:
        acts = []
        if c.status != "approved":
            acts.append(("Approve", url_for("admin.comment_action", id=c.id, action="approve"), None, False))
        if c.status != "spam":
            acts.append(("Spam", url_for("admin.comment_action", id=c.id, action="spam"), None, False))
        acts.append(("Delete", url_for("admin.comment_action", id=c.id, action="delete"), "Delete this comment?", True))
        art = c.article
        rows.append({"cells": [Markup(f"<strong>{escape(c.name)}</strong><br><span class='muted'>{escape(c.email)}</span>"),
                               Markup(f"<div class='wrap-text'>{escape(c.body)}</div>"),
                               Markup(f"<a href='{escape(art.url_path)}' target='_blank' rel='noopener'>{escape(art.title[:60])}</a>"),
                               c.status.title(), to_local(c.created_at).strftime("%Y-%m-%d %H:%M")], "actions": acts})
    tabs = [(s.title(), url_for("admin.comments", status=s), status == s) for s in ("pending", "approved", "spam")]
    return render_list("Comments", rows, ["Author", "Comment", "Article", "Status", "Posted"], pagination=p, tabs=tabs,
                       empty="No comments in this view.")


@bp.post("/comments/<int:id>/<action>")
@permission_required("comments.moderate")
def comment_action(id, action):
    c = db.get_or_404(Comment, id)
    if action == "delete":
        db.session.delete(c)
    elif action in ("approve", "spam"):
        c.status = "approved" if action == "approve" else "spam"
    else:
        abort(404)
    activity.log(f"comment_{action}", "comment", id, f"Comment by {c.name}")
    db.session.commit()
    flash("Comment updated.", "ok")
    return redirect(request.referrer or url_for("admin.comments"))


# ----------------------------------------------------------------- messages
@bp.get("/messages")
@permission_required("messages.view")
def messages():
    status = request.args.get("status", "new")
    stmt = db.select(ContactMessage).order_by(ContactMessage.created_at.desc())
    if status in ("new", "read", "archived"):
        stmt = stmt.where(ContactMessage.status == status)
    p = db.paginate(stmt, page=page_arg(), per_page=30, error_out=False)
    rows = []
    for m in p.items:
        acts = [("Reply", f"mailto:{m.email}?subject=Re: {m.subject}", None, False)]
        if m.status != "read":
            acts.append(("Mark read", url_for("admin.message_action", id=m.id, action="read"), None, False))
        if m.status != "archived":
            acts.append(("Archive", url_for("admin.message_action", id=m.id, action="archive"), None, False))
        acts.append(("Delete", url_for("admin.message_action", id=m.id, action="delete"), "Delete this message?", True))
        rows.append({"cells": [Markup(f"<strong>{escape(m.name)}</strong><br><span class='muted'>{escape(m.email)}</span>"),
                               INQUIRY.get(m.inquiry_type, m.inquiry_type),
                               Markup(f"<details><summary>{escape(m.subject)}</summary><div class='wrap-text'>{escape(m.message)}</div></details>"),
                               to_local(m.created_at).strftime("%Y-%m-%d %H:%M")], "actions": acts})
    tabs = [(s.title(), url_for("admin.messages", status=s), status == s) for s in ("new", "read", "archived")]
    return render_list("Messages", rows, ["From", "Type", "Subject", "Received"], pagination=p, tabs=tabs, empty="No messages.")


@bp.post("/messages/<int:id>/<action>")
@permission_required("messages.view")
def message_action(id, action):
    m = db.get_or_404(ContactMessage, id)
    if action == "delete":
        db.session.delete(m)
    elif action in ("read", "archive"):
        m.status = "read" if action == "read" else "archived"
    else:
        abort(404)
    activity.log(f"message_{action}", "message", id, m.subject[:100])
    db.session.commit()
    return redirect(request.referrer or url_for("admin.messages"))


# ---------------------------------------------------------------- analytics
@bp.get("/analytics")
@permission_required("analytics.view")
def analytics_page():
    from datetime import date, timedelta

    def d(name):
        try:
            return date.fromisoformat(request.args.get(name, ""))
        except ValueError:
            return None
    start, end = d("start"), d("end")
    days = request.args.get("days", 30, type=int)
    if not (start and end):
        days = days if days in (7, 30, 90, 365) else 30
        end = utcnow().date()
        start = end - timedelta(days=days - 1)
    else:
        days = None
    return render_template("admin/analytics.html", days=days, r=analytics.report(start, end), o=analytics.overview())
