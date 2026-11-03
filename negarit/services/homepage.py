"""Editor-controlled homepage: lead story, breaking banner, editor's picks, ordered/visible sections."""
from types import SimpleNamespace

from sqlalchemy import select

from ..extensions import db
from ..models import Article, Category, HomeSection, HomeSlot
from . import content, data, settings

# kind, title, category slug, article types (comma separated), item limit
DEFAULTS = [
    ("latest", "Latest news", "", "", 8), ("top", "Top business stories", "", "", 5), ("picks", "Editor\u2019s picks", "", "", 4),
    ("category", "", "economy", "", 4), ("category", "", "finance-banking", "", 4), ("category", "", "trade-investment", "", 4),
    ("category", "", "technology", "", 4), ("category", "", "industry", "", 4), ("category", "", "agriculture", "", 4),
    ("category", "", "energy", "", 4), ("category", "", "real-estate", "", 4), ("category", "", "entrepreneurship", "", 4),
    ("category", "", "african-business", "", 4), ("category", "", "global-business", "", 4),
    ("type", "Analysis & opinion", "", "analysis,opinion", 4), ("data", "Economic data & reports", "", "", 4),
    ("most_read", "Most read", "", "", 6),
]


def ensure_default_sections() -> int:
    """Persist the default layout the first time an editor opens the homepage manager (never overwrites)."""
    if HomeSection.query.count():
        return 0
    cats = {c.slug: c for c in Category.query.all()}
    n = 0
    for i, (kind, title, slug, types, limit) in enumerate(DEFAULTS):
        if kind == "category" and slug not in cats:
            continue
        db.session.add(HomeSection(kind=kind, title=title, category_id=cats[slug].id if slug else None, article_type=types,
                                   position=(i + 1) * 10, visible=True, item_limit=limit))
        n += 1
    db.session.commit()
    return n


def effective_sections() -> list:
    rows = HomeSection.query.order_by(HomeSection.position, HomeSection.id).all()
    if rows:
        return [r for r in rows if r.visible]
    cats = {c.slug: c for c in Category.query.all()}  # nothing configured yet: show the defaults (not persisted)
    return [SimpleNamespace(id=None, kind=k, title=t, category=cats.get(s), category_id=cats[s].id if s in cats else None,
                            article_type=ty, item_limit=lim, visible=True)
            for k, t, s, ty, lim in DEFAULTS if k != "category" or s in cats]


def slot_articles(slot: str) -> list[Article]:
    ids = [r.article_id for r in HomeSlot.query.filter_by(slot=slot).order_by(HomeSlot.position)]
    if not ids:
        return []
    live = {a.id: a for a in db.session.scalars(content.live_stmt(Article.id.in_(ids)))}
    return [live[i] for i in ids if i in live]  # an unpublished/archived pick silently drops out


def _hours() -> int:
    try:
        return min(max(int(settings.get("breaking_hours")), 1), 168)
    except (TypeError, ValueError):
        return 24


def build() -> dict:
    lead_pick = slot_articles("lead")
    lead = lead_pick[0] if lead_pick else (content.fetch(1, Article.is_featured.is_(True)) or [None])[0]
    pool = content.fetch(6, exclude=[lead.id] if lead else [])
    if lead is None and pool:
        lead, pool = pool[0], pool[1:]
    secondary = pool[:4]
    used = {a.id for a in ([lead] if lead else []) + secondary}
    blocks, group = [], None
    for sec in effective_sections():
        if sec.kind == "latest":
            items = content.fetch(sec.item_limit, exclude=used)
            used |= {a.id for a in items}
            block = {"kind": "latest", "title": sec.title or "Latest news", "items": items}
        elif sec.kind == "top":
            items = content.fetch(sec.item_limit, Article.is_featured.is_(True), exclude=[lead.id] if lead else [])
            block = {"kind": "top", "title": sec.title or "Top stories", "items": items}
        elif sec.kind == "picks":
            block = {"kind": "picks", "title": sec.title or "Editor\u2019s picks", "items": slot_articles("picks")[: sec.item_limit]}
        elif sec.kind == "category":
            cat = sec.category
            if not cat or not cat.active:
                continue
            block = {"kind": "category", "title": sec.title or cat.name, "href": f"/{cat.slug}",
                     "items": content.fetch(sec.item_limit, Article.category_id.in_(cat.family_ids))}
        elif sec.kind == "type":
            types = [t for t in sec.article_type.split(",") if t]
            block = {"kind": "type", "title": sec.title or "Stories", "items": content.fetch(sec.item_limit, Article.article_type.in_(types)),
                     "href": "/" + __import__("negarit.models", fromlist=["TYPE_ROUTES"]).TYPE_ROUTES.get(types[0], "news") if types else None}
        elif sec.kind == "data":
            from ..models import Report
            reports = Report.query.filter_by(published=True).order_by(Report.published_on.desc().nullslast(), Report.id.desc()).limit(3).all()
            block = {"kind": "data", "title": sec.title or "Economic data & reports", "cards": data.home_cards(sec.item_limit),
                     "reports": reports, "items": []}
            if not block["cards"] and not reports:
                continue
        elif sec.kind == "most_read":
            block = {"kind": "most_read", "title": sec.title or "Most read", "items": content.most_read(sec.item_limit, days=7)}
        else:
            continue
        if sec.kind != "data" and not block["items"]:
            continue  # never render an empty block
        if block["kind"] == "category":  # consecutive category blocks share one two-column grid
            if group is None:
                group = {"kind": "category_group", "blocks": []}
                blocks.append(group)
            group["blocks"].append(block)
        else:
            group = None
            blocks.append(block)
    ticker = []
    if settings.flag("breaking_enabled"):
        ticker = content.breaking(_hours(), 4)
    return {"lead": lead, "secondary": secondary, "trending": content.trending(5), "blocks": blocks, "ticker": ticker,
            "ticker_label": settings.get("breaking_label") or "Breaking"}
