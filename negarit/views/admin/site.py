"""Homepage manager (lead, picks, breaking banner, section order/visibility) and the Data section admin."""
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for

from ...extensions import db
from ...models import (ARTICLE_TYPES, DATA_FREQUENCIES, HOME_KINDS, Article, Category, DataPoint, Dataset, HomeSection,
                       HomeSlot, Report)
from ...security import permission_required
from ...services import activity, content, data, homepage, settings
from ...utils import utcnow
from . import bp, render_form, render_list
from .helpers import f, flash_all, parse
from .taxonomy import _slug_for

TYPE_CHOICES = [(v, l) for v, l in ARTICLE_TYPES] + [("analysis,opinion", "Analysis + Opinion")]
KIND_LABELS = dict(HOME_KINDS)
PICKS = 6


# ------------------------------------------------------------------ homepage
def _recent_options(limit=150):
    return [(a.id, f"{a.title[:70]}  ·  {a.published_at:%b %d}") for a in content.fetch(limit)]


@bp.route("/homepage", methods=["GET", "POST"])
@permission_required("homepage.manage")
def homepage_manager():
    homepage.ensure_default_sections()
    if request.method == "POST":
        form = request.form
        live_ids = {a.id for a in content.fetch(400)}

        def pick(name):
            v = form.get(name, type=int)
            return v if v in live_ids else None

        HomeSlot.query.filter_by(slot="lead").delete()
        if pick("lead_id"):
            db.session.add(HomeSlot(slot="lead", article_id=pick("lead_id"), position=0))
        HomeSlot.query.filter_by(slot="picks").delete()
        seen = set()
        for i in range(PICKS):
            aid = pick(f"pick_{i}")
            if aid and aid not in seen:
                seen.add(aid)
                db.session.add(HomeSlot(slot="picks", article_id=aid, position=len(seen)))
        for sec in HomeSection.query.all():
            k = f"sec_{sec.id}_"
            if form.get(k + "delete") == "on":
                db.session.delete(sec)
                continue
            sec.visible = form.get(k + "visible") == "on"
            sec.title = (form.get(k + "title") or "").strip()[:120]
            sec.item_limit = min(max(form.get(k + "limit", sec.item_limit, type=int) or sec.item_limit, 1), 12)
            sec.position = form.get(k + "pos", sec.position, type=int) or sec.position
        kind = form.get("new_kind", "")
        if kind in KIND_LABELS:
            cid = form.get("new_category", type=int)
            types = form.get("new_types", "")
            if kind == "category" and not (cid and db.session.get(Category, cid)):
                flash("Choose a category for the new section.", "error")
            elif kind == "type" and types not in dict(TYPE_CHOICES):
                flash("Choose a content type for the new section.", "error")
            else:
                last = db.session.query(db.func.max(HomeSection.position)).scalar() or 0
                db.session.add(HomeSection(kind=kind, title=(form.get("new_title") or "").strip()[:120],
                                           category_id=cid if kind == "category" else None, article_type=types if kind == "type" else "",
                                           position=last + 10, visible=True, item_limit=min(max(form.get("new_limit", 4, type=int) or 4, 1), 12)))
        settings.save({"breaking_enabled": form.get("breaking_enabled"), "breaking_label": form.get("breaking_label", "Breaking"),
                       "breaking_hours": form.get("breaking_hours", "24")})
        activity.log("homepage_save", "homepage", None, "Homepage layout updated")
        db.session.commit()
        flash("Homepage saved.", "ok")
        return redirect(url_for("admin.homepage_manager"))
    lead = homepage.slot_articles("lead")
    picks = [a.id for a in homepage.slot_articles("picks")]
    picks += [None] * (PICKS - len(picks))
    return render_template("admin/homepage.html", sections=HomeSection.query.order_by(HomeSection.position, HomeSection.id).all(),
                           options=_recent_options(), lead_id=lead[0].id if lead else None, picks=picks, kinds=HOME_KINDS,
                           kind_labels=KIND_LABELS, categories=Category.query.filter(Category.active.is_(True)).order_by(Category.name).all(),
                           type_choices=TYPE_CHOICES, S=settings.all_settings(), breaking=content.breaking(24, 10))


# ---------------------------------------------------------------------- data
def _ds_fields(ds: Dataset):
    return [
        f("title", "Title", value=ds.title, required=True, maxlength=200),
        f("slug", "URL slug", value=ds.slug, maxlength=120, help="Page URL: /data/slug"),
        f("topic", "Topic / group", value=ds.topic or "General", maxlength=60, help="Datasets are grouped by topic on the Data page (e.g. Inflation, Exchange rates, Banking, Trade)."),
        f("unit", "Unit", value=ds.unit, required=True, maxlength=80, help="e.g. “% year-on-year”, “ETB per USD”, “USD million”."),
        f("frequency", "Frequency", "select", ds.frequency or "monthly", options=DATA_FREQUENCIES, required=True,
          help="Used to warn readers when the newest value is older than normal for this series."),
        f("decimals", "Decimal places shown", "number", ds.decimals if ds.id else 1),
        f("source_name", "Source", value=ds.source_name, maxlength=200, help="Required to publish: readers always see where numbers come from."),
        f("source_url", "Source link", "url", ds.source_url, maxlength=500),
        f("description", "Description", "textarea", ds.description, rows=3),
        f("context", "Context / methodology notes", "textarea", ds.context, rows=4, full=True),
        f("published", "Published", "bool", ds.published if ds.id else False,
           help="Unpublished datasets are invisible to the public. Add data first, then publish."),
    ]


