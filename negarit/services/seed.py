"""Core content (categories, legal pages) and clearly-labelled demo content."""
import io
import random
from datetime import timedelta

from PIL import Image, ImageDraw, ImageFont

from ..extensions import db
from ..models import Article, Author, Category, Media, Page, PageView, Tag
from ..utils import slugify, unique_slug, utcnow

# name, slug, parent slug, sort
CATEGORIES = [
    ("Economy & Public Policy", "economy", None, 10), ("Banking & Finance", "finance-banking", None, 20),
    ("Business & Corporate Affairs", "business", None, 30), ("Trade & Investment", "trade-investment", None, 40),
    ("Technology & Telecommunications", "technology", None, 50), ("Industry & Manufacturing", "industry", None, 60),
    ("Agriculture & Agribusiness", "agriculture", None, 70), ("Energy & Infrastructure", "energy", None, 80),
    ("Real Estate & Construction", "real-estate", None, 90), ("Entrepreneurship & Startups", "entrepreneurship", None, 100),
    ("Markets & Investment", "markets", None, 110), ("African Business", "african-business", None, 120),
    ("Global Business", "global-business", None, 130),
    ("Inflation & Monetary Policy", "inflation-monetary", "economy", 11), ("Public Finance & Budget", "public-finance", "economy", 12),
    ("Banking", "banking", "finance-banking", 21), ("Fintech & Digital Payments", "fintech", "finance-banking", 22),
    ("Companies", "companies", "business", 31), ("Mergers & Acquisitions", "mergers-acquisitions", "business", 32),
    ("Exports & Imports", "exports-imports", "trade-investment", 41), ("Foreign Investment", "foreign-investment", "trade-investment", 42),
    ("Telecom & Digital", "telecom-digital", "technology", 51), ("Digital Transformation", "digital-transformation", "technology", 52),
    ("Infrastructure", "infrastructure", "energy", 81), ("Renewable Energy", "renewable-energy", "energy", 82),
]
# Used by `flask sync-taxonomy` to align an EXISTING database: legacy slug -> (new name, parent slug or None)
LEGACY_RENAMES = {
    "economy": ("Economy & Public Policy", None), "finance-banking": ("Banking & Finance", None),
    "business": ("Business & Corporate Affairs", None), "investment": ("Trade & Investment", None),
    "technology": ("Technology & Telecommunications", None), "agriculture": ("Agriculture & Agribusiness", None),
    "energy": ("Energy & Infrastructure", None), "real-estate": ("Real Estate & Construction", None),
    "entrepreneurship": ("Entrepreneurship & Startups", None), "markets": ("Markets & Investment", None),
    "companies": ("Companies", "business"), "telecom-digital": ("Telecom & Digital", "technology"),
    "infrastructure": ("Infrastructure", "energy"),
}
# Replaced by content types (News/Analysis/Opinion/...): hidden from navigation, old article URLs keep working.
LEGACY_TO_DEACTIVATE = ("opinion", "analysis", "interviews", "explainers", "data-insights", "transport-logistics")

