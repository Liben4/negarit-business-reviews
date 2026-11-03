# Negarit Business Review (NBR)

*Navigating Business Dynamics* — a business-media platform: news + analysis + data, with a full editorial CMS.
Flask on Vercel (serverless) · PostgreSQL · Cloudinary/S3 media · transactional email API · Vercel Cron.

```
Local dev → Git → GitHub → Vercel ─┬─ PostgreSQL (Neon / Supabase / any)   DATABASE_URL
                                   ├─ Cloudinary or S3-compatible storage  STORAGE_*
                                   ├─ Resend / Postmark                    EMAIL_*
                                   └─ Vercel Cron → /cron/publish-scheduled CRON_SECRET
                      → https://negaritbusinessreview.com
```
Architecture, DB design, UI structure and extension points (NBR Data / NBR Premium): [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 1. Features

**Public site:** navy / gold / cream editorial design (light + dark) · main nav **Home · News · Analysis · Opinion · Data** plus a
**Sections** strip (13 topic categories with subcategories; extras under *More*) · editor-controlled homepage: lead story, breaking
banner, top stories, editor's picks, latest, per-category blocks, analysis & opinion, economic data & reports, most read, newsletter,
ad slots · article pages at `/<category>/<slug>` with deck, byline, published/updated dates, reading time, hero image + caption +
credit, gallery, sources & references, tags, share, **print layout**, author box, related, previous/next in section, most-read,
moderated comments · content types News, Analysis, Opinion, Explainer, Interview, Data Report, Feature and **Sponsored Content**
(opinion, analysis and sponsored pieces carry a visible label; sponsored shows the sponsor) · site-wide search (articles, headlines,
authors, categories, tags; filters; sorting) · author profiles with expertise and socials · contact form (incl. *Write for Us* and
*Correction Request*) · CMS-managed pages: About, Contact, Editorial Policy, Corrections Policy, Privacy, Terms, Cookie Policy,
Advertise With Us, Write for Us, Advertising Policy · 404/403/500/maintenance pages.

**Data section (`/data`):** staff-entered indicators (inflation, exchange rates, banking, trade…) shown with value, unit, "as of"
period, change vs previous, **source**, accessible SVG line chart, data table, **CSV download** and a *Not recent* warning when a
series is older than its normal publication rhythm; downloadable reports (links to files you host); data-report articles. Nothing is
fetched or invented: every number is entered by staff, with its source, and a dataset cannot be published without one.

**CMS (`/admin`):** dashboard (real counts only) · **rich-text editor** (headings, lists, quotations, links, images from the media
library) with **autosave** and live save status · editorial workflow **Draft → Submitted for review → Approved → Scheduled / Published**
plus request-revisions, unpublish, **archive/restore**, **duplicate**, **revision history with restore**, **editorial notes** ·
**homepage manager** · categories (subcategories, deactivate) · tags · authors · media library (alt/caption/credit, WebP + responsive
sizes, **unused-image cleanup**) · pages · data indicators & reports · subscribers · comments · messages · advertisements (campaigns,
impressions, clicks, CTR) · analytics (date ranges, publishing activity, subscriber growth) · SEO · site settings · users · activity logs ·
top bar with notifications, article search and account menu.

| Role | Can do |
|---|---|
| **Super Administrator** | Everything, including users, roles, site settings and configuration |
| **Administrator** | Content, categories, authors, media, pages, ads, SEO, data, homepage, analytics, activity logs. **Not** users/roles/settings |
| **Editor** | Review, edit, approve, schedule, publish and archive any article; homepage sections; data; taxonomy, authors, media, subscribers, comments, messages, analytics |
| **Journalist / Author** | Create and edit own drafts, submit for review, delete own drafts, browse/upload media, add notes. Cannot publish |
| **Contributor** | Create own drafts and submit for review (upload only). Cannot publish, delete or browse the media library |

All permissions are enforced on the server for every route and action (see `negarit/permissions.py`).

**SEO / performance:** `robots.txt`, `sitemap.xml` (with images, datasets, type pages), `news-sitemap.xml`, `rss.xml`; canonical URLs;
Open Graph + X cards; NewsArticle / OpinionNewsArticle / AnalysisNewsArticle, Dataset, Organization, WebSite and BreadcrumbList
JSON-LD; `max-image-preview:large`; previews are always `noindex`. Self-hosted fonts, WebP + `srcset`, lazy loading, explicit image
dimensions, CDN-cacheable HTML. **Security:** scrypt hashing · CSRF on admin/auth forms · CSP with no inline script/style ·
secure cookies · HSTS · DB-backed login lockout + rate limits · upload validation by decoding · allow-list HTML sanitising of all
rich text and Markdown · honeypot + timing trap on public forms · audit log · single-use password-reset tokens · fail-closed config.

## 2. Local development

**Fastest way (Windows, macOS, Linux; needs Python 3.10+):**

```bash
python dev.py          # Windows: py dev.py
```
First run creates `.venv`, installs dependencies, builds a fresh local database (`instance/nbr-dev.sqlite`) with the categories,
legal pages and 16 labelled sample articles, creates an admin (the password is printed once), and starts the site at
http://localhost:5000 (admin at `/admin/`). Later runs just apply new migrations and start the server.
`python dev.py --reset` rebuilds the local database · `--admin` issues a new admin password · `--no-demo` skips sample articles ·
`--port 5001` · `--no-run` sets up without starting. `python dev.py` **backs up your database before any upgrade** (`instance/backups/`, newest 10 kept) and never resets it unless you pass `--reset`.

**Manual steps (if you prefer):**

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest
export FLASK_APP=app.py                                   # Windows: set FLASK_APP=app.py
flask reset-dev-db --demo            # builds instance/nbr-dev.sqlite: schema + categories + pages + sample articles
flask create-admin                   # prompts for email + password (12+ chars)
flask run                            # http://localhost:5000 — admin at /admin/
python -m pytest -q                  # 138 tests. TEST_DATABASE_URL=postgresql://… to run on Postgres
```
Local uploads use `STORAGE_PROVIDER=local` (`instance/uploads`); emails print to the console.

**Putting this version over an older copy?** The local database now has a new filename, so an old `instance/dev.sqlite` is simply
ignored — nothing to delete. If you point `DATABASE_URL` at an older database, `flask reset-dev-db` rebuilds a local SQLite one.
In local development the site shows a "Your database needs updating" page with these steps instead of a SQL error.

**Demo content** is flagged `is_demo`: labelled "Sample" everywhere, `noindex`, excluded from sitemaps/RSS. Remove it with `flask purge-demo`.

## 3. Environment variables

See [`.env.example`](.env.example). Required on Vercel: `SECRET_KEY` (≥32), `DATABASE_URL` (PostgreSQL), `SITE_URL`,
`CRON_SECRET` (≥16), `STORAGE_PROVIDER` + `STORAGE_*`, `EMAIL_PROVIDER` + `EMAIL_API_KEY` + `EMAIL_FROM`.
Optional: `RATELIMIT_STORAGE_URI` (Redis, so rate limits are shared across instances). `ADMIN_EMAIL`/`ADMIN_PASSWORD` are read
only by `flask create-admin`. Everything editorial (name, tagline, logo, colours, social links, contact details, SEO, GA ID,
newsletter text, maintenance mode…) lives in **Admin → Site settings**, not in code or env.

Scope `DATABASE_URL` and storage/email keys separately for *Production* and *Preview* so previews never touch production data.

## 4. Database & migrations

* Initialise any environment: `flask db upgrade`. Change the schema: edit `negarit/models.py` → `flask db migrate -m "describe"` → **review** the file → `flask db upgrade` → commit `migrations/`.
* **History:** `86462bd9bf17` initial schema → `3bca93f6297f` *stage 2* (this release). Stage 2 is **additive**: new tables (`home_sections`, `home_slots`,
  `article_revisions`, `editorial_notes`, `datasets`, `data_points`, `reports`) and new columns that all carry server defaults
  (`categories.parent_id/active`, `tags.description/active`, `authors.expertise`, `articles.is_breaking/sponsor_name`,
  `advertisements.campaign/impressions`). One data change: article type `data-story` → `data-report` (reversible). No rows are dropped.
  `tests/test_migration_upgrade.py` upgrades a populated v2 database on SQLite **and** PostgreSQL and checks nothing is lost.
* **Upgrading your existing local copy (v2 → this release):** unzip this release into a new folder, copy your old `instance/` folder into it, run
  `python dev.py`. It backs up `instance/nbr-dev.sqlite`, applies the migration, and adds the new starter pages/categories without touching your edits.
  Optional: `flask sync-taxonomy` renames/re-parents the starter categories to the new 13-section structure (never deletes; slugs and article URLs unchanged;
  legacy *Opinion/Analysis/Interviews/Explainers* categories are hidden because they are now content types).
* **Production:** never edit by hand. Merging to `main` runs `flask db upgrade` via GitHub Actions (repo secrets `SECRET_KEY`, `DATABASE_URL` — direct, non-pooled URL).
  Manual: `APP_ENV=production SECRET_KEY=… DATABASE_URL=… FLASK_APP=app.py flask db upgrade`. **Back up first**, always:
  `pg_dump "$DIRECT_DATABASE_URL" -Fc -f nbr_$(date +%F).dump` · restore: `pg_restore --clean --if-exists --no-owner -d "$TARGET_URL" file.dump` (into a scratch DB first).
  Vercel deploys the same commit in parallel, so keep migrations backwards-compatible. Enable your provider's point-in-time recovery; back up Cloudinary/S3 separately.
* **Safety commands (local SQLite only):** `flask backup-dev-db` · `flask reset-dev-db` (asks for confirmation and saves a backup first; refuses non-SQLite/production).

## 5. Deploy: GitHub → Vercel

1. `git init && git add . && git commit -m "NBR"`; push to a GitHub repo on `main`. Add repo secrets `SECRET_KEY`, `DATABASE_URL`;
   create an Actions environment `production` (optionally with required reviewers).
2. Vercel → **Add New → Project** → import the repo (Flask is auto-detected from `app.py`; no build command).
3. Add a Postgres database (Vercel Marketplace → Neon/Supabase) and set `DATABASE_URL` (pooled URL) for Production + Preview.
4. Create Cloudinary (or S3/R2) + Resend (verify your sending domain); set `STORAGE_*`, `EMAIL_*`, `SITE_URL`, `SECRET_KEY`, `CRON_SECRET`.
5. Push to `main` → CI tests + migrates → Vercel deploys. Every PR gets a preview deployment (auto `noindex`).
6. **Domain:** Project → Settings → Domains → add `negaritbusinessreview.com` (primary) and `www.` (the `vercel.json` redirect sends
   www → apex). Use the DNS records Vercel shows. Keep `SITE_URL=https://negaritbusinessreview.com` so canonical, sitemap, RSS and
   Open Graph URLs all use the apex. Submit `/sitemap.xml` and `/news-sitemap.xml` in Search Console.

**Scheduled publishing:** visibility is computed by time (`published_at <= now`), so a scheduled article goes live exactly on time
with no scheduler. The daily cron (`vercel.json`; Hobby allows once/day) only normalises `scheduled → published`. Public pages are
CDN-cached for 60 s, so a story can take a minute or two to appear on cached pages.

## 6. First-time production setup

```bash
export FLASK_APP=app.py APP_ENV=production SECRET_KEY=… DATABASE_URL='postgresql://…direct…'
flask db upgrade
ADMIN_EMAIL=editor@negaritbusinessreview.com ADMIN_PASSWORD='a-long-unique-passphrase' flask create-admin
flask seed-core
```
Sign in at `/admin/login`; set the logo, colours, contact details and legal pages in **Site settings** and **Pages**; create staff
under **Users**. **Review the starter legal text with counsel before launch.** Do not run `seed-demo` in production.

## 7. Post-deployment checklist

- [ ] Homepage, an article, a section, an author, search, `/contact`, a legal page load; `/api/health` → `{"status":"ok"}`
- [ ] Sign in; upload an image (check the URL is on Cloudinary/S3 and WebP variants exist); publish an article; schedule one 5 min ahead
- [ ] Writer account can submit but not publish; editor can approve/publish
- [ ] Subscribe with a real address → confirmation email → confirm; contact form → message in **Messages** (+ email to the contact address)
- [ ] "Forgot password" email arrives; link works once only
- [ ] `curl -i …/cron/publish-scheduled` → 401; with `Authorization: Bearer $CRON_SECRET` → 200
- [ ] `/robots.txt`, `/sitemap.xml`, `/news-sitemap.xml`, `/rss.xml` use the production domain; a preview URL serves `Disallow: /`
- [ ] `https://www.…` redirects to the apex; response headers show CSP, HSTS, X-Frame-Options
- [ ] Rich Results Test on an article; share a link to check the social card; Lighthouse mobile
- [ ] Add GA4 ID in **SEO** (loads only after cookie consent) and Search Console verification code

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| 500 on every request after deploy | Config fails closed: set `SECRET_KEY` (≥32) and a PostgreSQL `DATABASE_URL`; check function logs |
| `relation "…" does not exist` / `no such table: authors` | Migrations not applied: `flask db upgrade` (local old file: `python dev.py --reset`) |
| `Can't locate revision identified by '…'` | Database was created by an older version: local → `flask reset-dev-db`; production → restore a backup, never reset |
| Uploads fail | `STORAGE_PROVIDER` unset/misconfigured, or file > 4 MB (Vercel body limit) |
| No emails | `EMAIL_PROVIDER/API_KEY/FROM` unset or sender domain unverified; see logs |
| Cron returns 401 | `CRON_SECRET` unset/short, or called without the bearer header |
| New article not visible yet | CDN cache (≤ 60 s + SWR); check status/publish time and category |
| Many `too many requests` | Set `RATELIMIT_STORAGE_URI` to Redis; behind shared NAT raise limits in code |
| Fonts/CSS 404 locally | Run from the repo root; assets live in `public/static` |

## 9. Known limitations (honest list)

* **Not deployed or load-tested from here.** Verified locally: 138 tests on SQLite *and* real PostgreSQL; migrations up/down/up with populated data; an upgrade of a real v2
  database with zero rows lost; simulated Vercel production/preview boot; real-browser (Chromium) checks of public pages at 1440/1280/1024/768/430/390/360 px in light and dark
  (no overflow, CSP violations or console errors) and of the editor flow (typing, formatting, autosave, image from library, save, reload). **Not exercised:** Vercel itself,
  Cloudinary/S3, Resend/Postmark, DNS, Vercel Cron, Lighthouse/Core Web Vitals on production, screen readers, Safari/Firefox, Windows (`dev.py` paths untested there).
* **Email:** nothing is sent until `EMAIL_PROVIDER/API_KEY/FROM` are set. Until then newsletter sign-ups stay *Pending* and the site tells the visitor their confirmation email could
  not be sent (it never claims otherwise); the admin Subscribers page shows a warning. Password-reset and contact notifications likewise need email configured.
* **Data section:** manual entry/CSV import only; no automatic feeds. Reports are links to files you host (no PDF upload in the app). Charts are server-drawn line charts.
  The *Not recent* thresholds (monthly 75 days from period start, quarterly 165, annual 450, weekly 21, daily 7) are heuristics, so check the source.
* **Editor:** Quill 2 (self-hosted). Autosave covers draft / in-review / approved articles only, never live ones. Concurrent editing is guarded by a version check (the later
  autosave is rejected) but there is no live co-editing. Images are inserted from the media library (no drag-drop paste upload).
* **Ads:** impressions count page loads that rendered the ad (bots excluded), not viewability; no payment or billing integration; newsletter sponsorship is not built.
* **Analytics:** first-party, cookieless counts; no IP storage; `page_views` grows without pruning. Not a replacement for GA4/Search Console.
* **Uploads ≤ 4 MB** (Vercel body limit); WebP only (no AVIF); rate limits are per-instance unless `RATELIMIT_STORAGE_URI` is Redis.
* **Newsletter** sending campaigns, comment CAPTCHA, WYSIWYG for static pages (Markdown), NBR Premium paywall (only `articles.access_level` is stored) are not built.
* Default brand colours, wordmark and all legal/policy text are placeholders: **have counsel review the starter policies before launch.**
