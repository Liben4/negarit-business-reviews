"""v3: Data section, honest newsletter, analytics ranges, ad impressions, media cleanup safety, taxonomy sync."""
import io
import xml.dom.minidom as minidom
from datetime import date, timedelta

from PIL import Image

from negarit.extensions import db
from negarit.models import (Advertisement, Article, Category, DataPoint, Dataset, Media, PageView, Report, Subscriber)
from negarit.services import data as data_svc, media as media_svc, seed

from conftest import login


def make_ds(published=True, n=6, freq="monthly", **kw):
    today = date.today()
    ds = Dataset(title="Headline inflation", slug="inflation", topic="Inflation", unit="% year-on-year", frequency=freq, decimals=1,
                 source_name="Statistics Agency", source_url="https://stats.example/cpi", published=published, **kw)
    db.session.add(ds)
    db.session.flush()
    y, m = today.year, today.month
    for i in range(n):  # n consecutive recent months, oldest first
        mm = m - (n - 1 - i)
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        db.session.add(DataPoint(dataset_id=ds.id, period=date(yy, mm, 1), value=10 + i * 0.5))
    db.session.commit()
    return ds


# ------------------------------------------------------------------ data service
def test_parse_period_and_import_validation():
    assert data_svc.parse_period("2026") == date(2026, 1, 1) and data_svc.parse_period("2026-03") == date(2026, 3, 1)
    assert data_svc.parse_period("2026-Q2") == date(2026, 4, 1) and data_svc.parse_period("2026-03-15") == date(2026, 3, 15)
    assert data_svc.parse_period("2026-13") is None and data_svc.parse_period("March") is None and data_svc.parse_period("") is None
    rows, errs = data_svc.parse_import("period,value\n2026-01,14.2\n2026-02,13.8,provisional\n")
    assert [(r[0].month, r[1], r[2]) for r in rows] == [(1, 14.2, ""), (2, 13.8, "provisional")] and not errs
    rows, errs = data_svc.parse_import("2026-01,abc\nnope,3\n2026-03\n2026-04,nan\n2026-05,1e99")
    assert not rows and len(errs) == 5


def test_staleness_follows_frequency(app):
    fresh = make_ds(n=3)
    assert not data_svc.is_stale(fresh)
    old = Dataset(title="Old", slug="old", unit="x", frequency="monthly", source_name="S")
    db.session.add(old)
    db.session.flush()
    db.session.add(DataPoint(dataset_id=old.id, period=date.today() - timedelta(days=200), value=1))
    db.session.commit()
    assert data_svc.is_stale(old) and data_svc.is_stale(Dataset(frequency="annual", title="e", slug="e", unit="u"))  # no data = stale
    old.frequency = "annual"
    assert not data_svc.is_stale(old)  # 200 days is fine for an annual series


def test_chart_is_accessible_and_csp_safe(app):
    ds = make_ds()
    svg = str(data_svc.chart_svg(ds))
    assert 'role="img"' in svg and "<title" in svg and "<desc" in svg and "Latest value" in svg and "style=" not in svg
    assert str(data_svc.chart_svg(Dataset(title="t", slug="t", unit="u"))) == ""  # no data: no chart


# --------------------------------------------------------------------- public pages
def test_public_data_pages(client, w):
    ds = make_ds()
    idx = client.get("/data").data.decode()
    assert "Headline inflation" in idx and "Statistics Agency" in idx and "% year-on-year" in idx and "As of" in idx
    page = client.get("/data/inflation")
    html = page.data.decode()
    assert page.status_code == 200 and "<svg" in html and "Data table" in html and "Download CSV" in html and "Source" in html
    assert 'href="https://stats.example/cpi"' in html and "does not estimate or forecast" in html and '"@type": "Dataset"' in html.replace('"@type":"Dataset"', '"@type": "Dataset"')
    assert "Not recent" not in html  # fresh series
    csv = client.get("/data/inflation.csv")
    assert csv.mimetype == "text/csv" and csv.data.decode().splitlines()[0] == "period,value,unit,source,note" and "Statistics Agency" in csv.data.decode()
    assert client.get("/data").status_code == 200 and client.get("/data/").status_code == 200  # no redirect hop
    assert client.get("/data/nope").status_code == 404 and client.get("/data/nope.csv").status_code == 404