@bp.get("/data")
@permission_required("data.manage")
def datasets():
    rows = []
    for ds in Dataset.query.order_by(Dataset.topic, Dataset.title):
        s = data.summary(ds)
        rows.append({"cells": [ds.title + (" (sample)" if ds.is_demo else ""), ds.topic, ds.unit, len(ds.points),
                               f"{data.fmt(s['last'].value, ds.decimals)} ({s['label']})" if s["last"] else "No data yet",
                               "Not recent" if s["last"] and s["stale"] else ("Current" if s["last"] else "-"),
                               "Published" if ds.published else "Hidden"],
                     "link": url_for("admin.dataset_edit", id=ds.id)})
    return render_list("Data indicators", rows, ["Dataset", "Topic", "Unit", "Points", "Latest", "Freshness", "Status"],
                       new_url=url_for("admin.dataset_edit", id=0), new_label="New dataset",
                       toolbar=[("Reports", url_for("admin.reports"))],
                       note="Values are entered by staff and always shown with their source, unit and date. Nothing is fetched or invented automatically.",
                       empty="No datasets yet. Create one, add its values from a published source, then publish it.")


@bp.route("/data/<int:id>", methods=["GET", "POST"])
@permission_required("data.manage")
def dataset_edit(id):
    ds = db.get_or_404(Dataset, id) if id else Dataset()
    fields = _ds_fields(ds)
    if request.method == "POST":
        vals, errors = parse(fields)
        slug, err = _slug_for(Dataset, vals["slug"], vals["title"], ds if id else None)
        if err:
            errors.append(err)
        if vals["published"] and not vals["source_name"]:
            errors.append("A source is required before a dataset can be published.")
        if vals["published"] and id and not ds.points:
            errors.append("Add at least one data point before publishing.")
        if vals["published"] and not id:
            errors.append("Save the dataset and add its data points first, then publish.")
        vals["decimals"] = min(max(vals["decimals"], 0), 6)
        if not errors:
            for k in ("title", "topic", "unit", "frequency", "decimals", "source_name", "source_url", "description", "context", "published"):
                setattr(ds, k, vals[k])
            ds.slug, ds.updated_at = slug, utcnow()
            ds.is_demo = ds.is_demo if id else False
            db.session.add(ds)
            db.session.flush()
            activity.log("dataset_save", "dataset", ds.id, ds.title)
            db.session.commit()
            flash("Dataset saved." + ("" if id else " Now add its data points."), "ok")
            return redirect(url_for("admin.dataset_points", id=ds.id) if not id else url_for("admin.datasets"))
        flash_all(errors)
        for fld in fields:
            fld["value"] = vals.get(fld["name"], fld["value"])
    extra = [("Delete dataset and its data", url_for("admin.dataset_delete", id=ds.id), "Delete this dataset and all its values?", True)] if id else []
    links = [("Manage data points", url_for("admin.dataset_points", id=ds.id))] if id else []
    return render_form("Edit dataset" if id else "New dataset", fields, back=url_for("admin.datasets"), extra_actions=extra, links=links)


@bp.post("/data/<int:id>/delete")
@permission_required("data.manage")
def dataset_delete(id):
    ds = db.get_or_404(Dataset, id)
    activity.log("dataset_delete", "dataset", ds.id, ds.title)
    db.session.delete(ds)
    db.session.commit()
    flash("Dataset deleted.", "ok")
    return redirect(url_for("admin.datasets"))


def _touch(ds, summary):
    ds.updated_at = utcnow()
    activity.log("dataset_points", "dataset", ds.id, f"{ds.title}: {summary}")
    db.session.commit()


@bp.get("/data/<int:id>/points")
@permission_required("data.manage")
def dataset_points(id):
    ds = db.get_or_404(Dataset, id)
    return render_template("admin/dataset_points.html", ds=ds, points=list(reversed(ds.points))[:200], total=len(ds.points),
                           s=data.summary(ds), fmt=data.fmt, label=data.period_label, chart=data.chart_svg(ds))


