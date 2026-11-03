"""Transactional email over HTTPS APIs. No SMTP / local mail server."""
import requests
from flask import current_app


def delivery_enabled() -> bool:
    """True only when an email can really be delivered (or the dev console provider is active)."""
    c = current_app.config
    provider = (c["EMAIL_PROVIDER"] or "").lower()
    if provider == "console" and not c["STRICT"]:
        return True
    return provider in ("resend", "postmark") and bool(c["EMAIL_API_KEY"] and c["EMAIL_FROM"])


def send_email(to: str, subject: str, html: str, text: str) -> bool:
    c = current_app.config
    provider = (c["EMAIL_PROVIDER"] or "").lower()
    sender = c["EMAIL_FROM"]
    try:
        if provider == "console" and not c["STRICT"]:
            current_app.logger.info("EMAIL to=%s subject=%s\n%s", to, subject, text)
            return True
        if not (c["EMAIL_API_KEY"] and sender):
            current_app.logger.error("Email not configured (EMAIL_API_KEY / EMAIL_FROM).")
            return False
        if provider == "resend":
            r = requests.post("https://api.resend.com/emails",
                              headers={"Authorization": f"Bearer {c['EMAIL_API_KEY']}"},
                              json={"from": sender, "to": [to], "subject": subject, "html": html, "text": text},
                              timeout=10)
        elif provider == "postmark":
            r = requests.post("https://api.postmarkapp.com/email",
                              headers={"X-Postmark-Server-Token": c["EMAIL_API_KEY"], "Accept": "application/json"},
                              json={"From": sender, "To": to, "Subject": subject, "HtmlBody": html, "TextBody": text},
                              timeout=10)
        else:
            current_app.logger.error("Unknown EMAIL_PROVIDER %r", provider)
            return False
        if r.status_code >= 300:
            current_app.logger.error("Email API error %s: %s", r.status_code, r.text[:300])
            return False
        return True
    except requests.RequestException:
        current_app.logger.exception("Email send failed")
        return False
