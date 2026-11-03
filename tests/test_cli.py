from negarit.models import Article, Author, Category, Page, User
from negarit.utils import utcnow
from datetime import timedelta
from negarit.extensions import db


def test_create_admin_command(app):
    runner = app.test_cli_runner()
    env = {"ADMIN_EMAIL": "Ops@Example.com", "ADMIN_PASSWORD": "a-long-test-password"}
    r = runner.invoke(args=["create-admin"], env=env)
    assert r.exit_code == 0 and "Created super admin ops@example.com" in r.output
    u = User.query.one()
    assert u.role == "super_admin" and u.check_password("a-long-test-password") and u.author is not None
    r = runner.invoke(args=["create-admin"], env={**env, "ADMIN_PASSWORD": "another-long-password"})
    assert "Updated existing user" in r.output and User.query.one().check_password("another-long-password")
    assert runner.invoke(args=["create-admin"], env={**env, "ADMIN_PASSWORD": "short"}).exit_code != 0
    assert runner.invoke(args=["create-admin", "--email", "nope"], env={"ADMIN_PASSWORD": "a-long-test-password"}).exit_code != 0
    assert User.query.count() == 1 and Author.query.count() == 1


def test_seed_and_publish_commands(app):
    runner = app.test_cli_runner()
    assert "Added 25 categories, 9 pages" in runner.invoke(args=["seed-core"]).output
    assert "Created 16 demo articles" in runner.invoke(args=["seed-demo", "--no-images"]).output
    a = Article.query.first()
    a.status, a.published_at = "scheduled", utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert "Published 1 article" in runner.invoke(args=["publish-scheduled"]).output
    assert "Removed 16 demo articles" in runner.invoke(args=["purge-demo"]).output
    assert Article.query.count() == 0 and Category.query.count() == 25 and Page.query.count() == 9