@bp.post("/data/<int:id>/points")
@permission_required("data.manage")
def dataset_point_add(id):
    ds = db.get_or_404(Dataset, id)
    period = data.parse_period(request.form.get("period", ""))
    try:
        value = float((request.form.get("value") or "").replace(",", "").strip())
        ok_value = value == value and abs(value) < 1e15
    except ValueError:
        ok_value = False
    if not period or not ok_value:
        flash("Enter a valid period (2026, 2026-03, 2026-Q2 or 2026-03-15) and a numeric value.", "error")
    else:
        pt = DataPoint.query.filter_by(dataset_id=ds.id, period=period).first()
        if pt:
            pt.value, pt.note = value, (request.form.get("note") or "")[:200]
        else:
            db.session.add(DataPoint(dataset_id=ds.id, period=period, value=value, note=(request.form.get("note") or "")[:200]))
        _touch(ds, f"{'updated' if pt else 'added'} {period}")
        flash("Data point saved.", "ok")
    return redirect(url_for("admin.dataset_points", id=ds.id))


@bp.post("/data/<int:id>/import")
@permission_required("data.manage")
def dataset_import(id):
    ds = db.get_or_404(Dataset, id)
    rows, errors = data.parse_import(request.form.get("csv", "")[:200000])
    if errors:
        flash_all(errors[:8] + ([f"\u2026and {len(errors) - 8} more problems."] if len(errors) > 8 else []))
        flash("Nothing was imported. Fix the lines above and try again.", "error")
    elif not rows:
        flash("No rows found. Paste lines like 2026-03,14.2", "error")
    else:
        existing = {p.period: p for p in ds.points}
        added = updated = 0
        for period, value, note in rows:
            if period in existing:
                existing[period].value, existing[period].note = value, note
                updated += 1
            else:
                db.session.add(DataPoint(dataset_id=ds.id, period=period, value=value, note=note))
                existing[period] = None
                added += 1
        _touch(ds, f"imported {added} new, {updated} updated")
        flash(f"Imported {added} new and {updated} updated data points.", "ok")
    return redirect(url_for("admin.dataset_points", id=ds.id))


@bp.post("/data/<int:id>/points/<int:pid>/delete")
@permission_required("data.manage")
def dataset_point_delete(id, pid):
    ds = db.get_or_404(Dataset, id)
    pt = db.get_or_404(DataPoint, pid)
    if pt.dataset_id != ds.id:
        abort(404)
    db.session.delete(pt)
    _touch(ds, f"deleted {pt.period}")
    flash("Data point deleted.", "ok")
    return redirect(url_for("admin.dataset_points", id=ds.id))


# ------------------------------------------------------------------- reports
def _report_fields(r: Report):
    return [
        f("title", "Title", value=r.title, required=True, maxlength=200),
        f("topic", "Topic", value=r.topic or "General", maxlength=60),
        f("summary", "Summary", "textarea", r.summary, rows=3),
        f("file_url", "Download link (https://…)", "url", r.file_url, required=True, maxlength=600,
          help="Link to the PDF/file on your storage, CDN or the publisher's site. Check you have the right to distribute it."),
        f("source_name", "Publisher / source", value=r.source_name, required=True, maxlength=200),
        f("published_on", "Publication date", "date", r.published_on),
        f("published", "Published", "bool", r.published if r.id else True),
    ]


@bp.get("/reports")
@permission_required("data.manage")
def reports():
    rows = [{"cells": [r.title + (" (sample)" if r.is_demo else ""), r.topic, r.source_name, r.published_on or "-",
                       "Published" if r.published else "Hidden"], "link": url_for("admin.report_edit", id=r.id)}
            for r in Report.query.order_by(Report.published_on.desc().nullslast(), Report.id.desc())]
    return render_list("Reports", rows, ["Title", "Topic", "Source", "Date", "Status"], new_url=url_for("admin.report_edit", id=0),
                       new_label="New report", toolbar=[("Data indicators", url_for("admin.datasets"))])


@bp.route("/reports/<int:id>", methods=["GET", "POST"])
@permission_required("data.manage")
def report_edit(id):
    r = db.get_or_404(Report, id) if id else Report()
    fields = _report_fields(r)
    if request.method == "POST":
        vals, errors = parse(fields)
        if not errors:
            for k in ("title", "topic", "summary", "file_url", "source_name", "published_on", "published"):
                setattr(r, k, vals[k])
            db.session.add(r)
            db.session.flush()
            activity.log("report_save", "report", r.id, r.title)
            db.session.commit()
            flash("Report saved.", "ok")
            return redirect(url_for("admin.reports"))
        flash_all(errors)
        for fld in fields:
            fld["value"] = vals.get(fld["name"], fld["value"])
    extra = [("Delete report", url_for("admin.report_delete", id=r.id), "Delete this report entry?", True)] if id else []
    return render_form("Edit report" if id else "New report", fields, back=url_for("admin.reports"), extra_actions=extra)


@bp.post("/reports/<int:id>/delete")
@permission_required("data.manage")
def report_delete(id):
    r = db.get_or_404(Report, id)
    activity.log("report_delete", "report", r.id, r.title)
    db.session.delete(r)
    db.session.commit()
    flash("Report deleted.", "ok")
    return redirect(url_for("admin.reports"))
