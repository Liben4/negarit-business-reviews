"""Validate → optimise → WebP variants → object storage → Media row."""
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select

from ..extensions import db
from ..models import Advertisement, Article, ArticleMedia, Author, Media
from ..storage import UploadError, get_storage

Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
ALLOWED = {"JPEG", "PNG", "WEBP", "GIF"}
WIDTHS = (480, 960, 1600)


def _open(raw: bytes) -> Image.Image:
    try:
        Image.open(io.BytesIO(raw)).verify()
        img = Image.open(io.BytesIO(raw))
        if img.format not in ALLOWED:
            raise UploadError("Only JPEG, PNG, WebP or GIF images are allowed.")
        return img
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise UploadError("File is not a valid image.")


def build_variants(raw: bytes) -> tuple[list[tuple[int, int, bytes]], str, str]:
    """Return ([(width, height, bytes)], ext, content_type). Client-declared type/extension are ignored."""
    img = _open(raw)
    if img.format == "GIF" and getattr(img, "is_animated", False):
        return [(img.width, img.height, raw)], "gif", "image/gif"
    img = ImageOps.exif_transpose(img)  # also drops EXIF/metadata on re-encode
    img.load()
    if img.mode in ("P", "PA", "LA"):
        img = img.convert("RGBA")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    ow, oh = img.size
    top = min(ow, WIDTHS[-1])
    widths = sorted({w for w in WIDTHS if w < top} | {top})
    out = []
    for w in widths:
        im = img if w == ow else img.resize((w, max(1, round(oh * w / ow))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=82, method=4)
        out.append((im.width, im.height, buf.getvalue()))
    return out, "webp", "image/webp"


def _base_name(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename or "image")
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:40] or "image"


def create_media(raw: bytes, filename: str, user=None, alt="", caption="", credit="") -> Media:
    """Caller commits."""
    if not raw:
        raise UploadError("Empty file.")
    variants, ext, ctype = build_variants(raw)
    storage = get_storage()
    name = _base_name(filename)

    def put(v):
        w, _h, data = v
        return w, storage.save(data, ext, ctype, f"{name}-{w}")

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            saved = list(pool.map(put, variants))
    except UploadError:
        raise
    except Exception:  # noqa: BLE001
        current_app.logger.exception("media upload failed")
        raise UploadError("Image storage failed. Try again.")
    largest_w, largest_h, largest_bytes = variants[-1]
    m = Media(filename=(filename or "image")[:255], alt_text=alt[:300], caption=caption[:300], credit=credit[:200],
              url=saved[-1][1][0], variants=json.dumps({str(w): url for w, (url, _k) in saved}),
              storage_keys=json.dumps([k for _w, (_u, k) in saved]), width=largest_w, height=largest_h,
              size_bytes=len(largest_bytes), content_type=ctype, uploaded_by_id=user.id if user else None)
    db.session.add(m)
    db.session.flush()
    return m


def usage(media: Media) -> int:
    """How many places reference this image: featured/gallery/author/ads, images inside article or page bodies, site settings."""
    from sqlalchemy import or_
    from ..models import Page, SiteSetting
    urls = [media.url, *media.variant_map.values()]
    n = db.session.scalar(select(func.count()).select_from(Article).where(Article.featured_media_id == media.id))
    n += db.session.scalar(select(func.count()).select_from(ArticleMedia).where(ArticleMedia.media_id == media.id))
    n += db.session.scalar(select(func.count()).select_from(Author).where(Author.image_id == media.id))
    n += db.session.scalar(select(func.count()).select_from(Advertisement).where(Advertisement.media_id == media.id))
    n += db.session.scalar(select(func.count()).select_from(Article).where(
        or_(*[Article.body_html.contains(u, autoescape=True) for u in urls])))
    n += db.session.scalar(select(func.count()).select_from(Page).where(
        or_(*[Page.body_html.contains(u, autoescape=True) for u in urls])))
    n += db.session.scalar(select(func.count()).select_from(SiteSetting).where(
        or_(*[SiteSetting.value.contains(u, autoescape=True) for u in urls])))
    return n


def unused(limit: int = 200) -> list:
    """Oldest-first images with no references anywhere (safe to delete)."""
    out = []
    for m in db.session.scalars(select(Media).order_by(Media.created_at)):
        if usage(m) == 0:
            out.append(m)
            if len(out) >= limit:
                break
    return out


def delete_media(media: Media, force: bool = False) -> None:
    if not force and usage(media):
        raise UploadError("This image is in use (article, gallery, author or ad). Remove it there first.")
    keys = media.keys
    db.session.delete(media)
    db.session.flush()
    try:
        storage = get_storage()
        for k in keys:
            storage.delete(k)
    except UploadError:
        pass
