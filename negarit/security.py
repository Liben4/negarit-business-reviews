import hmac
from datetime import timedelta
from functools import wraps

from flask import abort, current_app, request
from flask_login import current_user, login_required

from .extensions import client_ip, db
from .models import LoginAttempt
from .utils import utcnow

WINDOW = timedelta(minutes=15)
MAX_PER_IP = 20
MAX_PER_EMAIL = 5


def permission_required(perm: str):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if not current_user.can(perm):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def login_locked(email: str) -> bool:
    since = utcnow() - WINDOW
    ip_fails = LoginAttempt.query.filter(LoginAttempt.ip == client_ip(), LoginAttempt.success.is_(False),
                                         LoginAttempt.created_at >= since).count()
    mail_fails = LoginAttempt.query.filter(LoginAttempt.email == email, LoginAttempt.success.is_(False),
                                           LoginAttempt.created_at >= since).count() if email else 0
    return ip_fails >= MAX_PER_IP or mail_fails >= MAX_PER_EMAIL


def record_login(email: str, ok: bool):
    db.session.add(LoginAttempt(ip=client_ip(), email=email[:255], success=ok))
    # opportunistic cleanup keeps the table small
    db.session.query(LoginAttempt).filter(LoginAttempt.created_at < utcnow() - timedelta(days=2)).delete()
    db.session.commit()


def cron_authorized() -> bool:
    secret = current_app.config["CRON_SECRET"]
    if len(secret) < 16:
        return False  # fail closed when unset/weak
    sent = request.headers.get("Authorization", "")
    return hmac.compare_digest(sent.encode(), f"Bearer {secret}".encode())
