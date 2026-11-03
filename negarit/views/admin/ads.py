from flask import flash, redirect, request, url_for
from markupsafe import Markup

from ...extensions import db
from ...models import AD_PLACEMENTS, Advertisement
from ...security import permission_required
from ...services import activity
from ...utils import to_local, utcnow
from . import bp, render_form, render_list
from .helpers import f, flash_all, parse

PLACEMENTS = dict(AD_PLACEMENTS)


def _state(ad: Advertisement) -> str:
    now = utcnow()
    if not ad.active:
        return "Inactive"
    if ad.start_at and ad.start_at > now:
        return "Scheduled"
    if ad.end_at and ad.end_at <= now:
        return "Expired"
    return "Running"


def _fields(ad: Advertisement):
    return [
        f("title", "Internal name / image alt text", value=ad.title, required=True, maxlength=160),
        f("advertiser", "Advertiser", value=ad.advertiser, required=True, maxlength=160),
        f("campaign", "Campaign name", value=ad.campaign, maxlength=160, help="Groups ads from the same booking, e.g. “Q4 Banking Launch”."),
        f("placement", "Placement", "select", ad.placement or "banner", options=AD_PLACEMENTS, required=True,
          help="Banner: top of pages · Sidebar · Inside articles · Homepage · Sponsored placement (text + image card)."),
        f("media_id", "Banner / image", "media", ad.media_id, preview=ad.media.thumb_url if ad.media else None,
          help="Recommended: banner 970×250 or 728×90; sidebar 300×250."),
        f("headline", "Headline (sponsored placements)", value=ad.headline, maxlength=200),
        f("dest_url", "Destination URL", "url", ad.dest_url, required=True, maxlength=600),
        f("start_at", "Start (Addis Ababa time, optional)", "datetime", ad.start_at),
        f("end_at", "End (Addis Ababa time, optional)", "datetime", ad.end_at),
        f("active", "Active", "bool", ad.active if ad.id else True),
    ]


@bp.get("/ads")
@permission_required("ads.manage")
def ads():
    rows = []
    for ad in Advertisement.query.order_by(Advertisement.created_at.desc()):
        ctr = f"{ad.clicks / ad.impressions * 100:.2f}%" if ad.impressions else "-"
        rows.append({"cells": [ad.title, ad.advertiser + (f" / {ad.campaign}" if ad.campaign else ""),
                               PLACEMENTS.get(ad.placement, ad.placement), _state(ad),
                               to_local(ad.start_at).strftime("%b %d") if ad.start_at else "-",
                               to_local(ad.end_at).strftime("%b %d") if ad.end_at else "-", f"{ad.impressions:,}", f"{ad.clicks:,}", ctr],
                     "link": url_for("admin.ad_edit", id=ad.id)})
    return render_list("Advertisements", rows, ["Name", "Advertiser / campaign", "Placement", "State", "Starts", "Ends", "Impressions", "Clicks", "CTR"],
                       new_url=url_for("admin.ad_edit", id=0), new_label="New advertisement",
                       note="Ads are always labelled “Advertisement” or “Sponsored” and open with rel=sponsored. Impressions count page loads that rendered the ad (bots excluded); they are not viewability. Figures start at zero and are never estimated.")


@bp.route("/ads/<int:id>", methods=["GET", "POST"])
@permission_required("ads.manage")
def ad_edit(id):
    ad = db.get_or_404(Advertisement, id) if id else Advertisement()
    fields = _fields(ad)
    if request.method == "POST":
        vals, errors = parse(fields)
        if vals["start_at"] and vals["end_at"] and vals["end_at"] <= vals["start_at"]:
            errors.append("The end date must be after the start date.")
        if vals["placement"] not in PLACEMENTS:
            errors.append("Choose a placement.")
        if vals["placement"] != "sponsored" and not vals["media_id"]:
            errors.append("Choose a banner image (only sponsored placements can be text-only).")
        if not errors:
            for k in ("title", "advertiser", "campaign", "placement", "headline", "dest_url", "start_at", "end_at", "active"):
                setattr(ad, k, vals[k])
            ad.media_id = vals["media_id"]
            db.session.add(ad)
            db.session.flush()
            activity.log("ad_save", "ad", ad.id, f"{ad.advertiser}: {ad.title}")
            db.session.commit()
            flash("Advertisement saved.", "ok")
            return redirect(url_for("admin.ads"))
        flash_all(errors)
        for fld in fields:
            fld["value"] = vals.get(fld["name"], fld["value"])
    extra = [("Delete", url_for("admin.ad_delete", id=ad.id), "Delete this advertisement?", True)] if id else []
    return render_form("Edit advertisement" if id else "New advertisement", fields, back=url_for("admin.ads"), extra_actions=extra)


@bp.post("/ads/<int:id>/delete")
@permission_required("ads.manage")
def ad_delete(id):
    ad = db.get_or_404(Advertisement, id)
    activity.log("ad_delete", "ad", ad.id, ad.title)
    db.session.delete(ad)
    db.session.commit()
    flash("Advertisement deleted.", "ok")
    return redirect(url_for("admin.ads"))
