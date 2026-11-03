"""Phase 1: architecture, permissions, auth, security headers, settings, error pages."""
import pytest

from negarit import create_app
from negarit.config import load_config
from negarit.extensions import db
from negarit.models import ActivityLog, LoginAttempt
from negarit.permissions import ROLES, assignable_roles, has_permission
from negarit.services import settings

from conftest import PW, login


def test_permission_matrix():
    assert all(has_permission("super_admin", p) for p in ("users.manage", "article.publish", "anything"))
    assert has_permission("editor", "article.publish") and not has_permission("editor", "*")
    assert has_permission("writer", "article.submit") and not has_permission("writer", "article.publish")
    assert has_permission("contributor", "article.submit") and not has_permission("contributor", "media.browse")
    assert not has_permission("contributor", "article.delete_own_draft") and has_permission("writer", "article.delete_own_draft")
    assert "super_admin" not in assignable_roles("admin") and "super_admin" in assignable_roles("super_admin")


def test_strict_config_rules():
    with pytest.raises(RuntimeError):
        load_config({"VERCEL": "1"})
    with pytest.raises(RuntimeError):
        load_config({"VERCEL": "1", "SECRET_KEY": "s" * 40, "DATABASE_URL": "sqlite:///x.db"})
    cfg = load_config({"VERCEL": "1", "VERCEL_ENV": "production", "SECRET_KEY": "s" * 40,
                       "DATABASE_URL": "postgres://u:p@host/db?sslmode=require"})
    assert cfg["SQLALCHEMY_DATABASE_URI"].startswith("postgresql+psycopg://")
    assert cfg["SESSION_COOKIE_SECURE"] and cfg["IS_PRODUCTION_DEPLOY"] is True


def test_admin_requires_login_everywhere(app, client):
    urls = [r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/admin/") and "GET" in r.methods
            and not any(x in r.rule for x in ("login", "forgot", "reset", "<"))]
    assert len(urls) > 15
    for u in urls:
        r = client.get(u)
        assert r.status_code == 302 and "/admin/login" in r.headers["Location"], u


def test_login_flow_and_activity_log(client, w):
    assert login(client, pw="wrong-password").status_code == 401
    assert login(client).status_code == 302
    r = client.get("/admin/")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store, private" and "noindex" in r.headers["X-Robots-Tag"]
    client.post("/admin/logout")
    assert client.get("/admin/").status_code == 302
    assert {"login", "logout"} <= {l.action for l in ActivityLog.query.all()}
    assert w.users["admin"].last_login_at is not None


def test_disabled_user_cannot_login(client, w):
    w.users["writer"].active = False
    db.session.commit()
    assert login(client, "writer").status_code == 401


def test_open_redirect_blocked(client, w):
    r = client.post("/admin/login?next=//evil.example", data={"email": "admin@example.com", "password": PW})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_login_lockout(client, w):
    for _ in range(5):
        login(client, pw="bad-bad-bad-bad")
    assert login(client).status_code == 429
    assert LoginAttempt.query.count() >= 5


def test_csrf_enforced_when_enabled(w):
    strict = create_app({"SQLALCHEMY_DATABASE_URI": db.engine.url.render_as_string(hide_password=False),
                         "WTF_CSRF_ENABLED": True, "RATELIMIT_ENABLED": False, "SCHEMA_GUARD": False})
    with strict.app_context():
        assert strict.test_client().post("/admin/login", data={"email": "admin@example.com", "password": PW}).status_code == 400


def test_password_reset_single_use_and_no_enumeration(client, w, monkeypatch):
    sent = {}
    import negarit.views.auth as auth
    monkeypatch.setattr(auth, "send_email", lambda to, subj, html, text: sent.update(to=to, text=text) or True)
    assert client.post("/admin/forgot", data={"email": "nobody@example.com"}).status_code == 302 and not sent
    client.post("/admin/forgot", data={"email": "admin@example.com"})
    token = sent["text"].split("/admin/reset/")[1].split(" ")[0].split("\n")[0].strip()
    new = "a-brand-new-password"
    assert client.post(f"/admin/reset/{token}", data={"password": new, "password2": new}).status_code == 302
    assert login(client, pw=new).status_code == 302
    client.post("/admin/logout")
    assert "forgot" in client.get(f"/admin/reset/{token}").headers["Location"]  # token dies with the old hash


def test_security_headers_health_and_error_pages(client, w):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"] and "googletagmanager" not in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/api/health").json == {"status": "ok"}
    nf = client.get("/no-such-thing")
    assert nf.status_code == 404 and b"Page not found" in nf.data and b"Negarit" in nf.data and b"Traceback" not in nf.data
    login(client, "writer")
    forbidden = client.get("/admin/users")
    assert forbidden.status_code == 403 and b"Access denied" in forbidden.data


def test_maintenance_mode(client, w):
    login(client, "super_admin")
    client.post("/admin/settings", data={"site_name": "NBR", "maintenance_mode": "on", "maintenance_message": "Back soon!"})
    client.post("/admin/logout")
    r = client.get("/")
    assert r.status_code == 503 and b"Back soon!" in r.data and r.headers["Retry-After"] == "3600"
    assert client.get("/api/health").status_code == 200 and client.get("/admin/login").status_code == 200
    login(client, "writer")
    assert client.get("/").status_code == 200  # signed-in staff can preview the live site


def test_settings_roundtrip_theme_and_validation(client, w):
    login(client, "super_admin")
    bad = client.post("/admin/settings", data={"site_name": "Should Not Save", "social_x": "javascript:alert(1)"})
    assert bad.status_code == 200 and b"must start with https://" in bad.data  # rejected, nothing saved
    assert settings.get("site_name") != "Should Not Save"
    r = client.post("/admin/settings", data={"site_name": "Negarit Test", "tagline": "Tag", "brand_accent": "#123456",
                                             "brand_accent_dark": "not-a-colour", "newsletter_enabled": "on"})
    assert r.status_code == 302
    assert settings.get("site_name") == "Negarit Test" and settings.get("social_x") == ""
    css = client.get("/theme.css")
    assert b"#123456" in css.data and b"not-a-colour" not in css.data and css.mimetype == "text/css"
    assert client.get("/admin/settings").status_code == 200


def test_settings_page_forbidden_for_editor(client, w):
    login(client, "editor")
    assert client.get("/admin/settings").status_code == 403 and client.post("/admin/settings", data={}).status_code == 403