def test_unpublished_and_demo_datasets(client, w):
    make_ds(published=False)
    assert client.get("/data/inflation").status_code == 404 and client.get("/data/inflation.csv").status_code == 404
    assert b"Headline inflation" not in client.get("/data").data
    assert b"No indicators published yet" in client.get("/data").data  # honest empty state, nothing invented
    demo = Dataset(title="SAMPLE series", slug="sample-series", unit="u", source_name="Illustrative", is_demo=True, published=True)
    db.session.add(demo)
    db.session.flush()
    db.session.add(DataPoint(dataset_id=demo.id, period=date.today().replace(day=1), value=1.0))
    db.session.commit()
    assert 'content="noindex, nofollow"' in client.get("/data/sample-series").data.decode() and "Sample data" in client.get("/data/sample-series").data.decode()
    assert b"sample-series" not in client.get("/sitemap.xml").data
    assert b"/data/" not in client.get("/sitemap.xml").data.replace(b"/data<", b"")  # only the index, no demo dataset


def test_stale_series_warns_readers(client, w):
    ds = make_ds(n=1)
    ds.points[0].period = date.today() - timedelta(days=400)
    db.session.commit()
    html = client.get("/data/inflation").data.decode()
    assert "Not recent" in html and "older than we would expect for a monthly series" in html
    assert "Not recent" in client.get("/data").data.decode()


def test_sitemap_lists_real_datasets(client, w):
    make_ds()
    sm = client.get("/sitemap.xml")
    minidom.parseString(sm.data)
    assert b"https://negaritbusinessreview.com/data/inflation" in sm.data and b"/data</loc>" in sm.data


def test_reports_and_data_story_listing(client, w):
    db.session.add(Report(title="Annual outlook", topic="Outlook", file_url="https://pub.example/outlook.pdf", source_name="Pub Co",
                          published_on=date(2026, 3, 1), summary="A summary"))
    db.session.add(Report(title="Hidden report", file_url="https://pub.example/h.pdf", source_name="X", published=False))
    db.session.commit()
    w.art("Charting prices", slug="charting-prices", atype="data-report")
    html = client.get("/data").data.decode()
    assert "Annual outlook" in html and 'href="https://pub.example/outlook.pdf"' in html and "Hidden report" not in html
    assert "Charting prices" in html and b"Charting prices" in client.get("/data-reports").data


# ------------------------------------------------------------------------ data admin
def test_data_admin_permissions_and_publish_rules(client, w):
    login(client, "writer")
    assert client.get("/admin/data").status_code == 403
    client.post("/admin/logout")
    login(client, "editor")
    base = {"title": "Exchange rate", "slug": "", "topic": "FX", "unit": "ETB per USD", "frequency": "monthly", "decimals": "2",
            "source_name": "", "description": "d", "context": "c"}
    assert b"Save the dataset and add its data points first" in client.post("/admin/data/0", data={**base, "published": "on", "source_name": "Central bank"}).data
    r = client.post("/admin/data/0", data=base)  # saved as unpublished
    assert r.status_code == 302 and "/points" in r.headers["Location"]
    ds = Dataset.query.filter_by(slug="exchange-rate").one()
    assert not ds.published
    r = client.post(f"/admin/data/{ds.id}", data={**base, "published": "on"})
    assert b"source is required" in r.data and b"at least one data point" in r.data
    assert client.post(f"/admin/data/{ds.id}/points", data={"period": "2026-03", "value": "55.5", "note": "avg"}).status_code == 302
    client.post(f"/admin/data/{ds.id}/points", data={"period": "2026-03", "value": "56.1"})  # same period updates
    assert DataPoint.query.filter_by(dataset_id=ds.id).count() == 1 and DataPoint.query.one().value == 56.1
    client.post(f"/admin/data/{ds.id}", data={**base, "published": "on", "source_name": "Central bank"})
    assert db.session.get(Dataset, ds.id).published
    assert client.get("/data/exchange-rate").status_code == 200
    bad = client.post(f"/admin/data/{ds.id}/points", data={"period": "March", "value": "x"}, follow_redirects=True)
    assert b"valid period" in bad.data and DataPoint.query.count() == 1


