from functools import wraps

from flask import make_response


def cached(seconds: int = 60, swr: int = 300):
    """Let Vercel's CDN cache anonymous GET pages. Public pages never touch the
    session or embed CSRF tokens, so they are safe to share between visitors."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            resp = make_response(fn(*a, **kw))
            if resp.status_code == 200:
                resp.headers["Cache-Control"] = f"public, max-age=0, s-maxage={seconds}, stale-while-revalidate={swr}"
            return resp
        return wrapper
    return deco
