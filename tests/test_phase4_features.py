"""Phase 4: newsletter, advertisements, comments, contact, media library, authors."""
import io
import time
from datetime import timedelta
from types import SimpleNamespace

from PIL import Image

from negarit.extensions import db
from negarit.models import Advertisement, Article, ArticleMedia, Comment, ContactMessage, Media, Subscriber

from conftest import login


# ------------------------------------------------------------------ newsletter
def sub(client, email, **extra):
    return client.post("/newsletter/subscribe", data={"email": email, **extra}, headers={"Accept": "application/json"})


def test_newsletter_double_opt_in(client, w):
    r = sub(client, "Reader@Example.com")
    assert r.status_code == 200 and r.json["ok"] and "confirm" in r.json["message"]
    s = Subscriber.query.one()
    assert s.email == "reader@example.com" and s.status == "pending"
    sub(client, "reader@example.com")
    assert Subscriber.query.count() == 1  # duplicate prevention
    assert client.get(f"/newsletter/confirm/{s.token}").status_code == 200
    assert db.session.get(Subscriber, s.id).status == "confirmed" and s.confirmed_at
    client.get(f"/newsletter/unsubscribe/{s.token}")
    assert db.session.get(Subscriber, s.id).status == "unsubscribed"
    sub(client, "reader@example.com")
    assert db.session.get(Subscriber, s.id).status == "pending"  # can re-subscribe, must reconfirm
    assert sub(client, "nope").status_code == 400
    assert client.get("/newsletter/confirm/bogus").status_code == 404


def test_newsletter_single_opt_in_and_honeypot(client, w):
    login(client, "super_admin")
    client.post("/admin/settings", data={"site_name": "NBR", "newsletter_double_opt_in": ""})
    client.post("/admin/logout")
    assert sub(client, "quick@example.com").json["message"].startswith("You're subscribed")
    assert Subscriber.query.one().status == "confirmed"
    sub(client, "bot@example.com", website="http://spam")
    assert Subscriber.query.filter_by(email="bot@example.com").first() is None


def test_newsletter_admin_search_export_remove(client, w):
    db.session.add_all([Subscriber(email="=cmd@example.com", status="confirmed"), Subscriber(email="ann@example.com", status="pending")])
    db.session.commit()
    login(client, "editor")
    assert b"ann@example.com" in client.get("/admin/subscribers?q=ann").data and b"=cmd" not in client.get("/admin/subscribers?q=ann").data
    assert b"ann@example.com" not in client.get("/admin/subscribers?status=confirmed").data
    csv = client.get("/admin/subscribers.csv")
    assert csv.mimetype == "text/csv" and "'=cmd@example.com" in csv.data.decode()
    sid = Subscriber.query.filter_by(email="ann@example.com").one().id
    client.post(f"/admin/subscribers/{sid}/delete")
    assert db.session.get(Subscriber, sid) is None


def test_newsletter_can_be_disabled_in_settings(client, w):
    w.art("Any")
    login(client, "super_admin")
    client.post("/admin/settings", data={"site_name": "NBR"})  # checkbox omitted = off
    client.post("/admin/logout")
    assert b'id="newsletter"' not in client.get("/").data


# ------------------------------------------------------------------------- ads
def make_ad(w, placement="banner", **kw):
    d = dict(title="Acme banner", advertiser="Acme", placement=placement, dest_url="https://acme.example/landing",
             media_id=w.media.id, active=True)
    d.update(kw)
    ad = Advertisement(**d)
    db.session.add(ad)
    db.session.commit()
    return ad


def test_ads_respect_dates_and_status(client, w):
    now = w.now
    make_ad(w, title="Running", start_at=now - timedelta(days=1), end_at=now + timedelta(days=1))
    make_ad(w, title="Expired", end_at=now - timedelta(hours=1))
    make_ad(w, title="Future", start_at=now + timedelta(hours=1))
    make_ad(w, title="Off", active=False)
    html = client.get("/").data.decode()
    assert 'alt="Running"' in html and 'alt="Expired"' not in html and 'alt="Future"' not in html and 'alt="Off"' not in html
    assert "Advertisement" in html and 'rel="sponsored noopener"' in html