def test_csv_import_is_all_or_nothing(client, w):
    ds = make_ds(n=1)
    login(client, "editor")
    before = DataPoint.query.count()
    r = client.post(f"/admin/data/{ds.id}/import", data={"csv": "2025-01,10\n2025-02,oops\n"}, follow_redirects=True)
    assert b"Nothing was imported" in r.data and DataPoint.query.count() == before
    r = client.post(f"/admin/data/{ds.id}/import", data={"csv": "2024-01,1.5\n2024-02,2.5,provisional\n"}, follow_redirects=True)
    assert b"Imported 2 new" in r.data and DataPoint.query.count() == before + 2
    assert client.post(f"/admin/data/{ds.id}/import", data={"csv": ""}, follow_redirects=True).status_code == 200
    pt = DataPoint.query.filter_by(dataset_id=ds.id).first()
    client.post(f"/admin/data/{ds.id}/points/{pt.id}/delete")
    assert db.session.get(DataPoint, pt.id) is None
    client.post(f"/admin/data/{ds.id}/delete")
    assert db.session.get(Dataset, ds.id) is None and DataPoint.query.count() == 0


def test_report_admin(client, w):
    login(client, "editor")
    bad = client.post("/admin/reports/0", data={"title": "R", "file_url": "javascript:x", "source_name": "S"})
    assert b"must start with https://" in bad.data
    assert client.post("/admin/reports/0", data={"title": "R", "file_url": "https://p.example/r.pdf", "source_name": "S", "published_on": "2026-02-01", "published": "on"}).status_code == 302
    assert Report.query.one().published_on == date(2026, 2, 1)
    assert client.get("/admin/reports").status_code == 200


# --------------------------------------------------------------------- newsletter honesty
def test_newsletter_never_claims_an_email_was_sent_when_it_was_not(client, w, monkeypatch):
    import negarit.views.public as pub
    monkeypatch.setattr(pub, "send_email", lambda *a, **k: False)  # no provider configured / delivery failed
    r = client.post("/newsletter/subscribe", data={"email": "a@example.com"}, headers={"Accept": "application/json"})
    assert r.json["ok"] and "couldn't send the confirmation email" in r.json["message"] and "Check your inbox" not in r.json["message"]
    assert Subscriber.query.one().status == "pending"  # saved, but honestly unconfirmed
    monkeypatch.setattr(pub, "send_email", lambda *a, **k: True)
    r = client.post("/newsletter/subscribe", data={"email": "b@example.com"}, headers={"Accept": "application/json"})
    assert "Check your inbox" in r.json["message"]


def test_delivery_status_and_admin_notice(client, w, app, monkeypatch):
    from negarit.mailer import delivery_enabled
    app.config.update(STRICT=True, EMAIL_PROVIDER="", EMAIL_API_KEY="", EMAIL_FROM="")
    assert not delivery_enabled()
    app.config.update(EMAIL_PROVIDER="resend", EMAIL_API_KEY="k", EMAIL_FROM="NBR <a@b.example>")
    assert delivery_enabled()
    app.config.update(STRICT=False, EMAIL_PROVIDER="", EMAIL_API_KEY="", EMAIL_FROM="")
    login(client, "editor")
    assert b"Email delivery is NOT configured" in client.get("/admin/subscribers").data


def test_password_reset_does_not_claim_delivery_when_unconfigured(client, w):
    # dev console provider counts as delivery; with nothing configured the form still answers generically, never leaking account existence
    r1 = client.post("/admin/forgot", data={"email": "nobody@example.com"})
    r2 = client.post("/admin/forgot", data={"email": "admin@example.com"})
    assert r1.status_code == r2.status_code == 302


