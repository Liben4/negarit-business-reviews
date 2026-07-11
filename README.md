# Negarit Business Review

A Flask + SQLite news site with an admin panel for posting articles.
Server-rendered HTML/CSS (Jinja2 templates), no JS framework, single SQLite
file for storage.

## Project structure

```
negarit-business-news/
├── app.py                        # routes, DB helpers, auth, CRUD
├── requirements.txt
├── Procfile                      # for Render/Railway/Heroku-style hosts
├── static/
│   ├── css/style.css
│   └── uploads/                  # article images land here (auto-created)
├── templates/
│   ├── base.html                 # masthead, nav, footer
│   ├── index.html                # homepage (featured + latest)
│   ├── category.html             # section listing, paginated
│   ├── article.html               # single article + related stories
│   ├── about.html / contact.html  # static pages — edit the placeholder copy
│   ├── 404.html
│   └── admin/                     # login, dashboard, article form, change password
└── negarit.db                     # created automatically on first run (not in git)
```

## Run it locally

```bash
cd negarit-business-news
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**.

The **first time** you run it, `negarit.db` is created automatically and
seeded with 5 sections and 6 sample articles (fictional companies — replace
or delete them from the dashboard). The terminal will print something like:

```
============================================================
  Negarit Business News — first-time setup complete
  Admin login URL : /admin/login
  Admin username  : admin
  Admin password  : Xk9pQ2mZnR8
  This password is shown ONCE. Save it now, then change it
  from the admin dashboard after logging in.
============================================================
```

**Copy that password immediately** — it's random per install and only
printed once. Log in at `/admin/login`, and change it right away from
**Change password** in the dashboard.

If you ever lose it and have terminal/DB access, you can reset it directly:

```bash
python3 -c "
import sqlite3
from werkzeug.security import generate_password_hash
conn = sqlite3.connect('negarit.db')
conn.execute('UPDATE admin_users SET password_hash=? WHERE username=?',
             (generate_password_hash('a-new-password'), 'admin'))
conn.commit()
"
```

## How content works

- **Sections** (Markets, Economy, Companies, Technology, Policy) are seeded
  once in `app.py` (`DEFAULT_CATEGORIES`). There's no "manage sections" admin
  screen yet — add/rename/remove rows in the `categories` table directly, or
  ask to have that screen built if you want it.
- **Articles** have a title, summary (used on cards), content, section,
  byline, optional image, and a **Published/Draft** status.
- **Draft** articles are only visible to you (while logged in) at their
  article URL, so you can preview before publishing. They never appear
  publicly or in listings.
- **Featured** marks the single article shown in the homepage hero. Marking
  a new one automatically un-features the previous one — only one at a time.
- The content field accepts basic HTML (`<p>`, `<strong>`, `<em>`,
  `<a href="">`, `<h3>`) so you can format articles without a rich-text
  editor. It renders as-is, so keep the admin password private.

## Customizing

- **Colors/fonts**: all design tokens are CSS variables at the top of
  `static/css/style.css` (`:root { --ink-navy, --brand-blue,
  --herald-orange, ... }`). Change them there and the whole site updates.
- **About/Contact copy**: currently placeholder text — edit directly in
  `templates/about.html` and `templates/contact.html`.
- **Site name**: `SITE_NAME` near the top of `app.py`.

## Deploying

Any host that can run a Python/WSGI app works. Two common paths:

### Option A — PaaS (Render, Railway, PythonAnywhere)
The included `Procfile` (`web: gunicorn app:app`) is ready for
Render/Railway-style platforms: push the folder to a GitHub repo, connect
the repo, set the `SECRET_KEY` environment variable (see below), and deploy.
PythonAnywhere doesn't use a Procfile — instead point its "Manual
configuration (WSGI)" setup at `app.app` following their Flask quickstart.

### Option B — Your own VPS (Ubuntu + gunicorn + nginx)
```bash
# on the server
sudo apt update && sudo apt install python3-venv nginx
cd /var/www && git clone <your-repo-url> negarit-business-news
cd negarit-business-news
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# run with gunicorn (add a systemd service to keep it alive on reboot)
gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
```
Then point nginx at `127.0.0.1:8000` as a reverse proxy, and get an SSL
certificate (e.g. `certbot --nginx`) so the admin login isn't sent over
plain HTTP.

### Before going live, either way
1. Set a persistent **`SECRET_KEY`** environment variable (random 32+ byte
   string). Without it, a fresh random key is generated on every restart,
   which silently logs every admin session out each time the process
   restarts.
   ```bash
   export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
   ```
2. Log in and change the default admin password if you haven't already.
3. Serve over **HTTPS** — the admin login form sends a password over the
   wire, so plain HTTP exposes it.
4. Back up `negarit.db` periodically — it's the entire database (one file,
   easy to copy/cron).
5. Don't commit `negarit.db` or `static/uploads/*` to git (already handled
   by `.gitignore`) — each environment should generate its own DB and admin
   password rather than sharing one.

## Possible next steps

Things intentionally left out to keep v1 simple, easy to add later on request:
category management UI, a rich-text/WYSIWYG editor for article content,
image resizing on upload, homepage pagination, search, and a contact form
that saves messages to the database instead of showing static contact info.
