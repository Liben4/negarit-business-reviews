#!/usr/bin/env python3
"""One-command local setup and run for Negarit Business Review. Works on Windows, macOS and Linux.

    python dev.py                first run: virtualenv + dependencies + database + sample content + admin, then starts the site
    python dev.py                later runs: applies any new migrations, then starts the site
    python dev.py --reset        DELETE and rebuild the local database (a backup copy is saved first)
    python dev.py --admin        create/reset the local admin with a new random password
    python dev.py --no-demo      with a first run/--reset: skip the sample articles
    python dev.py --port 5001    use another port
    python dev.py --no-run       set everything up but do not start the server

Local development only. For production see README.md (PostgreSQL + Vercel).
"""
import argparse
import os
import secrets
import sqlite3
import subprocess
import sys
import venv

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
PY = os.path.join(VENV, "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")
DB = os.path.join(ROOT, "instance", "nbr-dev.sqlite")
ADMIN_EMAIL = "admin@example.com"  # (.local/.test are rejected by the email validator)


def say(msg):
    print(f"\n==> {msg}", flush=True)


def run(args, env=None, check=True):
    return subprocess.run([PY, "-m", *args], cwd=ROOT, env={**os.environ, **(env or {})}, check=check)


def flask(*args, env=None, check=True):
    return run(["flask", "--app", "app", *args], env=env, check=check)


def ensure_venv():
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10 or newer is required (this is {sys.version.split()[0]}). Install it from python.org and retry.")
    if not os.path.exists(PY):
        say("Creating virtual environment (.venv)")
        venv.EnvBuilder(with_pip=True).create(VENV)
    stamp = os.path.join(VENV, ".deps-installed")
    req = os.path.join(ROOT, "requirements.txt")
    if not os.path.exists(stamp) or os.path.getmtime(stamp) < os.path.getmtime(req):
        say("Installing dependencies (first run takes a minute)")
        run(["pip", "install", "--quiet", "--disable-pip-version-check", "-r", "requirements.txt"])
        open(stamp, "w").close()


def has_users() -> bool:
    try:
        with sqlite3.connect(DB) as c:
            return c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0
    except sqlite3.Error:
        return False


def make_admin():
    password = secrets.token_urlsafe(12)
    flask("create-admin", env={"ADMIN_EMAIL": ADMIN_EMAIL, "ADMIN_PASSWORD": password})
    print("\n" + "=" * 60)
    print(f"  Admin sign-in:  http://localhost:{PORT}/admin/")
    print(f"  Email:          {ADMIN_EMAIL}")
    print(f"  Password:       {password}")
    print("  (shown once - run `python dev.py --admin` for a new one)")
    print("=" * 60, flush=True)


def main():
    global PORT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--admin", action="store_true")
    ap.add_argument("--no-demo", action="store_true")
    ap.add_argument("--no-run", action="store_true")
    ap.add_argument("--port", type=int, default=5000)
    a = ap.parse_args()
    PORT = a.port

    ensure_venv()
    first = not os.path.exists(DB)
    if first or a.reset:
        say("Building the local database" + (" with sample content" if not a.no_demo else ""))
        flask("reset-dev-db", "--yes", *([] if a.no_demo else ["--demo"]))  # --reset is your explicit approval; a backup is saved first
        make_admin()
    else:
        say("Checking the database (a backup is saved first if an upgrade is pending)")
        flask("backup-dev-db", "--if-pending", check=False)
        if flask("db", "upgrade", check=False).returncode != 0:
            sys.exit("\nThe local database is from an older version. Run:  python dev.py --reset   (deletes only the local dev database)")
        flask("seed-core", check=False)  # additive + idempotent: new starter pages/categories only, never overwrites your edits
        if a.admin or not has_users():  # also repairs a database left without any user
            make_admin()
    if a.no_run:
        return
    say(f"Starting the site at http://localhost:{PORT}  (Ctrl+C to stop)")
    flask("run", "--debug", "--port", str(PORT), check=False)


if __name__ == "__main__":
    PORT = 5000
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"\nA setup step failed (exit {e.returncode}). See the output above.")
    except KeyboardInterrupt:
        pass
