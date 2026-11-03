from flask_login import current_user

from ..extensions import client_ip, db
from ..models import ActivityLog


def log(action: str, entity_type: str = "", entity_id: int | None = None, summary: str = "", user=None):
    """Queue an audit record on the session; the caller commits."""
    u = user or (current_user._get_current_object() if current_user and current_user.is_authenticated else None)
    db.session.add(ActivityLog(user_id=u.id if u else None, user_email=(u.email if u else "")[:255], action=action,
                               entity_type=entity_type, entity_id=entity_id, summary=(summary or "")[:400],
                               ip=client_ip()))