def test_ad_placements_and_click_tracking(client, w):
    w.art("With ads")
    banner, side, inline = make_ad(w, "banner", title="B"), make_ad(w, "sidebar", title="S"), make_ad(w, "article", title="I")
    sponsored = make_ad(w, "sponsored", title="Sp", headline="Sponsored headline", media_id=None, advertiser="Globex")
    art = client.get("/economy/with-ads").data.decode()
    assert 'class="ad ad-banner"' in art and 'ad-sidebar' in art and 'ad-article' in art
    assert "Sponsored · Globex" in client.get("/economy").data.decode() and "Sponsored headline" in client.get("/economy").data.decode()
    r = client.get(f"/ad/{banner.id}")
    assert r.status_code == 302 and r.headers["Location"] == "https://acme.example/landing" and "noindex" in r.headers["X-Robots-Tag"]
    assert db.session.get(Advertisement, banner.id).clicks == 1
    expired = make_ad(w, end_at=w.now - timedelta(hours=1))
    client.get(f"/ad/{expired.id}")
    assert db.session.get(Advertisement, expired.id).clicks == 0  # redirect works, no click billed for inactive ads
    bad = make_ad(w, dest_url="javascript:alert(1)")
    assert client.get(f"/ad/{bad.id}").status_code == 404


def test_ads_can_be_switched_off(client, w):
    make_ad(w, title="Visible?")
    login(client, "super_admin")
    client.post("/admin/settings", data={"site_name": "NBR"})  # ads_enabled checkbox omitted = off
    client.post("/admin/logout")
    assert b'alt="Visible?"' not in client.get("/").data


def test_ad_admin_validation_and_permissions(client, w):
    login(client, "editor")
    assert client.get("/admin/ads").status_code == 403
    client.post("/admin/logout")
    login(client, "admin")
    base = {"title": "T", "advertiser": "A", "placement": "banner", "dest_url": "https://a.example", "active": "on"}
    assert b"Choose a banner image" in client.post("/admin/ads/0", data=base).data
    assert b"must start with https://" in client.post("/admin/ads/0", data={**base, "dest_url": "javascript:x", "media_id": w.media.id}).data
    assert b"end date must be after" in client.post("/admin/ads/0", data={**base, "media_id": w.media.id, "start_at": "2030-01-02T10:00", "end_at": "2030-01-01T10:00"}).data
    assert client.post("/admin/ads/0", data={**base, "media_id": w.media.id}).status_code == 302
    assert client.post("/admin/ads/0", data={**base, "placement": "sponsored", "headline": "Text only"}).status_code == 302
    assert Advertisement.query.count() == 2


# -------------------------------------------------------------------- comments
def test_comment_moderation_flow(client, w):
    a = w.art("Talk about it")
    r = client.post(f"/comments/{a.id}", data={"name": "Ana", "body": "Great analysis!"})
    assert r.status_code == 302 and "comment=pending" in r.headers["Location"]
    assert b"Great analysis" not in client.get(a.url_path).data  # pending: not public
    login(client, "editor")
    assert b"Great analysis" in client.get("/admin/comments").data
    cid = Comment.query.one().id
    client.post(f"/admin/comments/{cid}/approve")
    client.post("/admin/logout")
    assert b"Great analysis" in client.get(a.url_path).data
    login(client, "editor")
    client.post(f"/admin/comments/{cid}/spam")
    client.post("/admin/logout")
    assert b"Great analysis" not in client.get(a.url_path).data


