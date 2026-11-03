from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@login_required
def _require_login():
    """Every admin route requires a signed-in, active user."""


def render_list(title, rows, headers, *, pagination=None, q=None, new_url=None, new_label="Add new", toolbar=(),
                tabs=(), empty="Nothing here yet.", note=""):
    return render_template("admin/list.html", title=title, rows=rows, headers=headers, pagination=pagination, q=q,
                           new_url=new_url, new_label=new_label, toolbar=toolbar, tabs=tabs, empty=empty, note=note)


def render_form(title, fields, *, action=None, back=None, submit="Save", extra_actions=(), note="", links=()):
    return render_template("admin/form.html", title=title, fields=fields, back=back, submit=submit,
                           extra_actions=extra_actions, note=note, links=links)


from . import articles, audience, ads, media, site, system, taxonomy  # noqa: E402,F401  (register routes)