PAGES = [
    ("About Us", "about", 10, """
**{site}** is a digital business publication covering the economy, finance, technology, telecoms, investment and enterprise
in Ethiopia and across Africa. Our tagline says what we try to do: *navigate business dynamics* — combining news, analysis,
data and business intelligence for decision-makers.

## What we publish
News, analysis, opinion, interviews, explainers, data stories and features. Every piece is labelled by type so you always
know whether you are reading reporting or commentary.

## Contact
Reach the newsroom through our [contact page](/contact)."""),
    ("Privacy Policy", "privacy-policy", 20, """
*Starter text — have it reviewed by legal counsel before launch.*

## What we collect
- **Newsletter:** your email address, sign-up date and confirmation status.
- **Contact form and comments:** the details you submit.
- **Reader analytics:** anonymous page-view counts (page, referrer type, date). We do not store IP addresses or set
  cookies for this.
- **Optional analytics:** if enabled, Google Analytics loads only after you accept cookies.

## How we use it
To send the newsletter you asked for, reply to enquiries, moderate comments and understand which content is useful.

## Your choices
You can unsubscribe from any newsletter email, ask us to delete your data via the [contact page](/contact), and decline
optional cookies."""),
    ("Terms of Use", "terms-of-use", 30, """
*Starter text — have it reviewed by legal counsel before launch.*

By using this website you agree to these terms.

1. **Content.** Articles are provided for information and are not investment, legal or tax advice.
2. **Copyright.** Content is owned by the publisher or its licensors. You may quote briefly with attribution and a link.
3. **Comments.** You are responsible for what you post. We may remove comments that are unlawful, abusive or spam.
4. **Links.** We are not responsible for third-party websites.
5. **Changes.** We may update these terms; continued use means acceptance."""),
    ("Cookie Policy", "cookie-policy", 40, """
*Starter text — have it reviewed by legal counsel before launch.*

## Essential
A session cookie is used only for the staff sign-in area. A local-storage entry remembers your light/dark theme and your
cookie choice.

## Analytics (optional)
If Google Analytics is enabled, it sets cookies **only after you accept** in the cookie banner. You can change your mind by
clearing site data in your browser.

## Advertising
Advertisements shown on this site are served directly by us and do not use third-party tracking cookies."""),
    ("Editorial Policy", "editorial-policy", 50, """
*Starter text — adapt to your newsroom's practice.*

## Independence and accuracy
Editorial decisions are independent of advertisers and sponsors. We verify facts against primary sources where possible and
attribute information clearly.

## Corrections
If we get something wrong we correct it promptly and note the correction on the article. Report errors through the
[contact page](/contact).

## Content types
News reports facts. Analysis and Opinion carry the author's interpretation and are labelled. Sponsored content is always
labelled as such.

## Comments
Comments are moderated before publication."""),
    ("Corrections Policy", "corrections-policy", 55, """
*Starter text: adapt to your newsroom's practice.*

We aim to be accurate. When we are not, we correct the record quickly and transparently.

- **Errors of fact** are corrected in the article, with a note stating what was changed and when.
- **Significant corrections** are labelled at the top of the article.
- **Headline or image errors** are fixed in place and noted where they change the meaning.
- We do not silently remove published content except where required by law or for safety; any such removal is noted.

To report an error, use the *Correction Request* option on the [contact page](/contact), with the article link and the correct information."""),
    ("Advertise With Us", "advertise", 62, """
*Starter text: add your real packages and rate card.*

{site} reaches business leaders, investors, entrepreneurs and policymakers interested in Ethiopia, Africa and global business.

## Opportunities
- **Display advertising** across the homepage, sections and articles
- **Sponsored content**, always clearly labelled and kept separate from our reporting
- **Newsletter sponsorship** (in preparation)
- **Corporate partnerships and reports**

Editorial independence comes first: advertisers and sponsors have no influence over coverage. See our [advertising policy](/advertising-policy).

For rates and availability, contact us via the *Advertising* option on the [contact page](/contact)."""),
    ("Write for Us", "write-for-us", 64, """
*Starter text: adapt to your commissioning process.*

We welcome pitches from journalists, analysts, economists and practitioners.

## What we look for
- Original reporting and analysis on business, the economy, finance, technology, trade, agriculture, energy and enterprise
- Clear arguments supported by data and named sources
- Opinion pieces that add a distinct, well-argued perspective

## How to pitch
Use the *Write for Us / Story Pitch* option on the [contact page](/contact). Include a headline, a three-sentence summary, why now, your sources and a link to previous work. All submissions are reviewed by an editor and edited before publication; opinion is labelled as such.""" ),
    ("Advertising Policy", "advertising-policy", 60, """
*Starter text — adapt to your commercial terms.*

- Advertising and sponsored content are clearly labelled.
- Advertisers have no influence over editorial coverage.
- We do not accept advertising that is misleading, unlawful or discriminatory.
- To advertise, use the *Advertising* option on the [contact page](/contact)."""),
]


