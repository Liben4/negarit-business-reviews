"""Local-dev guard: detect an out-of-date database and explain the fix instead of a raw SQL error."""
from alembic.script import ScriptDirectory
from markupsafe import escape
from sqlalchemy import inspect, text

from .extensions import db

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Database needs updating</title><link rel="stylesheet" href="/static/css/site.css"></head>
<body><main class="wrap"><section class="status-page"><p class="eyebrow">Local development</p>
<h1 class="page-title">Your database needs updating</h1><p>{problem}.</p>
<p>In a terminal, in the project folder (virtual environment active), run:</p>
<pre class="cmd">flask db upgrade</pre>
<p>If that fails with <em>“already exists”</em> or <em>“Can't locate revision”</em>, the file is from an older version of this project.
Reset the <strong>local</strong> development database (this deletes <code>{dbfile}</code>):</p>
<pre class="cmd">python dev.py --reset</pre>
<p class="muted small">or, manually: <code>flask reset-dev-db --demo</code> then <code>flask create-admin</code></p>
<p class="muted small">This message only appears in local development, never on Vercel.</p></section></main></body></html>"""


def schema_status(migrations_dir: str) -> str | None:
    """None when the database is at the migration head; otherwise a short human-readable reason."""
    head = ScriptDirectory(migrations_dir).get_current_head()
    if not inspect(db.engine).has_table("alembic_version"):
        return "This database has no migration history (it is empty or was not created with <code>flask db upgrade</code>)"
    with db.engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if current != head:
        return f"This database is at schema version <code>{escape(current)}</code> but the code expects <code>{escape(head)}</code>"
    return None


def response(app, problem: str):
    import os
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    dbfile = escape(os.path.join("instance", os.path.basename(uri))) if uri.startswith("sqlite:///") else "the database"
    resp = app.make_response(PAGE.format(problem=problem, dbfile=dbfile))
    resp.status_code = 503
    resp.headers["Cache-Control"] = "no-store"
    return resp