def test_comment_spam_protection_and_toggle(client, w):
    a = w.art("Protected")
    client.post(f"/comments/{a.id}", data={"name": "Bot", "body": "buy now", "website": "http://spam"})
    client.post(f"/comments/{a.id}", data={"name": "", "body": "x"})
    assert Comment.query.count() == 0
    assert client.post("/comments/9999", data={"name": "A", "body": "hello"}).status_code == 404
    login(client, "super_admin")
    client.post("/admin/settings", data={"site_name": "NBR"})  # comments_enabled omitted = off
    client.post("/admin/logout")
    assert client.post(f"/comments/{a.id}", data={"name": "A", "body": "hello there"}).status_code == 404
    assert b'id="comments"' not in client.get(a.url_path).data


def test_comment_html_is_escaped(client, w):
    a = w.art("Escape")
    db.session.add(Comment(article_id=a.id, name="<b>x</b>", body="<script>alert(1)</script>", status="approved"))
    db.session.commit()
    html = client.get(a.url_path).data.decode()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


# --------------------------------------------------------------------- contact
def test_contact_form_flow_and_spam_traps(client, w, monkeypatch):
    page = client.get("/contact")
    assert page.status_code == 200 and "no-store" in page.headers["Cache-Control"]
    for label in ("General Inquiry", "News Tip", "Advertising", "Partnership", "Careers", "Editorial"):
        assert label.encode() in page.data
    import re
    ts = re.search(r'name="ts" value="([^"]+)"', page.data.decode()).group(1)
    good = {"name": "Sam", "email": "sam@example.com", "subject": "Tip", "message": "Something newsworthy happened.",
            "inquiry_type": "tip", "ts": ts}
    client.post("/contact", data=good)  # instantly → looks like a bot: silently dropped
    assert ContactMessage.query.count() == 0
    real = time.time()
    monkeypatch.setattr("negarit.views.public.time", SimpleNamespace(time=lambda: real + 30))
    assert client.post("/contact", data={**good, "website": "http://spam"}).status_code == 200 and ContactMessage.query.count() == 0
    assert b"has been received" in client.post("/contact", data=good).data
    m = ContactMessage.query.one()
    assert m.inquiry_type == "tip" and m.status == "new"
    bad = client.post("/contact", data={**good, "email": "not-an-email"})
    assert bad.status_code == 400 and b"correct the highlighted" in bad.data
    assert client.post("/contact", data={**good, "ts": "forged"}).status_code == 200 and ContactMessage.query.count() == 1


def test_contact_messages_admin(client, w):
    m = ContactMessage(name="A", email="a@example.com", subject="Hello <b>", message="Body <script>", inquiry_type="general")
    db.session.add(m)
    db.session.commit()
    login(client, "editor")
    html = client.get("/admin/messages").data.decode()
    assert "Hello &lt;b&gt;" in html and "<script>" not in html
    client.post(f"/admin/messages/{m.id}/read")
    assert db.session.get(ContactMessage, m.id).status == "read"
    client.post(f"/admin/messages/{m.id}/delete")
    assert ContactMessage.query.count() == 0