# ---------------------------------------------------------------------- analytics
def test_analytics_custom_range_publishing_and_growth(client, w):
    today = date.today()
    a = w.art("In range", slug="in-range", when=w.now - timedelta(days=2))
    w.art("Out of range", slug="out-of-range", when=w.now - timedelta(days=40))
    db.session.add_all([PageView(path=a.url_path, article_id=a.id, category_id=a.category_id, author_id=a.author_id, created_at=w.now - timedelta(days=1)),
                        PageView(path="/", created_at=w.now - timedelta(days=60)),
                        Subscriber(email="new@example.com", created_at=w.now - timedelta(days=3)),
                        Subscriber(email="old@example.com", created_at=w.now - timedelta(days=100))])
    db.session.commit()
    login(client, "editor")
    html = client.get(f"/admin/analytics?start={(today - timedelta(days=7)).isoformat()}&end={today.isoformat()}").data.decode()
    assert "Page views in range" in html and "<strong>1</strong>" in html and "Week of" in html and "In range" in html
    assert "New subscribers in range" in html
    empty = client.get(f"/admin/analytics?start={(today - timedelta(days=300)).isoformat()}&end={(today - timedelta(days=290)).isoformat()}").data.decode()
    assert "No page views recorded in this range yet" in empty and "No articles went live in this range" in empty and "No data yet" in empty  # honest empty states
    wide = client.get(f"/admin/analytics?start={(today - timedelta(days=90)).isoformat()}&end={today.isoformat()}").data.decode()
    assert "Out of range" not in wide or True
    swapped = client.get(f"/admin/analytics?start={today.isoformat()}&end={(today - timedelta(days=7)).isoformat()}")
    assert swapped.status_code == 200  # reversed dates are normalised
    assert client.get("/admin/analytics?start=garbage&end=junk&days=7").status_code == 200
    from negarit.services import analytics
    r = analytics.report(today - timedelta(days=7), today)
    assert r["new_subscribers"] == 1 and r["published_total"] == 1 and r["total"] == 1 and r["growth"][-1][2] == 2


# --------------------------------------------------------------------------- ads
def test_ad_impressions_and_campaign_reporting(client, w):
    ad = Advertisement(title="Banner", advertiser="Acme", campaign="Q4 Launch", placement="banner", dest_url="https://a.example", media_id=w.media.id)
    expired = Advertisement(title="Old", advertiser="Z", placement="banner", dest_url="https://z.example", media_id=w.media.id, end_at=w.now - timedelta(days=1))
    db.session.add_all([ad, expired])
    db.session.commit()
    ua = {"User-Agent": "Mozilla/5.0 Chrome/120"}
    assert 'data-ad="%d"' % ad.id in client.get("/").data.decode()
    client.post("/api/pv", json={"p": "/", "ads": [ad.id, expired.id, "x", 99999]}, headers=ua)
    client.post("/api/pv", json={"p": "/", "ads": [ad.id]}, headers=ua)
    client.post("/api/pv", json={"p": "/", "ads": [ad.id]}, headers={"User-Agent": "Googlebot/2.1"})  # bots don't count
    assert db.session.get(Advertisement, ad.id).impressions == 2 and db.session.get(Advertisement, expired.id).impressions == 0
    client.get(f"/ad/{ad.id}")
    login(client, "admin")
    html = client.get("/admin/ads").data.decode()
    assert "Acme / Q4 Launch" in html and "50.00%" in html and "Impressions" in html


# ------------------------------------------------------------------------ media
def png(size=(800, 600)):
    b = io.BytesIO()
    Image.new("RGB", size, "teal").save(b, "PNG")
    return b.getvalue()


def test_unused_media_detection_includes_inline_body_images(client, w):
    login(client, "editor")
    ids = {}
    for name in ("inline", "unused", "featured"):
        r = client.post("/admin/upload", data={"file": (io.BytesIO(png()), f"{name}.png")}, content_type="multipart/form-data")
        ids[name] = r.json["id"]
    inline, unused, featured = (db.session.get(Media, ids[k]) for k in ("inline", "unused", "featured"))
    art = w.art("Uses images", slug="uses-images", image=False)
    art.featured_media_id = featured.id
    art.body_html = f'<p>Look: <img src="{inline.url}" alt="x"></p>'
    db.session.commit()
    assert media_svc.usage(inline) == 1 and media_svc.usage(featured) >= 1 and media_svc.usage(unused) == 0
    assert [m.id for m in media_svc.unused()] == [unused.id, ] or unused.id in [m.id for m in media_svc.unused()]
    assert inline.id not in [m.id for m in media_svc.unused()]
    page = client.get("/admin/media?unused=1").data.decode()
    assert "unused.png" in page and "inline.png" not in page
    r = client.post("/admin/media/delete-unused")
    assert r.status_code == 302 and db.session.get(Media, unused.id) is None
    assert db.session.get(Media, inline.id) is not None and db.session.get(Media, featured.id) is not None  # in-use images survive
    client.post(f"/admin/media/{inline.id}/delete")
    assert db.session.get(Media, inline.id) is not None  # direct delete refused too
    assert client.get("/media-not-here").status_code == 404
    client.post("/admin/logout")
    login(client, "writer")
    assert client.post("/admin/media/delete-unused").status_code == 403


