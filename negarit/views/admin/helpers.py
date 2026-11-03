"""Tiny form-spec toolkit so every admin entity shares one form template and one parser."""
from datetime import date
from urllib.parse import urlparse

from email_validator import EmailNotValidError, validate_email
from flask import flash, request

from ...extensions import db
from ...models import Media
from ...utils import from_local_input


def f(name, label, kind="text", value=None, options=None, help="", required=False, maxlength=None, placeholder="",
      rows=None, preview=None, full=False, readonly=False):
    return dict(name=name, label=label, kind=kind, value=value, options=options or [], help=help, required=required,
                maxlength=maxlength, placeholder=placeholder, rows=rows, preview=preview, full=full, readonly=readonly)


def parse(spec: list[dict], form=None) -> tuple[dict, list[str]]:
    """Validate submitted values against a field spec. Returns (values, errors)."""
    form = form if form is not None else request.form
    out, errors = {}, []
    for fld in spec:
        n, kind, label = fld["name"], fld["kind"], fld["label"]
        raw = form.get(n, "")
        if kind == "bool":
            out[n] = form.get(n) in ("on", "1", "true")
            continue
        raw = (raw or "").strip()
        if fld["maxlength"]:
            raw = raw[: fld["maxlength"]]
        if kind == "number":
            try:
                out[n] = int(raw) if raw else 0
            except ValueError:
                errors.append(f"{label} must be a number.")
                out[n] = 0
            continue
        if kind == "select":
            valid = {str(v) for v, _l in fld["options"]}
            if raw in valid:
                out[n] = raw
            else:  # never silently coerce a forged/invalid choice to some other option
                out[n] = ""
                if fld["required"] or raw:
                    errors.append(f"{label}: choose one of the available options.")
            continue
        if kind in ("media", "media_id"):
            mid = int(raw) if raw.isdigit() else None
            out[n] = mid if mid and db.session.get(Media, mid) else None
            continue
        if kind == "datetime":
            out[n] = from_local_input(raw)
            continue
        if kind == "date":
            try:
                out[n] = date.fromisoformat(raw) if raw else None
            except ValueError:
                errors.append(f"{label} is not a valid date.")
                out[n] = None
            continue
        if kind == "email" and raw:
            try:
                raw = validate_email(raw, check_deliverability=False).normalized.lower()
            except EmailNotValidError:
                errors.append(f"{label} is not a valid email address.")
        if kind == "url" and raw and not is_web_url(raw):
            errors.append(f"{label} must start with https:// or http://.")
            raw = ""
        if fld["required"] and not raw and kind != "password":
            errors.append(f"{label} is required.")
        out[n] = raw
    return out, errors


def is_web_url(u: str) -> bool:
    p = urlparse(u)
    return p.scheme in ("http", "https") and bool(p.netloc)


def flash_all(errors):
    for e in errors:
        flash(e, "error")


def page_arg() -> int:
    return min(max(request.args.get("page", 1, type=int) or 1, 1), 5000)


def ids_from_csv(raw: str) -> list[int]:
    seen, out = set(), []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) not in seen:
            seen.add(int(part))
            out.append(int(part))
    return out
