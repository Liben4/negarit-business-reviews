import os

import click
from email_validator import EmailNotValidError, validate_email

from .extensions import db
from .models import Article, Author, User
from .utils import unique_slug, utcnow


def publish_due() -> int:
    """Flip due 'scheduled' articles to 'published'. Idempotent housekeeping."""
    n = (Article.query.filter(Article.status == "scheduled", Article.published_at <= utcnow())
         .update({"status": "published"}, synchronize_session=False))
    db.session.commit()
    return n


def ensure_author(user: User) -> Author:
    if user.author:
        return user.author
    a = Author(name=user.name, slug=unique_slug(Author, user.name), user_id=user.id)
    db.session.add(a)
    return a


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--email", default=lambda: os.environ.get("ADMIN_EMAIL", ""))
    @click.option("--name", default="Administrator")
    def create_admin(email, name):
        """Create/reset the Super Admin. Reads ADMIN_EMAIL / ADMIN_PASSWORD (or prompts)."""
        try:
            email = validate_email(email or click.prompt("Admin email"), check_deliverability=False).normalized.lower()
        except EmailNotValidError as e:
            raise click.ClickException(str(e))
        pw = os.environ.get("ADMIN_PASSWORD") or click.prompt("Admin password", hide_input=True, confirmation_prompt=True)
        if len(pw) < 12:
            raise click.ClickException("Password must be at least 12 characters.")
        user = User.query.filter_by(email=email).first()
        if user:
            user.role, user.active = "super_admin", True
            click.echo(f"Updated existing user {email} (role=super_admin, password reset).")
        else:
            user = User(email=email, name=name, role="super_admin")
            click.echo(f"Created super admin {email}.")
        user.set_password(pw)  # before the first flush: password_hash is NOT NULL
        db.session.add(user)
        db.session.flush()
        ensure_author(user)
        db.session.commit()

    def _sqlite_path():
        uri = app.config["SQLALCHEMY_DATABASE_URI"]
        if app.config["STRICT"] or not uri.startswith("sqlite:///"):
            raise click.ClickException("Refusing: this only works on a local SQLite development database.")
        return uri[len("sqlite:///"):]

    @app.cli.command("backup-dev-db")
    @click.option("--if-pending", is_flag=True, help="Only back up when migrations are waiting to be applied.")
    def backup_dev_db(if_pending):
        """Copy the LOCAL SQLite database to instance/backups/ (keeps the newest 10)."""
        from . import devcheck
        from .backup import backup_sqlite
        path = _sqlite_path()
        if if_pending and devcheck.schema_status(os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")) is None:
            click.echo("Database is up to date; no backup needed.")
            return
        dest = backup_sqlite(path)
        click.echo(f"Backed up to {dest}" if dest else "No database file yet; nothing to back up.")

    @app.cli.command("sync-taxonomy")
    def sync_taxonomy_cmd():
        """Align an existing database's categories with the current set (adds/renames/re-parents; never deletes)."""
        from .services import seed
        click.echo("Taxonomy synced: " + ", ".join(f"{k}={v}" for k, v in seed.sync_taxonomy().items()))

    @app.cli.command("reset-dev-db")
    @click.option("--demo", is_flag=True, help="Also load the labelled sample content.")
    @click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
    def reset_dev_db(demo, yes):
        """DELETE and rebuild the LOCAL SQLite development database (refuses anything else). A backup is taken first."""
        from flask_migrate import upgrade
        from .backup import backup_sqlite
        path = _sqlite_path()
        if os.path.exists(path) and not yes:
            click.confirm(f"This DELETES {path} and rebuilds it (a backup copy is saved first). Continue?", abort=True)
        saved = backup_sqlite(path)
        if saved:
            click.echo(f"Backup saved: {saved}")
        db.session.remove()
        db.engine.dispose()  # release the file (required on Windows)
        if os.path.exists(path):
            os.remove(path)
        upgrade()
        from .services import seed
        c, p = seed.seed_core()
        click.echo(f"Rebuilt {path}: schema at head, {c} categories, {p} pages.")
        if demo:
            click.echo(f"Created {seed.seed_demo(images=True)} sample articles.")
        click.echo("Next: flask create-admin")

    @app.cli.command("seed-core")
    def seed_core():
        """Categories and legal/static pages (idempotent; never overwrites edits)."""
        from .services import seed
        c, p = seed.seed_core()
        click.echo(f"Added {c} categories, {p} pages.")

    @app.cli.command("seed-demo")
    @click.option("--no-images", is_flag=True, help="Skip placeholder images (no storage needed).")
    def seed_demo(no_images):
        """Clearly-labelled SAMPLE articles for development/testing."""
        from .services import seed
        seed.seed_core()
        n = seed.seed_demo(images=not no_images)
        click.echo(f"Created {n} demo articles (flagged is_demo, noindex, excluded from sitemap/RSS).")

    @app.cli.command("purge-demo")
    def purge_demo():
        """Delete all demo content."""
        from .services import seed
        click.echo(f"Removed {seed.purge_demo()} demo articles.")

    @app.cli.command("publish-scheduled")
    def publish_scheduled():
        """Manually run the scheduled-publishing sweep."""
        click.echo(f"Published {publish_due()} article(s).")
