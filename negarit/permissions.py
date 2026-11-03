"""Role -> capability matrix. Single source of truth for server-side authorization."""

ROLES = ("super_admin", "admin", "editor", "writer", "contributor")
ROLE_LABELS = {"super_admin": "Super Administrator", "admin": "Administrator", "editor": "Editor",
               "writer": "Journalist / Author", "contributor": "Contributor"}
# Stored role values never change (data safety); only the labels do.

_EDITORIAL = {
    "article.create", "article.edit_own", "article.submit", "article.edit_any", "article.publish",
    "article.delete_any", "article.archive", "taxonomy.manage", "authors.manage", "media.browse", "media.upload",
    "media.delete", "subscribers.manage", "comments.moderate", "analytics.view", "messages.view", "dashboard.full",
    "homepage.manage", "data.manage", "notes.add",
}
PERMISSIONS = {
    "super_admin": {"*"},  # users, roles, settings, configuration, everything
    # Administrator: content, taxonomy, authors, media, pages, ads, SEO, data, homepage, audit logs.
    # NOT users/roles/settings (Super Administrator only).
    "admin": _EDITORIAL | {"pages.manage", "ads.manage", "seo.manage", "logs.view"},
    # Editor: review, edit, approve/publish, homepage sections, data.
    "editor": _EDITORIAL,
    # Journalist / Author: own drafts, submit for review, own-draft deletion, media.
    "writer": {"article.create", "article.edit_own", "article.submit", "article.delete_own_draft", "media.browse",
               "media.upload", "notes.add"},
    # Contributor: restricted: create + submit only (upload only, no library browsing, no deleting).
    "contributor": {"article.create", "article.edit_own", "article.submit", "media.upload", "notes.add"},
}


def has_permission(role: str, perm: str) -> bool:
    granted = PERMISSIONS.get(role, set())
    return "*" in granted or perm in granted


def assignable_roles(actor_role: str) -> tuple:
    """Only a Super Administrator can mint or modify Super Administrators."""
    return ROLES if actor_role == "super_admin" else tuple(r for r in ROLES if r != "super_admin")
