"""Public Data section: indicators with source/date/units, SVG charts, tables, CSV downloads, reports."""
from flask import Blueprint, Response, abort, current_app, render_template, request

from ..models import Article, Dataset, Report
from ..services import content, data
from . import cached

bp = Blueprint("data", __name__, url_prefix="/data")


def _canon(path):
    return current_app.config["SITE_URL"] + path


@bp.get("", strict_slashes=False)  # /data and /data/ both serve the index with no redirect hop
@cached(300, 1800)
def index():
    sets = [d for d in Dataset.query.filter_by(published=True).order_by(Dataset.topic, Dataset.title).all()]
    topics: dict = {}
    for ds in sets:
        topics.setdefault(ds.topic or "General", []).append((ds, data.summary(ds)))
    reports = Report.query.filter_by(published=True).order_by(Report.published_on.desc().nullslast(), Report.id.desc()).limit(12).all()
    stories = content.fetch(6, Article.article_type == "data-report")
    return render_template("public/data_index.html", topics=topics, reports=reports, stories=stories, canonical=_canon("/data"),
                           breadcrumbs=[("Home", "/"), ("Data", None)])


@bp.get("/<slug>")
@cached(300, 1800)
def dataset(slug):
    csv_mode = slug.endswith(".csv")
    ds = Dataset.query.filter_by(slug=slug[:-4] if csv_mode else slug, published=True).first_or_404()
    if csv_mode:
        return Response(data.csv_text(ds), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={ds.slug}.csv"})
    s = data.summary(ds)
    page = request.args.get("all") == "1"
    rows = list(reversed(ds.points))
    return render_template("public/dataset.html", ds=ds, s=s, chart=data.chart_svg(ds), rows=rows if page else rows[:36],
                           total=len(rows), show_all=page, age=data.age_days(ds),
                           canonical=_canon(f"/data/{ds.slug}"), breadcrumbs=[("Home", "/"), ("Data", "/data"), (ds.title, None)])
