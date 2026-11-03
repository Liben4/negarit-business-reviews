from flask import request
from flask_limiter import Limiter
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import MetaData

# Deterministic constraint names => reproducible Alembic migrations.
db = SQLAlchemy(metadata=MetaData(naming_convention={
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}))
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.session_protection = "strong"


def client_ip() -> str:
    """Real client IP. Forwarded headers are only trusted behind Vercel."""
    from flask import current_app
    if current_app.config.get("ON_VERCEL"):
        ip = request.headers.get("X-Real-IP") or (request.headers.get("X-Forwarded-For", "").split(",")[0].strip())
        if ip:
            return ip[:45]
    return (request.remote_addr or "0.0.0.0")[:45]


limiter = Limiter(key_func=client_ip, default_limits=[])