def seed_core() -> tuple[int, int]:
    from ..services import settings
    site = settings.get("site_name")
    added_c = added_p = 0
    by_slug = {c.slug: c for c in Category.query.all()}
    for name, slug, parent, order in CATEGORIES:
        if slug in by_slug or Category.query.filter_by(name=name).first():
            continue
        c = Category(name=name, slug=slug, is_primary=parent is None and order <= 60, sort_order=order,  # first 6 fit the strip on one row; the rest sit under "More"
                     parent_id=by_slug[parent].id if parent and parent in by_slug else None)
        db.session.add(c)
        db.session.flush()
        by_slug[slug] = c
        added_c += 1
    for title, slug, order, body in PAGES:
        if not Page.query.filter_by(slug=slug).first():
            p = Page(title=title, slug=slug, sort_order=order)
            p.set_body(body.strip().replace("{site}", site))
            db.session.add(p)
            added_p += 1
    db.session.commit()
    from .homepage import ensure_default_sections
    ensure_default_sections()
    return added_c, added_p


def sync_taxonomy() -> dict:
    """Align an existing database with the current category set. Adds, renames, re-parents and hides legacy sections;
    NEVER deletes, and never changes an existing slug (so published URLs keep working)."""
    stats = {"created": 0, "renamed": 0, "reparented": 0, "deactivated": 0}
    stats["created"], _ = seed_core()
    by_slug = {c.slug: c for c in Category.query.all()}
    for slug, (name, parent) in LEGACY_RENAMES.items():
        c = by_slug.get(slug)
        if not c:
            continue
        clash = Category.query.filter(Category.name == name, Category.id != c.id).first()
        if c.name != name and not clash:
            c.name = name
            stats["renamed"] += 1
        target = by_slug.get(parent).id if parent and parent in by_slug else None
        if c.parent_id != target and (parent is None or target):
            c.parent_id = target
            c.is_primary = parent is None
            stats["reparented"] += 1
    for slug in LEGACY_TO_DEACTIVATE:
        c = by_slug.get(slug)
        if c and c.active:
            c.active = False
            stats["deactivated"] += 1
    db.session.commit()
    return stats


# ------------------------------------------------------------------ demo content
DISCLAIMER = ("> **Sample content.** This article was written for development and testing of the website. "
              "It is fictional, is not news, and describes no real events, companies or figures.\n\n")
PARA = [
    "Analysts often separate short-term noise from structural change. In this illustrative example we walk through how a "
    "reader might do the same, using generic assumptions rather than real data.",
    "A useful starting point is to ask which decisions the information should change. If a number moves but no decision "
    "does, it is context rather than a signal.",
    "Second, compare like with like. Growth rates measured from different base periods, or in different currencies, can "
    "look dramatic while describing very little.",
    "Third, look for the mechanism. A credible explanation names who is affected, how, and over what time frame; a weak "
    "one relies on adjectives.",
    "Finally, keep a record of what you expected. Reviewing expectations against outcomes is the fastest way to improve "
    "judgement, whether you run a company or read about one.",
]
# (category, type, title, subtitle, tags)
DEMO = [
    ("economy", "analysis", "SAMPLE: Reading an inflation print without over-reading it", "A framework for separating trend from noise in monthly data.", ["Inflation", "Data"]),
    ("finance-banking", "news", "SAMPLE: A bank launches a customer-service dashboard", "Illustrative news layout for the finance desk.", ["Banking", "Digital"]),
    ("technology", "feature", "SAMPLE: How small teams choose their first software stack", "A long-form layout test with subheads and quotes.", ["Software", "Startups"]),
    ("telecom-digital", "explainer", "SAMPLE: What is mobile money interoperability?", "An explainer layout: the question, the answer, the caveats.", ["Payments", "Digital"]),
    ("african-business", "news", "SAMPLE: A regional trade bloc discusses payment harmonisation", "Placeholder story for the African business desk.", ["Trade", "Payments"]),
    ("entrepreneurship", "interview", "SAMPLE: Founder Q&A \u2014 building a logistics start-up", "Interview layout with a fictional founder.", ["Startups", "Logistics"]),
    ("companies", "news", "SAMPLE: A manufacturer reports a fictional quarter", "Placeholder earnings-style story.", ["Earnings", "Manufacturing"]),
    ("markets", "data-report", "SAMPLE: Charting a fictional commodity price index", "Data-report layout placeholder.", ["Commodities", "Data"]),
    ("industry", "opinion", "SAMPLE: Why boring infrastructure deserves better headlines", "Opinion layout with a clear byline.", ["Infrastructure"]),
    ("trade-investment", "analysis", "SAMPLE: The trade-offs behind a hypothetical tariff change", "Analysis layout placeholder.", ["Trade", "Policy"]),
    ("global-business", "news", "SAMPLE: Global shipping costs ease in a fictional quarter", "Placeholder story for the global desk.", ["Logistics", "Trade"]),
    ("energy", "explainer", "SAMPLE: Grid capacity versus generation capacity", "Explainer layout for the energy desk.", ["Energy", "Infrastructure"]),
    ("agriculture", "feature", "SAMPLE: A fictional cooperative rethinks its supply chain", "Feature layout placeholder.", ["Agribusiness", "Logistics"]),
    ("real-estate", "news", "SAMPLE: Developers test modular construction", "Placeholder real-estate story.", ["Construction"]),
    ("trade-investment", "explainer", "SAMPLE: How freight corridors are priced", "Explainer layout placeholder.", ["Logistics", "Trade"]),
    ("business", "sponsored", "SAMPLE: How a fictional software firm helps retailers plan stock", "Sponsored-content layout with a clear disclosure.", ["Retail"]),
]
PALETTES = [("#10233F", "#2E5A88"), ("#3B2F1B", "#9A5B00"), ("#123B34", "#2F8F7B"), ("#3A1C2E", "#A5476F"),
            ("#1F2A44", "#5573C1"), ("#2B2B2B", "#777777")]


