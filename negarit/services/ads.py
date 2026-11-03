import random

from flask import g
from sqlalchemy.orm import joinedload

from ..models import Advertisement
from . import settings


def _running() -> dict:
    """One query per request for all running ads, grouped by placement."""
    if not hasattr(g, "_ads"):
        g._ads = {}
        if settings.flag("ads_enabled"):
            rows = Advertisement.query.options(joinedload(Advertisement.media)).filter(Advertisement.running_filter()).all()
            for ad in rows:
                g._ads.setdefault(ad.placement, []).append(ad)
    return g._ads


def pick(placement: str, limit: int = 1) -> list[Advertisement]:
    pool = list(_running().get(placement, []))
    random.shuffle(pool)
    return pool[:limit]
