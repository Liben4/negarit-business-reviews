from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from ...extensions import db, limiter
from ...models import Media
from ...security import permission_required
from ...services import activity, media as media_service
from ...storage import UploadError
from ...utils import escape_like
from . import bp
from .helpers import flash_all, page_arg, parse, f


def _payload(m: Media) -> dict:
    return {"id": m.id, "url": m.url, "thumb": m.thumb_url, "alt": m.alt_text, "caption": m.caption, "credit": m.credit,
            "filename": m.filename, "width": m.width, "height": m.height}


@bp.get("/media")
@permission_required("media.browse")
def media_library():
    q = (request.args.get("q") or "").strip()[:100]
    stmt = db.select(Media).order_by(Media.created_at.desc())
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Media.filename.ilike(like, escape="\\"), Media.alt_text.ilike(like, escape="\\"),
                              Media.caption.ilike(like, escape="\\"), Media.credit.ilike(like, escape="\\")))
    only_unused = request.args.get("unused") == "1"
    if only_unused:
        stmt = stmt.where(Media.id.in_([m.id for m in media_service.unused(500)] or [0]))
    p = db.paginate(stmt, page=page_arg(), per_page=24, error_out=False)
    return render_template("admin/media.html", pagination=p, q=q, only_unused=only_unused, usage=media_service.usage)


@bp.get("/media/picker")
@permission_required("media.browse")
def media_picker():
    q = (request.args.get("q") or "").strip()[:100]
    stmt = db.select(Media).order_by(Media.created_at.desc())
    if q:
        like = f"%{escape_like(q)}%"
        stmt = stmt.where(or_(Media.filename.ilike(like, escape="\\"), Media.alt_text.ilike(like, escape="\\")))
    p = db.paginate(stmt, page=page_arg(), per_page=18, error_out=False)
    return jsonify(items=[_payload(m) for m in p.items], has_next=p.has_next, page=p.page)


@bp.post("/upload")
@permission_required("media.upload")
@limiter.limit("60/hour")
def upload():
    """Multipart upload used by the media library and the article editor."""
    files = request.files.getlist("file")
    if not files:
        return jsonify(error="No file."), 400
    out = []
    try:
        for fs in files[:6]:
            raw = fs.read(request_limit() + 1)
            m = media_service.create_media(raw, fs.filename or "image", current_user,
                                           alt=request.form.get("alt", ""), caption=request.form.get("caption", ""),
                                           credit=request.form.get("credit", ""))
            activity.log("media_upload", "media", m.id, m.filename)
            out.append(_payload(m))
        db.session.commit()
    except UploadError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    return jsonify(items=out, **out[0])


def request_limit() -> int:
    from flask import current_app
    return current_app.config["MAX_CONTENT_LENGTH"]


@bp.route("/media/<int:id>", methods=["GET", "POST"])
@permission_required("media.browse")
def media_edit(id):
    m = db.get_or_404(Media, id)
    fields = [f("alt_text", "Alt text (describe the image for screen readers)", value=m.alt_text, maxlength=300),
              f("caption", "Caption", value=m.caption, maxlength=300),
              f("credit", "Credit / photographer", value=m.credit, maxlength=200)]
    if request.method == "POST":
        if not current_user.can("media.upload"):
            abort(403)
        vals, errors = parse(fields)
        if not errors:
            m.alt_text, m.caption, m.credit = vals["alt_text"], vals["caption"], vals["credit"]
            activity.log("media_edit", "media", m.id, m.filename)
            db.session.commit()
            flash("Image details saved.", "ok")
            return redirect(url_for("admin.media_library"))
        flash_all(errors)
    return render_template("admin/media_edit.html", m=m, fields=fields, uses=media_service.usage(m))


@bp.post("/media/delete-unused")
@permission_required("media.delete")
def media_delete_unused():
    """Safe bulk cleanup: only images with zero references (featured, galleries, authors, ads, bodies, settings)."""
    n = 0
    for m in media_service.unused(500):
        try:
            media_service.delete_media(m)  # re-checks usage; refuses if anything references it
            n += 1
        except UploadError:
            continue
    activity.log("media_delete_unused", "media", None, f"Deleted {n} unused image(s)")
    db.session.commit()
    flash(f"Deleted {n} unused image{'s' if n != 1 else ''}.", "ok")
    return redirect(url_for("admin.media_library"))


@bp.post("/media/<int:id>/delete")
@permission_required("media.delete")
def media_delete(id):
    m = db.get_or_404(Media, id)
    try:
        name = m.filename
        media_service.delete_media(m)
        activity.log("media_delete", "media", id, name)
        db.session.commit()
        flash("Image deleted.", "ok")
    except UploadError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("admin.media_library"))