def _placeholder(colors, label="SAMPLE IMAGE") -> bytes:
    w, h = 1600, 900
    top, bottom = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in colors)
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):  # vertical gradient, one line per row
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    d.text((80, h - 140), label, font=font, fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def seed_demo(images: bool = True) -> int:
    from . import media as media_service
    rnd = random.Random(7)
    authors = []
    for name, pos in (("Demo Desk", "Sample author (fictional)"), ("Sample Analyst", "Sample author (fictional)"),
                      ("Test Columnist", "Sample author (fictional)")):
        a = Author.query.filter_by(name=name).first()
        if not a:
            a = Author(name=name, slug=unique_slug(Author, name), position=pos,
                       bio="A fictional author created for development and testing. Not a real person.")
            db.session.add(a)
        authors.append(a)
    db.session.flush()
    medias = []
    if images:
        for i, colors in enumerate(PALETTES):
            m = media_service.create_media(_placeholder(colors), f"sample-{i + 1}.jpg", alt=f"Sample placeholder image {i + 1}",
                                           caption="Sample placeholder image", credit="Generated")
            medias.append(m)
    now = utcnow()
    created = 0
    for i, (cat_slug, atype, title, sub, tags) in enumerate(DEMO):
        slug = slugify(title.replace("SAMPLE:", "sample"))
        if Article.query.filter_by(slug=slug).first():
            continue
        cat = Category.query.filter_by(slug=cat_slug).first()
        if not cat:
            continue
        a = Article(title=title, slug=slug, subtitle=sub, excerpt=sub, article_type=atype, status="published", is_demo=True,
                    category_id=cat.id, author_id=authors[i % 3].id, is_featured=(i in (0, 1, 2)), is_breaking=(i == 1),
                    sponsor_name="Sample Sponsor (fictional)" if atype == "sponsored" else "",
                    published_at=now - timedelta(hours=6 + i * 9), view_count=rnd.randint(20, 900),
                    cover_caption="Sample placeholder image", meta_description=sub)
        body = DISCLAIMER + "\n\n".join(PARA[: 3 + i % 3]) + "\n\n## A second section\n\n" + PARA[(i + 1) % 5] + \
            "\n\n> This pull quote is placeholder text used to check blockquote styling.\n\n" + PARA[(i + 2) % 5]
        a.set_body(body)
        a.set_body_html(a.body_html)
        a.set_sources("*Sources:* illustrative example — no real sources.")
        if medias:
            a.featured_media_id = medias[i % len(medias)].id
        db.session.add(a)
        with db.session.no_autoflush:
            for t in tags:
                tag = Tag.query.filter_by(name=t).first() or Tag(name=t, slug=unique_slug(Tag, t))
                db.session.add(tag)
                a.tags.append(tag)
        db.session.flush()
        for _ in range(rnd.randint(8, 30)):
            db.session.add(PageView(path=a.url_path, article_id=a.id, category_id=cat.id, author_id=a.author_id,
                                    source=rnd.choice(["direct", "search", "search", "social", "referral", "internal"]),
                                    created_at=now - timedelta(hours=rnd.randint(0, 24 * 20))))
        created += 1
    db.session.commit()
    _seed_demo_extras(now, rnd)
    return created


def _seed_demo_extras(now, rnd):
    """Editor's picks and two clearly-synthetic datasets + a sample report (all flagged is_demo)."""
    from datetime import date
    from ..models import DataPoint, Dataset, HomeSlot, Report
    if not HomeSlot.query.filter_by(slot="picks").count():
        for pos, a in enumerate(Article.query.filter_by(is_demo=True).order_by(Article.id).limit(8).all()[3:6]):
            db.session.add(HomeSlot(slot="picks", article_id=a.id, position=pos))
    specs = [("sample-inflation-index", "SAMPLE: Headline inflation (synthetic)", "Inflation", "% year-on-year", "monthly", 1, 14.0, 0.6),
             ("sample-exchange-rate", "SAMPLE: Exchange rate (synthetic)", "Exchange rates", "local currency per USD", "monthly", 2, 55.0, 1.4)]
    for slug, title, topic, unit, freq, dec, base, step in specs:
        if Dataset.query.filter_by(slug=slug).first():
            continue
        ds = Dataset(slug=slug, title=title, topic=topic, unit=unit, frequency=freq, decimals=dec, is_demo=True,
                     description="SYNTHETIC SAMPLE DATA for testing the Data section. These numbers are invented and describe nothing real.",
                     source_name="Illustrative sample (not a real source)", context="Do not cite. Replace with real, sourced series before launch.")
        db.session.add(ds)
        db.session.flush()
        y, m, v = now.year, now.month, base
        for _ in range(18):
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        for k in range(18):
            v = round(v + rnd.uniform(-step, step), dec)
            db.session.add(DataPoint(dataset_id=ds.id, period=date(y, m, 1), value=v))
            m += 1
            if m == 13:
                y, m = y + 1, 1
    if not Report.query.filter_by(is_demo=True).count():
        db.session.add(Report(title="SAMPLE: Annual business outlook (fictional)", topic="Outlook", is_demo=True,
                              summary="A placeholder entry showing how downloadable reports are listed. The link is not a real report.",
                              file_url="https://example.com/sample-report.pdf", source_name="Illustrative sample", published_on=now.date()))
    db.session.commit()


def purge_demo() -> int:
    from ..models import Dataset, HomeSlot, Report
    ids = [a.id for a in Article.query.filter_by(is_demo=True).all()]
    n = len(ids)
    if ids:
        PageView.query.filter(PageView.article_id.in_(ids)).delete(synchronize_session=False)
        HomeSlot.query.filter(HomeSlot.article_id.in_(ids)).delete(synchronize_session=False)
    for ds in Dataset.query.filter_by(is_demo=True).all():
        db.session.delete(ds)
    Report.query.filter_by(is_demo=True).delete(synchronize_session=False)
    for a in Article.query.filter_by(is_demo=True).all():
        db.session.delete(a)
    db.session.flush()
    for author in Author.query.filter_by(bio="A fictional author created for development and testing. Not a real person.").all():
        if not author.articles:
            db.session.delete(author)
    from . import media as media_service
    for m in Media.query.filter(Media.alt_text.like("Sample placeholder image%")).all():
        media_service.delete_media(m, force=True)
    db.session.commit()
    return n