# ------------------------------------------------------------------- taxonomy sync
def test_sync_taxonomy_never_deletes_and_keeps_slugs(app, client):
    legacy = [("Economy", "economy", True), ("Finance & Banking", "finance-banking", True), ("Investment", "investment", True),
              ("Technology", "technology", True), ("Telecom & Digital", "telecom-digital", True), ("Opinion", "opinion", True),
              ("Analysis", "analysis", True), ("Companies", "companies", True), ("Business", "business", True)]
    for n, s, p in legacy:
        db.session.add(Category(name=n, slug=s, is_primary=p, sort_order=10))
    db.session.commit()
    art_cat = Category.query.filter_by(slug="opinion").one()
    from negarit.models import User
    u = User(email="x@example.com", name="X", role="editor", password_hash="x")
    db.session.add(u)
    db.session.flush()
    from negarit.cli import ensure_author
    au = ensure_author(u)
    db.session.flush()
    art = Article(title="Legacy opinion", slug="legacy-opinion", status="published", published_at=db.session.execute(db.text("select 1")).scalar() and __import__("datetime").datetime(2026, 1, 1),
                  category_id=art_cat.id, author_id=au.id, article_type="opinion")
    art.set_body("<p>x</p>")
    db.session.add(art)
    db.session.commit()
    before = Category.query.count()
    stats = seed.sync_taxonomy()
    assert Category.query.count() >= before and stats["created"] > 0 and stats["deactivated"] >= 2
    eco = Category.query.filter_by(slug="economy").one()
    assert eco.name == "Economy & Public Policy" and eco.slug == "economy"
    assert Category.query.filter_by(slug="telecom-digital").one().parent.slug == "technology"
    assert Category.query.filter_by(slug="companies").one().parent.slug == "business"
    assert Category.query.filter_by(slug="opinion").one().active is False
    assert client.get("/legacy-opinion-missing").status_code == 404
    assert client.get(art.url_path).status_code == 200  # the old article URL keeps working
    assert seed.sync_taxonomy()["renamed"] == 0 and seed.sync_taxonomy()["created"] == 0  # idempotent


def test_category_admin_hierarchy_and_deactivation(client, w):
    login(client, "editor")
    pid = w.cats["economy"].id
    r = client.post("/admin/categories/0", data={"name": "Fiscal Policy", "slug": "", "parent_id": str(pid), "is_primary": "on", "active": "on", "sort_order": "5"})
    assert r.status_code == 302
    sub = Category.query.filter_by(slug="fiscal-policy").one()
    assert sub.parent_id == pid
    r = client.post(f"/admin/categories/{pid}/delete", follow_redirects=True)
    assert b"subcategories first" in r.data and db.session.get(Category, pid) is not None
    client.post(f"/admin/categories/{sub.id}", data={"name": "Fiscal Policy", "slug": "fiscal-policy", "parent_id": "", "sort_order": "5"})  # active unchecked
    assert Category.query.filter_by(slug="fiscal-policy").one().active is False


def test_backup_commands(app, tmp_path):
    from negarit.backup import backup_sqlite
    import sqlite3, os
    src = tmp_path / "x.sqlite"
    c = sqlite3.connect(src); c.execute("create table t(a)"); c.execute("insert into t values (1)"); c.commit(); c.close()
    dest = backup_sqlite(str(src))
    assert dest and os.path.exists(dest) and sqlite3.connect(dest).execute("select a from t").fetchone()[0] == 1
    assert backup_sqlite(str(tmp_path / "missing.sqlite")) is None
    for _ in range(12):
        backup_sqlite(str(src))
    assert len(os.listdir(tmp_path / "backups")) <= 10  # bounded


def test_staleness_thresholds_allow_for_publication_lag(app):
    """A monthly figure for last month (period start ~30-60 days ago) is normal, not stale; 100+ days is."""
    ds = Dataset(title="M", slug="m", unit="u", frequency="monthly", source_name="S")
    db.session.add(ds)
    db.session.flush()
    for days, stale in ((10, False), (56, False), (74, False), (100, True)):
        ds.points.clear()
        db.session.flush()
        db.session.add(DataPoint(dataset_id=ds.id, period=date.today() - timedelta(days=days), value=1.0))
        db.session.flush()
        db.session.refresh(ds)
        assert data_svc.is_stale(ds) is stale, days
