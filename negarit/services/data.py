"""Data section helpers: staleness, summaries, accessible SVG charts, CSV, and manual import parsing.
Nothing here fabricates or fetches values: every number comes from a DataPoint a staff member entered."""
import csv
import io
import re
from datetime import date, datetime

from markupsafe import Markup, escape

from ..models import FREQ_DAYS

MAX_CHART_POINTS = 120


def fmt(v: float, decimals: int = 1) -> str:
    return f"{v:,.{max(0, min(int(decimals), 6))}f}"


def latest(ds):
    return ds.points[-1] if ds.points else None


def age_days(ds, today: date | None = None) -> int | None:
    last = latest(ds)
    return None if not last else ((today or date.today()) - last.period).days


def is_stale(ds, today: date | None = None) -> bool:
    """No data, or the newest observation is older than the series' normal publication rhythm allows."""
    a = age_days(ds, today)
    return a is None or a > FREQ_DAYS.get(ds.frequency, 50)


def period_label(d: date, frequency: str) -> str:
    if frequency == "annual":
        return str(d.year)
    if frequency == "quarterly":
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    if frequency == "monthly":
        return d.strftime("%b %Y")
    return d.strftime("%d %b %Y").lstrip("0")


def summary(ds) -> dict:
    pts = ds.points
    last = pts[-1] if pts else None
    prev = pts[-2] if len(pts) > 1 else None
    change = pct = None
    if last and prev:
        change = last.value - prev.value
        pct = (change / prev.value * 100) if prev.value else None
    return {"last": last, "prev": prev, "change": change, "pct": pct, "stale": is_stale(ds),
            "label": period_label(last.period, ds.frequency) if last else ""}


def chart_svg(ds) -> Markup:
    """Server-rendered line chart: no JavaScript, CSP-safe (styling via CSS classes), screen-reader friendly."""
    pts = ds.points[-MAX_CHART_POINTS:]
    if not pts:
        return Markup("")
    W, H, L, R, T, B = 720, 300, 64, 16, 16, 40
    ys = [p.value for p in pts]
    lo, hi = min(ys), max(ys)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    t0, t1 = pts[0].period.toordinal(), pts[-1].period.toordinal()
    span = max(t1 - t0, 1)

    def x(p):
        return L + (W - L - R) * ((p.period.toordinal() - t0) / span if len(pts) > 1 else 0.5)

    def y(v):
        return T + (H - T - B) * (1 - (v - lo) / (hi - lo))

    d = ds.decimals
    grid = []
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        grid.append(f'<line class="chart-grid" x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                    f'<text class="chart-tick" x="{L - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{escape(fmt(v, d))}</text>')
    xt = []
    idx = sorted({0, len(pts) // 2, len(pts) - 1})
    for i in idx:
        anchor = "start" if i == 0 and len(pts) > 1 else "end" if i == len(pts) - 1 and len(pts) > 1 else "middle"
        xt.append(f'<text class="chart-tick" x="{x(pts[i]):.1f}" y="{H - 14}" text-anchor="{anchor}">'
                  f'{escape(period_label(pts[i].period, ds.frequency))}</text>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(p):.1f},{y(p.value):.1f}" for i, p in enumerate(pts))
    dots = "".join(f'<circle class="chart-dot" cx="{x(p):.1f}" cy="{y(p.value):.1f}" r="{3.2 if len(pts) <= 36 else 2}">'
                   f'<title>{escape(period_label(p.period, ds.frequency))}: {escape(fmt(p.value, d))} {escape(ds.unit)}</title></circle>'
                   for p in pts)
    last = pts[-1]
    desc = (f"Line chart of {ds.title}, {len(pts)} observations from {period_label(pts[0].period, ds.frequency)} to "
            f"{period_label(last.period, ds.frequency)}. Latest value {fmt(last.value, d)} {ds.unit}. Values range from "
            f"{fmt(min(ys), d)} to {fmt(max(ys), d)}. The full data table follows.")
    return Markup(
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-labelledby="chart-t chart-d" preserveAspectRatio="xMidYMid meet">'
        f'<title id="chart-t">{escape(ds.title)}</title><desc id="chart-d">{escape(desc)}</desc>'
        f'{"".join(grid)}<path class="chart-line" d="{path}" fill="none"/>{dots}{"".join(xt)}</svg>')


def csv_text(ds) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["period", "value", "unit", "source", "note"])
    for p in ds.points:
        w.writerow([p.period.isoformat(), p.value, ds.unit, ds.source_name, p.note])
    return out.getvalue()


_PERIOD = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?|-?Q([1-4]))?$", re.I)


def parse_period(raw: str) -> date | None:
    """2026 -> 2026-01-01 · 2026-03 -> 2026-03-01 · 2026-03-15 · 2026-Q2 -> 2026-04-01"""
    m = _PERIOD.match((raw or "").strip())
    if not m:
        return None
    year, month, day, quarter = m.groups()
    try:
        if quarter:
            return date(int(year), (int(quarter) - 1) * 3 + 1, 1)
        return date(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return None


def parse_import(text: str) -> tuple[list[tuple[date, float, str]], list[str]]:
    """Parse pasted CSV lines `period,value[,note]` (header row optional). Returns (rows, errors)."""
    rows, errors = [], []
    for n, line in enumerate(csv.reader(io.StringIO(text or "")), 1):
        if not line or not any(c.strip() for c in line):
            continue
        if n == 1 and line[0].strip().lower() in ("period", "date"):
            continue
        if len(line) < 2:
            errors.append(f"Line {n}: expected period,value.")
            continue
        period = parse_period(line[0])
        try:
            value = float(line[1].replace(" ", "").replace("\u00a0", ""))
        except ValueError:
            value = None
        if not period:
            errors.append(f"Line {n}: unrecognised period “{line[0].strip()}” (use 2026, 2026-03, 2026-Q2 or 2026-03-15).")
        elif value is None or value != value or abs(value) > 1e15:
            errors.append(f"Line {n}: “{line[1].strip()}” is not a valid number (no thousands separators).")
        else:
            rows.append((period, value, (line[2].strip()[:200] if len(line) > 2 else "")))
    return rows, errors


def home_cards(limit: int = 4):
    """Published datasets with data, freshest first, for the homepage."""
    from ..models import Dataset
    cards = [(ds, summary(ds)) for ds in Dataset.query.filter_by(published=True).all() if ds.points]
    cards.sort(key=lambda c: c[1]["last"].period, reverse=True)
    return cards[:limit]
