"""Environment-driven configuration. No secrets live in code."""
import os
import secrets
from datetime import timedelta


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalize_db_url(url: str) -> str:
    """Accept postgres:// / postgresql:// and force the psycopg3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def load_config(env=None) -> dict:
    env = os.environ if env is None else env
    on_vercel = bool(env.get("VERCEL"))
    vercel_env = env.get("VERCEL_ENV", "")  # production | preview | development
    strict = on_vercel or env.get("APP_ENV") == "production"
    is_prod_deploy = vercel_env == "production" or env.get("APP_ENV") == "production"

    secret = env.get("SECRET_KEY", "")
    db_url = env.get("DATABASE_URL", "")

    if strict:
        if len(secret) < 32:
            raise RuntimeError("SECRET_KEY must be set (>= 32 chars) in production/preview.")
        if not db_url:
            raise RuntimeError("DATABASE_URL must be set (PostgreSQL) in production/preview.")
        if db_url.startswith("sqlite"):
            raise RuntimeError("SQLite is not allowed in production. Use PostgreSQL.")
    else:
        secret = secret or secrets.token_urlsafe(32)  # per-process dev fallback
        db_url = db_url or "sqlite:///" + os.path.join(PROJECT_ROOT, "instance", "nbr-dev.sqlite")

    db_url = normalize_db_url(db_url)
    engine_opts = {"pool_pre_ping": True}
    if not db_url.startswith("sqlite"):
        # Serverless: don't hold connections between invocations; rely on the
        # provider's pooler (use the pooled connection string).
        from sqlalchemy.pool import NullPool
        engine_opts["poolclass"] = NullPool

    site_url = env.get("SITE_URL", "https://negaritbusinessreview.com" if strict else "http://localhost:5000")

    return {
        "SECRET_KEY": secret,
        "STRICT": strict,
        "ON_VERCEL": on_vercel,
        "IS_PRODUCTION_DEPLOY": is_prod_deploy,
        "SQLALCHEMY_DATABASE_URI": db_url,
        "SQLALCHEMY_ENGINE_OPTIONS": engine_opts,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SITE_URL": site_url.rstrip("/"),
        "SITE_NAME": env.get("SITE_NAME", "Negarit Business Review"),
        "SITE_TAGLINE": env.get("SITE_TAGLINE", "Business, markets and policy from Ethiopia and the region."),
        "SITE_TIMEZONE": env.get("SITE_TIMEZONE", "Africa/Addis_Ababa"),
        "ASSET_VERSION": (env.get("VERCEL_GIT_COMMIT_SHA") or "dev")[:8],
        # Vercel Functions cap request bodies at ~4.5 MB.
        "MAX_CONTENT_LENGTH": 4 * 1024 * 1024,
        "SESSION_COOKIE_NAME": "nbr_session",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": strict,
        "REMEMBER_COOKIE_HTTPONLY": True,
        "REMEMBER_COOKIE_SECURE": strict,
        "REMEMBER_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=12),
        "WTF_CSRF_TIME_LIMIT": 60 * 60 * 8,
        "PREFERRED_URL_SCHEME": "https" if strict else "http",
        "STORAGE_PROVIDER": env.get("STORAGE_PROVIDER", "" if strict else "local"),
        "STORAGE_API_KEY": env.get("STORAGE_API_KEY", ""),
        "STORAGE_API_SECRET": env.get("STORAGE_API_SECRET", ""),
        "STORAGE_CLOUD_NAME": env.get("STORAGE_CLOUD_NAME", ""),
        "STORAGE_REGION": env.get("STORAGE_REGION", ""),
        "STORAGE_ENDPOINT_URL": env.get("STORAGE_ENDPOINT_URL", ""),
        "STORAGE_PUBLIC_BASE_URL": env.get("STORAGE_PUBLIC_BASE_URL", "").rstrip("/"),
        "EMAIL_PROVIDER": env.get("EMAIL_PROVIDER", "" if strict else "console"),
        "EMAIL_API_KEY": env.get("EMAIL_API_KEY", ""),
        "EMAIL_FROM": env.get("EMAIL_FROM", ""),
        "CRON_SECRET": env.get("CRON_SECRET", ""),
        "RATELIMIT_STORAGE_URI": env.get("RATELIMIT_STORAGE_URI") or "memory://",
        "RATELIMIT_HEADERS_ENABLED": False,
        "PER_PAGE": _int("PER_PAGE", 12),
        "SCHEMA_GUARD": not strict,  # local dev only: explain a stale/missing schema instead of a raw SQL error
    }
