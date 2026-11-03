from flask import Blueprint, abort, jsonify

from ..cli import publish_due
from ..extensions import limiter
from ..security import cron_authorized

bp = Blueprint("cron", __name__, url_prefix="/cron")


@bp.get("/publish-scheduled")
@limiter.exempt
def publish_scheduled():
    """Housekeeping only: public visibility is already time-based, so a late or
    skipped run never delays an article. Called by Vercel Cron (daily on Hobby)
    and/or an external scheduler with `Authorization: Bearer $CRON_SECRET`."""
    if not cron_authorized():
        abort(401)
    return jsonify(published=publish_due())