# ----------------------------------------------------------------------- media
def png(size=(2000, 1000), color="navy"):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def upload(client, data, name="Photo One.png"):
    return client.post("/admin/upload", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")


def test_upload_creates_optimised_webp_variants(client, w, app):
    login(client, "editor")
    r = upload(client, png())
    assert r.status_code == 200
    m = db.session.get(Media, r.json["id"])
    assert sorted(m.variant_map) == [480, 960, 1600] and m.width == 1600 and m.height == 800
    assert m.content_type == "image/webp" and m.url.endswith(".webp") and "photo-one" in m.url and "srcset" not in m.url
    assert m.srcset.count("w,") == 2
    import os
    path = os.path.join(app.instance_path, "uploads", m.keys[-1])
    assert Image.open(path).format == "WEBP" and os.path.getsize(path) < len(png())
    assert client.get(m.url).status_code == 200
    small = upload(client, png((300, 200)), "tiny.png")
    assert list(db.session.get(Media, small.json["id"]).variant_map) == [300]  # never upscaled


def test_upload_strips_metadata_and_validates(client, w):
    login(client, "editor")
    exif = io.BytesIO()
    im = Image.new("RGB", (100, 100), "red")
    ex = Image.Exif()
    ex[0x010F] = "SecretCamera"
    im.save(exif, "JPEG", exif=ex)
    m = db.session.get(Media, upload(client, exif.getvalue(), "cam.jpg").json["id"])
    from negarit.storage import get_storage
    import os
    from flask import current_app
    with open(os.path.join(current_app.instance_path, "uploads", m.keys[-1]), "rb") as f:
        assert b"SecretCamera" not in f.read()
    assert upload(client, b"<?php system($_GET[0]); ?>", "shell.png").status_code == 400
    assert upload(client, b"MZ\x90\x00\x03", "a.exe").status_code == 400
    assert upload(client, b"", "empty.png").status_code == 400
    assert upload(client, b"0" * (5 * 1024 * 1024), "big.png").status_code == 413
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert upload(client, svg, "x.svg").status_code == 400  # SVG (script-capable) is not an allowed format


def test_animated_gif_is_preserved(client, w):
    login(client, "editor")
    b = io.BytesIO()
    a, c = Image.new("RGB", (40, 40), "red"), Image.new("RGB", (40, 40), "blue")
    a.save(b, "GIF", save_all=True, append_images=[c], duration=100, loop=0)
    m = db.session.get(Media, upload(client, b.getvalue(), "anim.gif").json["id"])
    assert m.content_type == "image/gif" and m.url.endswith(".gif")


def test_media_library_details_picker_and_delete_rules(client, w):
    login(client, "editor")
    mid = upload(client, png((800, 600)), "one.png").json["id"]
    assert client.get("/admin/media").status_code == 200 and b"No alt text" in client.get("/admin/media").data
    client.post(f"/admin/media/{mid}", data={"alt_text": "A chart", "caption": "Cap", "credit": "NBR"})
    m = db.session.get(Media, mid)
    assert (m.alt_text, m.caption, m.credit) == ("A chart", "Cap", "NBR")
    assert b"one.png" in client.get("/admin/media?q=chart").data and b"one.png" not in client.get("/admin/media?q=zzz").data
    pick = client.get("/admin/media/picker").json
    assert pick["items"][0]["id"] == mid and "thumb" in pick["items"][0]
    art = w.art("Uses image", image=False)
    art.featured_media_id = mid
    db.session.commit()
    r = client.post(f"/admin/media/{mid}/delete", follow_redirects=True)
    assert b"in use" in r.data and db.session.get(Media, mid) is not None
    art.featured_media_id = None
    db.session.commit()
    client.post(f"/admin/media/{mid}/delete")
    assert db.session.get(Media, mid) is None


def test_featured_image_and_gallery_render_publicly(client, w):
    a = w.art("Gallery story")
    db.session.add(ArticleMedia(article_id=a.id, media_id=w.media.id, position=0))
    db.session.commit()
    html = client.get(a.url_path).data.decode()
    assert 'aria-label="Image gallery"' in html and "Cover caption" in html


# --------------------------------------------------------------------- authors
def test_author_profile_page_fields(client, w):
    au = w.authors["editor"]
    au.position, au.bio, au.twitter, au.image_id = "Economics Editor", "Covers macro.", "https://x.com/e", w.media.id
    db.session.commit()
    w.art("Bylined", author="editor")
    html = client.get(f"/author/{au.slug}").data.decode()
    assert "Economics Editor" in html and "Covers macro." in html and 'href="https://x.com/e"' in html and "Bylined" in html
    assert '"@type":"Person"' in html.replace(" ", "") or "Person" in html
    assert "Economics Editor" in client.get("/economy/bylined").data.decode()
