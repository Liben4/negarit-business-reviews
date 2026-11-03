# NBR — Architecture, assessment and roadmap

## 1. Current-architecture assessment (baseline before this work)

Flask app factory on Vercel serverless; PostgreSQL via Alembic; external image storage (Cloudinary/S3); API email; DB-backed
login throttle; time-based scheduled visibility; CSP with no inline code; CDN-cacheable public pages (no session, no CSRF tokens).
**Existing features:** articles (Markdown → sanitised HTML), categories, users with 3 roles, single-author-per-user, search
(title/excerpt/body), newsletter double opt-in + CSV, admin CRUD, robots/sitemap/RSS, JSON-LD NewsArticle, password reset.
**Bugs/gaps found:** URLs were `/article/<slug>` (spec wants `/<category>/<slug>`); one author = one login user (no public
author profiles/socials); no tags, article types, deck/credits/sources; no media library (uploads were untracked URLs); no
editorial workflow (3 statuses, 3 roles); no ads, comments, analytics, contact, legal pages, site settings, activity log,
maintenance page; design was generic (system fonts, single CSS, hero slider).
**Decision:** keep the working platform layer (config, storage adapters, mailer, security, CDN/cron design, tests) and
extend it. The schema changed substantially and nothing had been deployed, so migrations were regenerated as one clean
initial migration. Everything else is incremental.

## 2. Recommended architecture

```
negarit/
  config.py            env → config, fail-closed on Vercel
  extensions.py        db, migrate, csrf, login, limiter
  models.py            all tables (below)
  permissions.py       role → capability matrix (single source of truth)
  forms.py             WTForms for public forms (contact, comment, subscribe)
  security.py          permission_required, login throttle, cron auth
  storage.py           provider adapters (cloudinary | s3 | local-dev) with save/delete
  mailer.py            Resend / Postmark
  services/            business logic, no request/response code
    settings.py        DB-backed site settings (cached, defaults in code)
    activity.py        audit log
    media.py           validate → optimise → WebP variants → store → Media row
    workflow.py        editorial state machine + permissions
    content.py         public queries (live filter, home blocks, related, search, most-read)
    ads.py             placement selection, click redirect
    analytics.py       page-view recording and reports
    seed.py            core content + clearly-labelled demo content
  views/
    public.py seo.py auth.py cron.py
    admin/             one module per admin area, all on the `admin` blueprint
  templates/ public/ admin/ auth/ email/
public/static/         css, js, self-hosted fonts (CDN-served on Vercel)
```

## 3. Database design

`users` (role: super_admin|admin|editor|writer|contributor) · `authors` (public profile; optional `user_id`; socials; image) ·
`categories` (`is_primary` → main nav, else "More") · `tags` · `article_tags` · `articles` (type, status, deck, excerpt,
body, sources, featured media + caption, SEO fields, `access_level`, `is_demo`, `view_count`) · `article_media` (gallery) ·
`media` (alt/caption/credit, WebP variants, storage keys) · `subscribers` · `advertisements` · `comments` (moderated) ·
`site_settings` (key/value) · `activity_logs` · `page_views` (first-party, cookieless) · `pages` (legal/static, CMS-managed) ·
`contact_messages` · `login_attempts`.

Workflow: `draft → review → approved → scheduled → published` (+ send-back, unschedule, unpublish). Public visibility is by
time (`published_at <= now` and status in scheduled/published), so scheduling never depends on a running job.

## 4. UI/UX structure

Editorial design system: Newsreader (headlines, article body) + IBM Plex Sans (UI), self-hosted; hairline rules instead of
cards; 2–4 px radii; paper/ink palette with one amber accent (brand colours editable in Settings); light/dark/system theme.
Masthead → primary nav (12 items + **More**) → homepage: lead story + secondary + trending; latest; category sections;
Opinion & Analysis; Interviews / Explainers; Most-read rail; newsletter band; ad slots; footer. Article: breadcrumb, category,
type badge, headline, deck, byline, dates, hero image + caption/credit, body, gallery, sources, tags, share, author box,
related, most-read, comments, newsletter.

## 5. Extension points (designed, deliberately not built)

- **NBR Data:** add `datasets` / `data_points` tables + a `views/data.py` blueprint under `/data`; charts client-side from
  JSON endpoints; `article_type = "data-story"` already exists to embed datasets in stories.
- **NBR Premium:** `articles.access_level` (`free|premium`) is stored and exposed; enforce it in one place
  (`views/public.article`) once a subscription/billing provider is chosen; premium newsletter = a flag on `subscribers`.
- **Analytics:** GA4 measurement ID and Search Console verification are settings; GA loads only after cookie consent.

## 6. Roadmap (phases, each ends with a test run)

1 architecture/DB/auth/design system · 2 homepage/categories/article system · 3 admin CMS + workflow ·
4 newsletter/ads/authors/media/comments/contact/pages · 5 SEO/analytics/security/performance · 6 QA, deploy docs.

## 7. Assumptions

Existing brand colours/logo were not provided → default navy + amber, text wordmark, all editable in **Site Settings**.
Legal page text is generic starter copy and **must be reviewed by counsel**. Demo content is flagged `is_demo`, labelled on
the page, `noindex`, and excluded from sitemap/RSS.


## 8. Stage 2 additions (this release)

* **Homepage:** `HomeSection` (kind, order, visibility, limit) + `HomeSlot` (lead, editor's picks) drive `services/homepage.build()`. Empty sections never render; defaults are
  used until an editor first opens the manager.
* **Workflow & history:** `services/workflow.py` is the single state machine (draft → review → approved → scheduled/published, archive/restore). `ArticleRevision` snapshots
  on save/transition (not on autosave); `EditorialNote` holds internal notes; autosave endpoint is restricted to non-live statuses.
* **Rich text:** HTML from the editor is sanitised with an allow-list (`utils.clean_editor_html`) before storage; legacy Markdown bodies keep working.
* **Data:** `Dataset` → `DataPoint` (unique per period) + `Report`; `services/data.py` does staleness, SVG charts, CSV, import parsing. `articles.access_level` and the `/data`
  blueprint are the hooks for NBR Premium and richer dashboards.
* **RBAC:** capability matrix in `permissions.py`; every admin route uses `permission_required(<capability>)`.
* **Taxonomy:** categories have one level of subcategories and an `active` flag; article URLs use the article's own category slug and redirect if it changes.
