from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..extensions import db, limiter
from ..mailer import send_email
from ..models import User
from ..security import login_locked, record_login
from ..services import activity, settings
from ..utils import is_safe_next, utcnow

bp = Blueprint("auth", __name__, url_prefix="/admin")
DUMMY_HASH = "scrypt:32768:8:1$x$" + "0" * 128  # constant-time-ish path for unknown users
MIN_PW = 12


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def make_reset_token(user: User) -> str:
    # Bind to the current hash: the token dies as soon as the password changes.
    return _serializer().dumps({"id": user.id, "h": user.password_hash[-12:]})


def _user_from_token(token: str):
    try:
        data = _serializer().loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        return None
    u = db.session.get(User, data.get("id"))
    return u if u and u.active and u.password_hash[-12:] == data.get("h") else None


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20/minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:255]
        password = request.form.get("password") or ""
        if login_locked(email):
            flash("Too many failed attempts. Try again in 15 minutes.", "error")
            return render_template("auth/login.html"), 429
        user = User.query.filter_by(email=email).first()
        ok = bool(user and user.active and user.check_password(password))
        if not user:
            from werkzeug.security import check_password_hash
            check_password_hash(DUMMY_HASH, password)  # equalise timing
        record_login(email, ok)
        if ok:
            session.clear()
            login_user(user, remember=False)
            session.permanent = True
            user.last_login_at = utcnow()
            activity.log("login", "user", user.id, "Signed in", user=user)
            db.session.commit()
            nxt = request.args.get("next")
            return redirect(nxt if is_safe_next(nxt) else url_for("admin.dashboard"))
        flash("Invalid email or password.", "error")
        return render_template("auth/login.html"), 401
    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    activity.log("logout", "user", current_user.id, "Signed out")
    db.session.commit()
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/forgot", methods=["GET", "POST"])
@limiter.limit("5/hour", methods=["POST"])
def forgot():
    if request.method == "POST":
        try:
            email = validate_email(request.form.get("email", ""), check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            email = ""
        user = User.query.filter_by(email=email, active=True).first() if email else None
        if user:
            link = f"{current_app.config['SITE_URL']}/admin/reset/{make_reset_token(user)}"
            send_email(user.email, "Reset your password",
                       render_template("email/reset.html", link=link, name=settings.get("site_name")),
                       f"Reset your password (valid for 1 hour): {link}")
        flash("If that account exists, a reset link has been sent.", "ok")  # identical either way
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
@limiter.limit("10/hour", methods=["POST"])
def reset(token):
    user = _user_from_token(token)
    if not user:
        flash("That reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot"))
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw) < MIN_PW:
            flash(f"Password must be at least {MIN_PW} characters.", "error")
        elif pw != pw2:
            flash("Passwords do not match.", "error")
        else:
            user.set_password(pw)
            activity.log("password_reset", "user", user.id, "Password reset via emailed link", user=user)
            db.session.commit()
            flash("Password updated. Please sign in.", "ok")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html", token=token)
