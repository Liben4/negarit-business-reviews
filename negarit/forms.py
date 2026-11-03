"""Public-facing forms (CSRF is off: these pages are CDN-cached or protected by honeypot + rate limits)."""
from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional

from .models import INQUIRY_TYPES


class _Base(FlaskForm):
    class Meta:
        csrf = False

    website = StringField("Website")  # honeypot: real users never see or fill this


class ContactForm(_Base):
    name = StringField("Your name", [DataRequired(), Length(max=120)])
    email = StringField("Email", [DataRequired(), Email(check_deliverability=False), Length(max=255)])
    inquiry_type = SelectField("Inquiry type", choices=INQUIRY_TYPES, default="general")
    subject = StringField("Subject", [DataRequired(), Length(max=200)])
    message = TextAreaField("Message", [DataRequired(), Length(min=10, max=5000)])
    ts = StringField("ts")


class CommentForm(_Base):
    name = StringField("Name", [DataRequired(), Length(max=80)])
    email = StringField("Email (never published)", [Optional(), Email(check_deliverability=False), Length(max=255)])
    body = TextAreaField("Comment", [DataRequired(), Length(min=3, max=2000)])
