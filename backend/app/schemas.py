"""Pydantic request/response models (API contract)."""

from __future__ import annotations

import re
from ipaddress import ip_address
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from .teams import slugify as _slugify_name

ROLES = ("admin", "user")
URL_TYPES = ("url", "alias", "embedded")
APPROVAL_STATES = ("pending", "approved", "rejected")
# Static (always-available) bundle mapping sources. Server-template-scoped
# sources are dynamic (``server_<slug>_{name,ip,user}``) and are validated
# separately against the live server templates at save time.
BUNDLE_MAPPING_SOURCES = (
    "username",
    "user_id",
    "user_apps_server",
    "user_apps_server_host",
    "user_apps_server_ip",
    "user_role",
)

# A server-template-scoped mapping source names a server template by its slug:
# ``server_<slug>_name`` / ``_ip`` / ``_user``. It resolves to the user's first
# server created from that template.
SERVER_VAR_FIELDS = ("name", "ip", "user")
SERVER_VAR_SOURCE_RE = re.compile(
    r"^server_(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)_(?P<field>name|ip|user)$"
)


def is_server_var_source(source: str) -> bool:
    """True when ``source`` matches the ``server_<slug>_{name,ip,user}`` form."""
    return SERVER_VAR_SOURCE_RE.match(source) is not None


def server_var_source_slug(source: str) -> str | None:
    """The template slug named by a server-var source, or None if not one."""
    match = SERVER_VAR_SOURCE_RE.match(source)
    return match.group("slug") if match else None

# A local alias becomes part of a URL path, so it is restricted to URL-safe
# characters: letters, digits, underscores, and dashes, with a hard length cap.
# This keeps it safe to substitute into a reverse-proxy location and link as a
# bare relative path (no scheme, host, traversal, or separators possible).
ALIAS_MAX_LEN = 30
_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Reserved alias slugs that would collide with AppManager's own served paths.
# An alias is proxied by nginx at /<slug>/, and embedded apps frame that path
# same-origin; forbidding these names guarantees an alias can never resolve to
# AppManager's SPA/API instead of the intended upstream service.
_RESERVED_ALIASES = frozenset(
    {"api", "assets", "docs", "redoc", "openapi", "logos", "team-icons", "embedded"}
)

# A bare hostname or IPv4 address used as the user's apps server. Restricted to
# DNS/IP characters so it can be safely substituted into an nginx proxy_pass and
# never used for command/config injection.
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_OS_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_APPS_PROTOCOLS = ("http", "https")
_APPS_PATH_RE = re.compile(r"^$|^/[A-Za-z0-9._~/-]*$")

# An optional SSH login user (ssh user@host). Restricted so it cannot inject a
# host or shell content when composed into "user@host" / used as an argv element.
_SSH_USER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_email_username(value: str) -> str:
    """Validate and canonicalize a sign-in username (must be an email).

    Trims whitespace and lowercases: sign-in usernames are matched
    case-insensitively everywhere (local login, SSO, and this normalization),
    so storing a canonical lowercase form keeps display and comparisons
    consistent.
    """
    value = value.strip()
    if not value:
        raise ValueError("Username must not be empty.")
    if not _EMAIL_RE.match(value):
        raise ValueError("Username must be an email address.")
    return value.lower()


def _validate_apps_server(value: str) -> str:
    """An optional bare hostname where a user runs their applications."""
    value = value.strip()
    if not value:
        return ""
    if len(value) > 253 or not _HOST_RE.match(value):
        raise ValueError(
            "Apps server host must be a bare hostname (letters, digits, '.', '-')."
        )
    return value


def _validate_apps_server_ip(value: str) -> str:
    """An optional IPv4/IPv6 address where a user runs their applications."""
    value = value.strip()
    if not value:
        return ""
    try:
        return str(ip_address(value))
    except ValueError as exc:
        raise ValueError("Apps server IP must be a valid IPv4 or IPv6 address.") from exc


def _validate_apps_port(value: str) -> str:
    """An optional TCP port (1-65535) as a string."""
    value = value.strip()
    if not value:
        return ""
    if not value.isdigit() or not (1 <= int(value) <= 65535):
        raise ValueError("Apps port must be a number between 1 and 65535.")
    return value


def _validate_apps_protocol(value: str) -> str:
    value = value.strip().lower()
    if value not in _APPS_PROTOCOLS:
        raise ValueError("Apps protocol must be either 'http' or 'https'.")
    return value


def _validate_apps_path(value: str) -> str:
    value = value.strip()
    if value and not value.startswith("/"):
        value = f"/{value}"
    if not _APPS_PATH_RE.match(value):
        raise ValueError(
            "Apps path may contain only URL path characters and must not include "
            "spaces, query strings, or fragments."
        )
    return value


def _validate_http_url(value: str) -> str:
    """Require an absolute http(s) URL; reject other schemes (e.g. javascript:)."""
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must be an absolute http(s) URL.")
    return value


# An uploaded logo is carried inline as a small base64 data URI. Only raster
# images are accepted (SVG is excluded to avoid script-in-SVG vectors); the
# bundled default-logo catalogue is served as static files, never as user
# input. The decoded image is capped to keep request bodies and the stored
# value small.
MAX_ICON_DATA_BYTES = 64 * 1024
# base64 encodes 3 bytes as 4 chars; allow the prefix plus a little slack.
ICON_FIELD_MAX_LEN = (MAX_ICON_DATA_BYTES * 4 // 3) + 64
_ICON_DATA_RE = re.compile(
    r"^data:image/(?:png|webp);base64,(?P<b64>[A-Za-z0-9+/]+={0,2})$"
)
# A relative reference to a bundled default-logo asset (see the frontend
# `defaultLogoFor`/`public/logos`). Stored verbatim and resolved against the
# deployment base at render time. The shape is intentionally narrow -- a lower
# case catalogue name plus a 1-3 variant suffix under `logos/` -- so this is not
# a general relative-path field: no scheme, no parent traversal, no leading or
# doubled slash can match.
_ICON_PATH_RE = re.compile(r"^logos/[a-z0-9]+(?:-[a-z0-9]+)*-[1-3]\.svg$")

# A relative reference to a bundled team-icon catalogue asset (see the frontend
# `public/team-icons`). Same narrow shape as the logo catalogue: a lower-case
# name under `team-icons/`, no scheme, traversal, or extra slashes.
_TEAM_ICON_PATH_RE = re.compile(r"^team-icons/[a-z0-9]+(?:-[a-z0-9]+)*\.svg$")


def _validate_icon_value(value: str) -> str:
    """Accept an empty value, a bundled relative logo path, an absolute http(s)
    URL, or a capped raster data URI."""
    value = value.strip()
    if not value:
        return ""
    if value.startswith("data:"):
        match = _ICON_DATA_RE.match(value)
        if match is None:
            raise ValueError(
                "Inline logo must be a base64 data URI of type image/png or "
                "image/webp."
            )
        b64 = match.group("b64")
        # 4 base64 chars decode to 3 bytes (minus padding); estimate the size
        # without decoding the whole payload.
        padding = b64.count("=")
        decoded_bytes = (len(b64) // 4) * 3 - padding
        if decoded_bytes > MAX_ICON_DATA_BYTES:
            raise ValueError(
                f"Inline logo must be at most {MAX_ICON_DATA_BYTES} bytes."
            )
        return value
    if _ICON_PATH_RE.match(value):
        # A bundled default-logo asset path (relative, resolved at render time).
        return value
    return _validate_http_url(value)


# Team names are admin-defined and also generate the team's URL slug. Allow a
# friendly character set (letters, digits, spaces, '&', '-') so names like
# "Forensics & BID" remain expressible, with a hard length cap. Uniqueness and
# slug-collision checks happen in the repository against the live team set.
TEAM_NAME_MAX_LEN = 40
_TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &-]*$")


def _validate_team_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Team name must not be empty.")
    if len(value) > TEAM_NAME_MAX_LEN:
        raise ValueError(
            f"Team name must be at most {TEAM_NAME_MAX_LEN} characters."
        )
    if not _TEAM_NAME_RE.match(value):
        raise ValueError(
            "Team name may contain only letters, digits, spaces, '&', and '-'."
        )
    # The name must yield a non-empty URL slug (e.g. "&&&" or "---" is invalid).
    if not _slugify_name(value):
        raise ValueError("Team name must contain at least one letter or digit.")
    return value


def _validate_team_icon(value: str) -> str:
    """Accept an empty value, a bundled team-icon path, an absolute http(s)
    URL, or a capped raster data URI (same raster policy as app logos)."""
    value = value.strip()
    if not value:
        return ""
    if value.startswith("data:"):
        return _validate_icon_value(value)
    if _TEAM_ICON_PATH_RE.match(value):
        return value
    return _validate_http_url(value)


def _validate_alias(value: str) -> str:
    """Validate a local alias: a bare relative path the portal links to.

    The alias is rendered as a link relative to the deployment base URL and is
    resolved by an upstream reverse proxy. To keep it URL-safe and prevent open
    redirects or injection it must be letters, digits, underscores, and dashes
    only, at most ``ALIAS_MAX_LEN`` characters. A single leading slash is
    accepted and stripped (so ``/grafana`` and ``grafana`` are equivalent).
    """
    value = value.strip().lstrip("/")
    if not value:
        raise ValueError("Alias must not be empty.")
    if len(value) > ALIAS_MAX_LEN:
        raise ValueError(
            f"Alias must be at most {ALIAS_MAX_LEN} characters."
        )
    if not _ALIAS_RE.match(value):
        raise ValueError(
            "Alias may contain only letters, digits, underscores, and dashes "
            "(e.g. my_app)."
        )
    if value.lower() in _RESERVED_ALIASES:
        raise ValueError(
            f"Alias {value!r} is reserved; choose a different name."
        )
    return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)
    confirm_password: str = Field(min_length=1, max_length=1024)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    role: str = "user"
    teams: list[str] = Field(default_factory=list)
    self_service: bool = False
    apps_server: str = Field(default="", max_length=253)
    apps_server_ip: str = Field(default="", max_length=45)
    # Server template IDs to auto-provision a server for on creation. Defaults
    # to none; the admin UI pre-selects every template. Each provision is
    # best-effort: a failure never blocks user creation. Capped and
    # de-duplicated to bound the synchronous provisioning work per request.
    provision_templates: list[int] = Field(default_factory=list, max_length=50)

    @field_validator("provision_templates")
    @classmethod
    def _dedupe_provision_templates(cls, value: list[int]) -> list[int]:
        seen: set[int] = set()
        ordered: list[int] = []
        for template_id in value:
            if template_id not in seen:
                seen.add(template_id)
                ordered.append(template_id)
        return ordered

    @field_validator("username")
    @classmethod
    def _clean_username(cls, value: str) -> str:
        return _clean_email_username(value)

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}.")
        return value

    @field_validator("apps_server")
    @classmethod
    def _check_apps_server(cls, value: str) -> str:
        return _validate_apps_server(value)

    @field_validator("apps_server_ip")
    @classmethod
    def _check_apps_server_ip(cls, value: str) -> str:
        return _validate_apps_server_ip(value)

    # issue_017: an apps-server location is no longer mandatory. Accounts may
    # exist solely to view applications; a custom apps server is only required
    # to create applications, which the UI warns about at creation time.


class UpdateUserRequest(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=128)
    role: str | None = None
    teams: list[str] | None = None
    is_active: bool | None = None
    self_service: bool | None = None
    apps_server: str | None = Field(default=None, max_length=253)
    apps_server_ip: str | None = Field(default=None, max_length=45)

    @field_validator("username")
    @classmethod
    def _clean_username(cls, value: str | None) -> str | None:
        return None if value is None else _clean_email_username(value)

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str | None) -> str | None:
        if value is not None and value not in ROLES:
            raise ValueError(f"Role must be one of {ROLES}.")
        return value

    @field_validator("apps_server")
    @classmethod
    def _check_apps_server(cls, value: str | None) -> str | None:
        return None if value is None else _validate_apps_server(value)

    @field_validator("apps_server_ip")
    @classmethod
    def _check_apps_server_ip(cls, value: str | None) -> str | None:
        return None if value is None else _validate_apps_server_ip(value)


class UserOut(BaseModel):
    id: int
    username: str
    # Derived human-facing identifier: email local part with dots/underscores
    # replaced by dashes (e.g. ``john.doe@example.com`` -> ``john-doe``).
    user_id: str = ""
    role: str
    is_active: bool
    must_change_password: bool
    self_service: bool
    apps_server: str = ""
    apps_server_ip: str = ""
    # issue_020: the user's own UI theme ("" = follow the deployment default).
    theme: str = ""
    teams: list[str]


class SshKeyInfoOut(BaseModel):
    """Public half of the account's SSH keypair. Never carries the private key."""

    user_id: str
    public_key: str
    generated_at: str | None = None


class ServerKeyRotationOut(BaseModel):
    """Per-server outcome of propagating a regenerated key."""

    server: str
    ip_address: str = ""
    status: str  # updated | skipped | failed
    detail: str = ""


class SshKeyRegenerateOut(SshKeyInfoOut):
    """Regeneration result plus the per-server key-rotation summary."""

    rotation: list[ServerKeyRotationOut] = Field(default_factory=list)


class SessionOut(BaseModel):
    authenticated: bool
    enable_auth: bool
    user: UserOut | None = None
    csrf_token: str | None = None
    # How the current session was established. Local password sessions still
    # enforce local password reset; SSO sessions do not.
    auth_method: str = "local"
    # Configurable branding, readable pre-authentication so the login page can
    # render the deployment's own name and logo.
    app_name: str = ""
    app_logo: str = ""
    # Admin-managed About-page collaborators (distinct from the git development
    # team); delivered with the session so the About page can render them.
    collaborators: list[str] = Field(default_factory=list)
    # issue_019: admin-selected default UI theme (fallback when a user has not
    # chosen their own), delivered pre-auth so the client can apply it.
    default_theme: str = "dark-modern"
    # One-time setup flag that drives the first-login wizard (admins only).
    configured: bool = False


class SsoProviderOut(BaseModel):
    protocol: str
    label: str
    login_url: str


class SsoConfigOut(BaseModel):
    enabled: bool
    local_login_enabled: bool
    providers: list[SsoProviderOut] = Field(default_factory=list)


class BundleTemplateMapping(BaseModel):
    field_name: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=80)

    @field_validator("field_name")
    @classmethod
    def _clean_field_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field name must not be empty.")
        return value

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        value = value.strip()
        if value in BUNDLE_MAPPING_SOURCES or is_server_var_source(value):
            return value
        raise ValueError(
            "source must be one of "
            f"{BUNDLE_MAPPING_SOURCES} or a server template variable "
            "(server_<template-slug>_name/_ip/_user)."
        )


class BundleTemplateOut(BaseModel):
    id: int
    name: str
    content: str
    description: str = ""
    mappings: list[BundleTemplateMapping] = Field(default_factory=list)
    is_builtin: bool = False
    enabled: bool = True
    # Response-only: a read-only, generic (no real topology/secrets) preview of
    # what the built-in template actually downloads, since its stored
    # ``content`` above is only a marker comment. Empty for custom templates,
    # whose ``content`` is already the real definition.
    definition: str = ""


class CloneBundleTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be empty.")
        return value


class SetBundleTemplateEnabledRequest(BaseModel):
    enabled: bool


class CreateBundleTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20000)
    description: str = Field(default="", max_length=500)
    mappings: list[BundleTemplateMapping] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be empty.")
        return value


class UpdateBundleTemplateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    description: str | None = Field(default=None, max_length=500)
    mappings: list[BundleTemplateMapping] | None = None

    @field_validator("name")
    @classmethod
    def _clean_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Name must not be empty.")
        return value


class BundleOptionOut(BaseModel):
    id: int
    name: str
    description: str = ""


class ApplicationShareUserOut(BaseModel):
    id: int
    username: str
    user_id: str


class ApplicationOut(BaseModel):
    id: int
    name: str
    description: str
    url: str
    url_type: str = "url"
    icon_url: str
    teams: list[str]
    is_active: bool
    approval_status: str = "approved"
    sort_order: int
    # Creating user's name, shown as publisher metadata in listings.
    created_by: str | None = None
    created_by_id: int | None = None
    # First team assigned to the publisher, in configured team order. This is
    # display metadata and is independent from shared visibility teams.
    publisher_team: str = ""
    # Reverse-proxy push result; populated only in management/own-app responses.
    last_push_status: str | None = None
    last_push_log: str = ""
    last_push_at: str | None = None
    # Per-app upstream settings (alias apps); management/own-app responses only.
    apps_server: str = ""
    apps_protocol: str = "http"
    apps_port: str = ""
    apps_path: str = ""
    # Whether the alias block requires an AppManager session before proxying.
    alias_auth_required: bool = True
    # Opt-in "rewrite root paths" mode for apps that assume they run at '/'.
    apps_rewrite_root: bool = False
    # Opt-in: forward the authenticated portal user's canonical stored
    # username/email upstream as the fixed ``X-AppManager-User`` header.
    # Only ever emitted for an alias whose nginx auth_request already
    # validated the session; never for public aliases or auth-disabled mode.
    pass_authenticated_user: bool = False
    # A staged alias change awaiting approval (management/own-app responses
    # only). Empty unless the owner edited the alias and it is pending review.
    pending_alias: str = ""
    pending_is_active: bool | None = None
    pending_alias_auth_required: bool | None = None
    pending_apps_rewrite_root: bool | None = None
    pending_pass_authenticated_user: bool | None = None
    needs_push: bool = False
    is_favorite: bool = False
    visits_7d: int | None = None
    show_statistics: bool = False
    is_private: bool = False
    shared_users: list[ApplicationShareUserOut] = Field(default_factory=list)


class ApplicationStatisticsSettingsOut(BaseModel):
    show_app_statistics: bool = False


class ApplicationStatisticsSettingsUpdate(BaseModel):
    show_app_statistics: bool


class ApplicationTrendPoint(BaseModel):
    date: str
    launches: int
    unique_users: int


class ApplicationStatisticsRow(BaseModel):
    application_id: int
    name: str
    launches: int
    unique_users: int
    favorites: int
    visits_7d: int
    # Authorized alias visits (issue_local_031): counted from nginx's
    # app-aware auth_request for the *original* alias request (direct
    # navigation, deep links, and embedded iframes included), separately from
    # the "launches" above (which only count a card click in the portal).
    # Anonymous visits (public aliases, or auth-disabled deployments) count
    # toward this total but never toward unique_alias_users.
    alias_visits: int = 0
    unique_alias_users: int = 0
    anonymous_alias_visits: int = 0


class ApplicationTrendSeries(BaseModel):
    application_id: int
    name: str
    launches: int
    points: list[ApplicationTrendPoint]


class ApplicationUserActivityOut(BaseModel):
    user_id: str
    launches: int
    active_days: int
    last_activity: str


class ApplicationFavoriteUserOut(BaseModel):
    user_id: str
    starred_at: str


class ApplicationStatisticsDetailOut(BaseModel):
    application_id: int
    activity_users: list[ApplicationUserActivityOut]
    favorite_users: list[ApplicationFavoriteUserOut]


class UserActivityRow(BaseModel):
    user_id: str
    launches: int
    applications_used: int


class LaunchUserRow(BaseModel):
    """issue_local_032: one row of the complete (uncapped) "Launch users" drill-down."""

    user_id: str
    launches: int
    applications_used: int
    active_days: int
    last_activity: str


class FavoriteEntryRow(BaseModel):
    """issue_local_032: one row of the complete "Favorites" drill-down: a
    single user's favorite of a single application."""

    application_id: int
    application_name: str
    user_id: str
    starred_at: str


class AliasUserApplicationRow(BaseModel):
    """issue_local_032 (follow-up): one application's contribution to an
    authenticated alias user's total, for the expandable per-user breakdown."""

    application_id: int
    application_name: str
    alias_visits: int


class AliasUserRow(BaseModel):
    """issue_local_032: one row of the complete "Alias visits" drill-down for
    authenticated visitors (anonymous traffic is never attributable to a
    user, so it never appears here -- see anonymous_alias_visits instead)."""

    user_id: str
    alias_visits: int
    applications_visited: int
    active_days: int
    last_visit: str
    # issue_local_032 (follow-up): per-application breakdown behind an
    # expandable row, ordered by visits descending then name/id.
    applications: list[AliasUserApplicationRow] = Field(default_factory=list)


class ApplicationStatisticsOut(BaseModel):
    days: int
    launches: int
    unique_users: int
    favorites: int
    trend: list[ApplicationTrendPoint]
    applications: list[ApplicationStatisticsRow]
    app_trends: list[ApplicationTrendSeries]
    user_activity: list[UserActivityRow]
    # Overall authorized-alias-visit totals over the same date range; see
    # ApplicationStatisticsRow for the metric's exact meaning and privacy
    # notes. Zero for a deployment with no alias traffic yet.
    alias_visits: int = 0
    unique_alias_users: int = 0
    anonymous_alias_visits: int = 0
    # issue_local_032: complete (uncapped) drill-down lists for the
    # corresponding clickable KPI card, over the same date range as the rest
    # of this response (except favorite_entries, which is always a current
    # snapshot -- see FavoriteEntryRow).
    launch_users: list[LaunchUserRow] = Field(default_factory=list)
    favorite_entries: list[FavoriteEntryRow] = Field(default_factory=list)
    alias_users: list[AliasUserRow] = Field(default_factory=list)


class AliasConfigOut(BaseModel):
    status: str
    log: str = ""
    alias: str = ""
    apps_protocol: str = "http"
    apps_server: str = ""
    apps_port: str = ""
    apps_path: str = ""
    alias_auth_required: bool = True
    apps_rewrite_root: bool = False
    pass_authenticated_user: bool = False


class CreateApplicationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=2048)
    url_type: str = "url"
    description: str = Field(default="", max_length=512)
    icon_url: str = Field(default="", max_length=ICON_FIELD_MAX_LEN)
    teams: list[str] = Field(default_factory=list)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=100000)
    # Optional per-app apps server/port (admins only -- enforced in the router).
    # Used to render the reverse-proxy alias when the owner has none.
    apps_server: str = Field(default="", max_length=253)
    apps_protocol: str = "http"
    apps_port: str = Field(default="", max_length=5)
    apps_path: str = Field(default="", max_length=256)
    alias_auth_required: bool = True
    apps_rewrite_root: bool = False
    pass_authenticated_user: bool = False
    is_private: bool = False
    shared_user_ids: list[int] = Field(default_factory=list, max_length=50)

    @field_validator("shared_user_ids")
    @classmethod
    def _check_shared_user_ids(cls, value: list[int]) -> list[int]:
        if any(user_id <= 0 for user_id in value):
            raise ValueError("shared_user_ids must contain positive IDs")
        return value

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be empty.")
        return value

    @field_validator("url_type")
    @classmethod
    def _check_url_type(cls, value: str) -> str:
        if value not in URL_TYPES:
            raise ValueError(f"url_type must be one of {URL_TYPES}.")
        return value

    @field_validator("apps_server")
    @classmethod
    def _check_apps_server(cls, value: str) -> str:
        return _validate_apps_server(value)

    @field_validator("apps_protocol")
    @classmethod
    def _check_apps_protocol(cls, value: str) -> str:
        return _validate_apps_protocol(value)

    @field_validator("apps_port")
    @classmethod
    def _check_apps_port(cls, value: str) -> str:
        return _validate_apps_port(value)

    @field_validator("apps_path")
    @classmethod
    def _check_apps_path(cls, value: str) -> str:
        return _validate_apps_path(value)

    @field_validator("icon_url")
    @classmethod
    def _check_icon_url(cls, value: str) -> str:
        return _validate_icon_value(value)

    @model_validator(mode="after")
    def _check_url(self) -> "CreateApplicationRequest":
        # Validation of ``url`` depends on ``url_type``:
        #  - 'alias'    : a bare relative alias slug.
        #  - 'embedded' : the SAME kind of alias slug -- an embedded app frames
        #                 an existing managed alias (the only source reachable by
        #                 external users and free of mixed-content), so its url is
        #                 an alias reference, not a raw http(s) URL.
        #  - 'url'      : a full external http(s) URL.
        if self.url_type in ("alias", "embedded"):
            self.url = _validate_alias(self.url)
        else:
            self.url = _validate_http_url(self.url)
        # Private/user-restricted apps must be AppManager-mediated: a managed
        # alias, or an embedded app (which is only reachable from the in-portal
        # sidebar behind the login).
        if self.is_private and self.url_type not in ("alias", "embedded"):
            raise ValueError(
                "Private applications must use a managed alias or embedded app."
            )
        return self


class UpdateApplicationRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    url_type: str | None = None
    description: str | None = Field(default=None, max_length=512)
    icon_url: str | None = Field(default=None, max_length=ICON_FIELD_MAX_LEN)
    teams: list[str] | None = None
    is_active: bool | None = None
    approval_status: str | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    apps_server: str | None = Field(default=None, max_length=253)
    apps_protocol: str | None = None
    apps_port: str | None = Field(default=None, max_length=5)
    apps_path: str | None = Field(default=None, max_length=256)
    alias_auth_required: bool | None = None
    apps_rewrite_root: bool | None = None
    pass_authenticated_user: bool | None = None
    created_by: int | None = None
    is_private: bool | None = None
    shared_user_ids: list[int] | None = Field(default=None, max_length=50)

    @field_validator("shared_user_ids")
    @classmethod
    def _check_shared_user_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(user_id <= 0 for user_id in value):
            raise ValueError("shared_user_ids must contain positive IDs")
        return value

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Name must not be empty.")
        return value

    @field_validator("url_type")
    @classmethod
    def _check_url_type(cls, value: str | None) -> str | None:
        if value is not None and value not in URL_TYPES:
            raise ValueError(f"url_type must be one of {URL_TYPES}.")
        return value

    @field_validator("apps_server")
    @classmethod
    def _check_apps_server(cls, value: str | None) -> str | None:
        return None if value is None else _validate_apps_server(value)

    @field_validator("apps_protocol")
    @classmethod
    def _check_apps_protocol(cls, value: str | None) -> str | None:
        return None if value is None else _validate_apps_protocol(value)

    @field_validator("apps_port")
    @classmethod
    def _check_apps_port(cls, value: str | None) -> str | None:
        return None if value is None else _validate_apps_port(value)

    @field_validator("apps_path")
    @classmethod
    def _check_apps_path(cls, value: str | None) -> str | None:
        return None if value is None else _validate_apps_path(value)

    @field_validator("approval_status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is not None and value not in APPROVAL_STATES:
            raise ValueError(f"approval_status must be one of {APPROVAL_STATES}.")
        return value

    @field_validator("icon_url")
    @classmethod
    def _check_icon_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_icon_value(value)

    @model_validator(mode="after")
    def _check_url(self) -> "UpdateApplicationRequest":
        if self.url is not None:
            # 'alias'/'embedded' both carry an alias slug; 'url' carries a full
            # http(s) URL. When url_type is OMITTED on update we cannot know the
            # stored type at the schema layer, so we accept either shape here and
            # defer strict, type-aware validation to the router (which knows the
            # existing url_type and re-validates: alias/embedded via the alias
            # rules, url via the http rules). This avoids a spurious 422 when an
            # API client repoints an embedded app's slug without resending
            # url_type.
            if self.url_type in ("alias", "embedded"):
                self.url = _validate_alias(self.url)
            elif self.url_type == "url":
                self.url = _validate_http_url(self.url)
            else:
                # url_type omitted: accept either an alias slug or an http URL;
                # the router enforces the correct one for the stored type.
                try:
                    self.url = _validate_alias(self.url)
                except ValueError:
                    self.url = _validate_http_url(self.url)
        return self


class ApplicationReorderGroup(BaseModel):
    """One reordered bucket (issue_local_032): all applications in a single
    request must be the caller's own applications (or, for an admin, any
    applications) that currently share the same approval_status -- reordering
    is scoped to what is visibly grouped together in the UI (ownership +
    review state), never crossing those boundaries."""

    application_ids: list[int] = Field(min_length=1, max_length=500)
    expected_application_ids: list[int] = Field(min_length=1, max_length=500)

    @field_validator("application_ids", "expected_application_ids")
    @classmethod
    def _check_unique_positive(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("Application IDs must not repeat within a group.")
        if any(v <= 0 for v in value):
            raise ValueError("Application IDs must be positive.")
        return value

    @model_validator(mode="after")
    def _check_same_set(self) -> "ApplicationReorderGroup":
        if set(self.application_ids) != set(self.expected_application_ids):
            raise ValueError(
                "application_ids and expected_application_ids must contain the "
                "same set of IDs (only their order may differ)."
            )
        return self


class ReorderApplicationsRequest(BaseModel):
    groups: list[ApplicationReorderGroup] = Field(min_length=1, max_length=20)


class ProvisionResultOut(BaseModel):
    """Per-template outcome of create-user auto-provisioning."""

    template_id: int
    template_name: str
    status: str  # "created" | "failed" | "skipped"
    detail: str = ""


class GeneratedPasswordOut(BaseModel):
    user: UserOut
    password: str
    # Present only for create-user; per-template auto-provisioning outcomes.
    provisioning: list[ProvisionResultOut] = Field(default_factory=list)


class AuditEntryOut(BaseModel):
    id: int
    created_at: str
    category: str
    action: str
    actor_username: str | None = None
    target_type: str | None = None
    target_id: int | None = None
    target_name: str | None = None
    detail: str = ""


class RecordNavigationRequest(BaseModel):
    """issue_local_032: only a semantic destination key is ever accepted --
    never a raw path, query string, or fragment. See
    repository.NAVIGATION_DESTINATIONS for the exact allowlist."""

    destination: str = Field(min_length=1, max_length=64)

    @field_validator("destination")
    @classmethod
    def _check_destination(cls, value: str) -> str:
        from . import repository  # local import: avoid any import-time cycle

        if value not in repository.NAVIGATION_DESTINATIONS:
            raise ValueError("destination must be one of the known navigation sections.")
        return value


class NavigationActivityOut(BaseModel):
    id: int
    actor_username: str
    destination: str
    first_seen_at: str
    last_seen_at: str
    visit_count: int


class NavigationActivityPageOut(BaseModel):
    """issue_local_032 (follow-up): a bounded page of navigation activity.

    ``total`` is capped at the newest ``NAVIGATION_ACTIVITY_MAX_EVENTS`` stored
    rows (each row is one deduplicated 5-minute bucket, not a raw visit
    count), even if retention has not yet swept older rows.
    """

    items: list[NavigationActivityOut]
    total: int
    offset: int
    limit: int


# Reject shell metacharacters / whitespace tricks in path-like settings so a
# value can never be abused when later passed (as an argv element) to ssh/scp.
_UNSAFE_PATH_CHARS = set(";|&`$<>\n\r\"'\\ ")


def _validate_path_setting(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if any(ch in _UNSAFE_PATH_CHARS for ch in value):
        raise ValueError(f"{label} must not contain spaces or shell metacharacters.")
    return value


class ReverseProxySettingsOut(BaseModel):
    nginx_host: str = ""
    nginx_user: str = ""
    nginx_conf_path: str = ""
    ssh_key_path: str = ""
    # Registry key selected for reverse-proxy SSH (replaces the raw path in UI).
    reverse_proxy_ssh_key_id: int | None = None
    appmanager_proxy_host: str = ""
    appmanager_proxy_port: str = ""
    alias_template: str = ""
    protected_alias_auth_status: str = ""
    protected_alias_auth_log: str = ""


class UpdateReverseProxySettingsRequest(BaseModel):
    nginx_host: str | None = Field(default=None, max_length=253)
    nginx_user: str | None = Field(default=None, max_length=64)
    nginx_conf_path: str | None = Field(default=None, max_length=4096)
    ssh_key_path: str | None = Field(default=None, max_length=4096)
    reverse_proxy_ssh_key_id: int | None = Field(default=None, ge=1)
    appmanager_proxy_host: str | None = Field(default=None, max_length=253)
    appmanager_proxy_port: str | None = Field(default=None, max_length=5)
    alias_template: str | None = Field(default=None, max_length=65536)

    @field_validator("nginx_host")
    @classmethod
    def _check_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return ""
        if not _HOST_RE.match(value):
            raise ValueError(
                "NGINX host must be a bare hostname or IP (letters, digits, '.', '-')."
            )
        return value

    @field_validator("nginx_user")
    @classmethod
    def _check_user(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return ""
        if not _SSH_USER_RE.match(value):
            raise ValueError(
                "SSH user must contain only letters, digits, '.', '_', and '-'."
            )
        return value

    @field_validator("nginx_conf_path")
    @classmethod
    def _check_conf_path(cls, value: str | None) -> str | None:
        return None if value is None else _validate_path_setting(value, "Conf path")

    @field_validator("ssh_key_path")
    @classmethod
    def _check_key_path(cls, value: str | None) -> str | None:
        return None if value is None else _validate_path_setting(value, "SSH key path")

    @field_validator("appmanager_proxy_host")
    @classmethod
    def _check_appmanager_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return ""
        if not _HOST_RE.match(value):
            raise ValueError(
                "AppManager backend host must be a bare hostname or IP."
            )
        return value

    @field_validator("appmanager_proxy_port")
    @classmethod
    def _check_appmanager_port(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_apps_port(value)

    @field_validator("alias_template")
    @classmethod
    def _check_alias_template_auth(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value and (
            "auth_request /api/auth/proxy-check" not in value
            or "error_page 401 = @appmanager_login;" not in value
        ):
            raise ValueError(
                "Alias template must include auth_request /api/auth/proxy-check "
                "and error_page 401 = @appmanager_login;"
            )
        return value


# --- Server provisioning (LXC/VM provider + policy) -------------------------

_PROXMOX_URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(?::\d{1,5})?/?$")


class RealmOut(BaseModel):
    """A Proxmox authentication realm offered for admin selection (issue_025)."""

    realm: str
    type: str = ""
    comment: str = ""


class ProvisioningSettingsOut(BaseModel):
    """Provider + policy settings. The API key itself is never returned."""

    provider_type: str = ""
    proxmox_url: str = ""
    proxmox_token_name: str = ""
    # Presence flag only; the stored secret is write-only.
    proxmox_api_key_set: bool = False
    proxmox_template_filter: str = ""
    proxmox_templates_only: bool = True
    proxmox_verify_tls: bool = True
    proxmox_conn_status: str = ""
    proxmox_conn_log: str = ""
    provisioning_self_service: bool = False
    provisioning_max_servers: int = 3
    # issue_021: retained for backward compatibility (read/write still
    # works), but no longer consulted for authorization -- any self-service
    # user may edit their own non-admin-managed server's resources now (see
    # get_account_server_access). The admin UI no longer surfaces this.
    provisioning_allow_resource_edit: bool = False
    provisioning_max_cpus: int = 12
    provisioning_max_memory_gb: int = 24
    provisioning_max_disk_gb: int = 200
    # issue_025: auto-add created guests to their owner's Proxmox pool, the
    # admin-selected realms, and the optional pool-id prefix.
    provisioning_add_to_pool: bool = True
    proxmox_realms: list[str] = Field(default_factory=list)
    proxmox_pool_prefix: str = ""
    jump_enabled: bool = False
    jump_host: str = ""
    jump_user: str = ""
    jump_port: int = 22
    jump_ssh_key_id: int | None = None
    # Management/jump-user split + account model (see update request).
    jump_management_user: str = "root"
    jump_account_mode: str = "per_user"
    jump_jumper_user: str = ""
    # SSH-config-bundle address override (see UpdateProvisioningSettingsRequest).
    jump_bundle_override: bool = False
    jump_bundle_host: str = ""
    jump_bundle_port: int = 22


class UpdateProvisioningSettingsRequest(BaseModel):
    provider_type: str | None = Field(default=None, max_length=20)
    proxmox_url: str | None = Field(default=None, max_length=253)
    proxmox_token_name: str | None = Field(default=None, max_length=253)
    # Write-only secret: accepted here, stored, never echoed back.
    proxmox_api_key: str | None = Field(default=None, max_length=512)
    proxmox_template_filter: str | None = Field(default=None, max_length=120)
    proxmox_templates_only: bool | None = None
    proxmox_verify_tls: bool | None = None
    provisioning_self_service: bool | None = None
    provisioning_max_servers: int | None = Field(default=None, ge=0, le=100)
    provisioning_allow_resource_edit: bool | None = None
    provisioning_max_cpus: int | None = Field(default=None, ge=1, le=1024)
    provisioning_max_memory_gb: int | None = Field(default=None, ge=1, le=4096)
    provisioning_max_disk_gb: int | None = Field(default=None, ge=1, le=65536)
    # issue_025: auto-add-to-pool toggle, selected realms, and pool-id prefix.
    provisioning_add_to_pool: bool | None = None
    proxmox_realms: list[str] | None = Field(default=None, max_length=64)
    proxmox_pool_prefix: str | None = Field(default=None, max_length=48)
    jump_enabled: bool | None = None
    jump_host: str | None = Field(default=None, max_length=253)
    jump_user: str | None = Field(default=None, max_length=64)
    jump_port: int | None = Field(default=None, ge=1, le=65535)
    jump_ssh_key_id: int | None = Field(default=None, ge=1)
    # Management/jump-user split. The management user is the SSH login AppManager
    # connects AS to manage the bastion (default root). The jumper user is the
    # shared account used only in 'shared' account mode. The account MODE itself
    # is changed only through the dedicated jump-server/account-mode endpoint
    # (which re-syncs and reverts on failure), so it is not accepted here.
    jump_management_user: str | None = Field(default=None, max_length=64)
    jump_jumper_user: str | None = Field(default=None, max_length=64)
    # SSH-config-bundle address override: when enabled, the built-in SSH config
    # bundle addresses the jump server at this host/port instead of the
    # management jump_host/jump_port (for bastions with separate public/private
    # interfaces). Off by default; onboarding/sync always use jump_host.
    jump_bundle_override: bool | None = None
    jump_bundle_host: str | None = Field(default=None, max_length=253)
    jump_bundle_port: int | None = Field(default=None, ge=1, le=65535)

    @field_validator("jump_host", "jump_bundle_host")
    @classmethod
    def _check_jump_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return ""
        if not _HOST_RE.match(value):
            raise ValueError(
                "Jump host must be a bare hostname or IP (letters, digits, '.', '-')."
            )
        return value

    @field_validator("proxmox_pool_prefix")
    @classmethod
    def _check_pool_prefix(cls, value: str | None) -> str | None:
        # A pool prefix must keep the resulting pool id within Proxmox's
        # allowed charset (letters, digits, '-', '_', '.').
        if value is None:
            return None
        value = value.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError(
                "Pool prefix may contain only letters, digits, '.', '-', '_'."
            )
        return value

    @field_validator("jump_user", "jump_management_user")
    @classmethod
    def _check_jump_user(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return ""
        if not _SSH_USER_RE.match(value):
            raise ValueError(
                "Jump user must contain only letters, digits, '.', '_', and '-'."
            )
        return value

    @field_validator("jump_jumper_user")
    @classmethod
    def _check_jump_jumper_user(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return ""
        # Created as an OS account -> stricter Linux username rule.
        if not _OS_USER_RE.match(value):
            raise ValueError(
                "Jumper user must be a valid Linux account name (lowercase "
                "letter or underscore first, then lowercase letters, digits, "
                "'-', '_'; max 32 chars)."
            )
        return value

    @field_validator("provider_type")
    @classmethod
    def _check_provider_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value not in ("", "proxmox"):
            raise ValueError("Provider type must be 'proxmox' (or empty to disable).")
        return value

    @field_validator("proxmox_url")
    @classmethod
    def _check_proxmox_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value:
            return ""
        if not _PROXMOX_URL_RE.match(value):
            raise ValueError(
                "Proxmox URL must look like https://host:8006 (PROTO://IP:PORT)."
            )
        return value

    @field_validator("proxmox_token_name")
    @classmethod
    def _check_proxmox_token_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if any(ch in value for ch in " \t\r\n\"'"):
            raise ValueError("Token name must not contain spaces or quotes.")
        return value

    @field_validator("proxmox_api_key")
    @classmethod
    def _strip_api_key(cls, value: str | None) -> str | None:
        # A whitespace-only "secret" must not sneak past the configured check.
        return None if value is None else value.strip()


class JumpSyncEntry(BaseModel):
    username: str
    status: str  # onboarded | failed | skipped | disabled
    detail: str = ""


class JumpSyncOut(BaseModel):
    results: list[JumpSyncEntry] = Field(default_factory=list)


class JumpAccountModeRequest(BaseModel):
    """Guarded switch of the jump-server account model (per_user <-> shared)."""

    account_mode: str
    # The shared account name; required when switching to 'shared'.
    jumper_user: str = Field(default="", max_length=64)
    # The admin must acknowledge that all users will be re-synced.
    acknowledge_sync: bool = False

    @field_validator("account_mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        value = (value or "").strip()
        if value not in ("per_user", "shared"):
            raise ValueError("account_mode must be 'per_user' or 'shared'.")
        return value

    @field_validator("jumper_user")
    @classmethod
    def _check_jumper(cls, value: str) -> str:
        value = (value or "").strip()
        # The jumper user is created as an OS account, so it must satisfy the
        # stricter OS-username rule (not just the SSH-login charset).
        if value and not _OS_USER_RE.match(value):
            raise ValueError(
                "Jumper user must be a valid Linux account name (lowercase "
                "letter or underscore first, then lowercase letters, digits, "
                "'-', '_'; max 32 chars)."
            )
        return value


class JumpAccountModeOut(BaseModel):
    account_mode: str
    reverted: bool = False
    detail: str = ""
    results: list[JumpSyncEntry] = Field(default_factory=list)


class ProviderTemplateOut(BaseModel):
    """A VM/LXC entry read live from the provider for the admin dropdown."""

    vmid: int
    name: str
    kind: str
    node: str = ""
    is_template: bool = False


class ProviderTemplatesOut(BaseModel):
    status: str
    log: str = ""
    templates: list[ProviderTemplateOut] = Field(default_factory=list)


class SshKeyOut(BaseModel):
    """Registry entry without secret material."""

    id: int
    name: str
    kind: str  # path | stored
    path: str = ""
    public_key: str = ""
    fingerprint: str = ""
    has_private_key: bool = False


class CreateSshKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: str
    # For kind='path'
    path: str = Field(default="", max_length=4096)
    # For kind='stored' (write-only; never returned)
    private_key: str = Field(default="", max_length=32768)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Key name must not be blank.")
        return value

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        if value not in ("path", "stored"):
            raise ValueError("Key kind must be 'path' or 'stored'.")
        return value

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        return _validate_admin_key_path(value)


class UpdateSshKeyRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    kind: str | None = None
    # For kind='path'
    path: str | None = Field(default=None, max_length=4096)
    # For kind='stored' (write-only; never returned). Sending a new value
    # replaces the stored key; omitting it keeps the current one.
    private_key: str | None = Field(default=None, max_length=32768)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Key name must not be blank.")
        return value

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("path", "stored"):
            raise ValueError("Key kind must be 'path' or 'stored'.")
        return value

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_admin_key_path(value)


class ServerTemplateOut(BaseModel):
    id: int
    vmid: int
    name: str
    kind: str
    admin_ssh_key_path: str = ""
    admin_ssh_key_id: int | None = None
    main_os_user: str = ""
    enable_sudo: bool = True
    enable_trusted_access: bool = True
    is_apps_server: bool = False


def _validate_main_os_user(value: str) -> str:
    """OS username for the template main user (same rules as servers)."""
    value = value.strip()
    if value and not _OS_USER_RE.match(value):
        raise ValueError(
            "Main user must be a valid Linux username (lowercase letters, "
            "digits, dashes, underscores; starting with a letter or underscore)."
        )
    return value


class ServerTemplateOptionOut(BaseModel):
    """User-facing template option (no vmid or admin key path)."""

    id: int
    name: str
    kind: str
    is_apps_server: bool = False


class ServerAccessOut(BaseModel):
    """Whether the caller may create servers for their own account."""

    can_create: bool = False
    reason: str = ""
    # issue_017: whether the caller may edit their own servers' resources
    # (self-service + admin-enabled, or admin actor).
    allow_resource_edit: bool = False


class ResourceUsageOut(BaseModel):
    """One resource dimension: how much of the per-user limit is committed."""

    used: int = 0
    limit: int = 0


class ServerUsageOut(BaseModel):
    """Per-user provisioning usage vs. limits, for the create-server form bars.

    ``unlimited`` is true for administrators, who are exempt from per-user
    caps; the individual dimensions are still populated with the committed
    usage (limit 0) so callers can show a count without a cap. For non-admins
    each dimension carries the committed usage and the applicable limit.
    """

    unlimited: bool = False
    servers: ResourceUsageOut = Field(default_factory=ResourceUsageOut)
    cpus: ResourceUsageOut = Field(default_factory=ResourceUsageOut)
    memory_gb: ResourceUsageOut = Field(default_factory=ResourceUsageOut)
    disk_gb: ResourceUsageOut = Field(default_factory=ResourceUsageOut)


def _validate_admin_key_path(value: str) -> str:
    """Admin SSH key path: shell-metachar-free and absolute when present.

    Requiring a leading ``/`` forecloses ``-o...`` option injection and
    ``host:path`` interpretation if the value is ever passed to scp-like
    tools in other argv positions.
    """
    value = _validate_path_setting(value, "Admin SSH key path")
    if value and not value.startswith("/"):
        raise ValueError("Admin SSH key path must be an absolute path.")
    return value


class CreateServerTemplateRequest(BaseModel):
    vmid: int = Field(ge=1, le=999999999)
    name: str = Field(min_length=1, max_length=60)
    kind: str
    admin_ssh_key_path: str = Field(default="", max_length=4096)
    admin_ssh_key_id: int | None = Field(default=None, ge=1)
    main_os_user: str = Field(default="", max_length=32)
    enable_sudo: bool = True
    enable_trusted_access: bool = True
    is_apps_server: bool = False

    @field_validator("main_os_user")
    @classmethod
    def _check_main_os_user(cls, value: str) -> str:
        return _validate_main_os_user(value)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        if value not in ("lxc", "vm"):
            raise ValueError("Template kind must be 'lxc' or 'vm'.")
        return value

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Template name must not be blank.")
        return value

    @field_validator("admin_ssh_key_path")
    @classmethod
    def _check_admin_key_path(cls, value: str) -> str:
        return _validate_admin_key_path(value)


class AccessResetOut(BaseModel):
    target_type: str
    target_name: str
    account: str = ""
    status: str
    detail: str = ""


class UserServerOut(BaseModel):
    id: int
    user_id: int
    name: str
    hostname: str = ""
    template_id: int | None = None
    template_name: str = ""
    vmid: int | None = None
    node: str = ""
    kind: str
    ip_address: str = ""
    cpus: int = 0
    memory_gb: int = 0
    disk_gb: int = 0
    admin_modified: bool = False
    status: str = "created"
    last_log: str = ""
    # Deferred deletion (issue_015-r4 F1). deletion_requested_at is the ISO
    # timestamp of a pending deletion (empty = none). deletion_pending is a
    # convenience flag. deletion_failed marks a server whose destroy failed
    # (admin-only rows). deletion_error carries the failure detail and is only
    # populated in administrator responses.
    deletion_requested_at: str = ""
    deletion_pending: bool = False
    deletion_failed: bool = False
    deletion_error: str = ""
    created_at: str = ""
    # issue_021: transient, response-only hint set right after a VM CPU/memory
    # change so the frontend can advise a reboot; never persisted.
    reboot_required: bool = False
    # issue_021: true when this server was cloned from a template flagged
    # is_apps_server (derived via a null-safe join; a server whose template
    # was deleted is not an apps-server). Drives the App-management
    # apps-server dropdown.
    is_apps_server: bool = False
    # Transient, response-only Proxmox pool membership, resolved live from the
    # provider on list (empty when the provider is unconfigured/unreachable or
    # the guest is not in a pool). Never persisted.
    poolid: str = ""
    # Transient, response-only live-presence classification, resolved from a
    # single cluster-wide Proxmox inventory read per list (never persisted):
    # "live" (the guest exists there), "missing" (an authoritative inventory
    # read completed and the guest was not found -- the DB record is *not*
    # deleted; it is surfaced for the admin/owner to investigate), or
    # "unverified" (the provider is unconfigured, unreachable, or the read
    # failed -- the last-known record is shown as-is; absence is never
    # inferred from a failed or partial read). Empty for a server with no
    # vmid yet (e.g. a still-pending create) or a failed provisioning record.
    presence: str = ""
    # Transient result returned by Reset access on servers. It contains only
    # target/account/status metadata, never SSH key material or admin paths.
    access_reset: list[AccessResetOut] = Field(default_factory=list)


class CreateUserServerRequest(BaseModel):
    template_id: int = Field(ge=1)
    # The user-supplied SUFFIX only; the server's full name is composed
    # server-side as "<template>-<owner-derived-id>-<suffix>". Capped so the
    # composed name still fits the 63-char server-name limit (validated after
    # composition, which returns a clearer "N characters available" error).
    name: str = Field(min_length=1, max_length=63)
    install_pubkey: bool = True
    pubkey_users: str = Field(default="", max_length=512)


class OwnerServersOut(BaseModel):
    """A user's servers, for the admin/self server overview (issue_015-r5 F2)."""

    user_id: int
    username: str
    derived_user_id: str = ""
    servers: list[UserServerOut] = Field(default_factory=list)


class ServersOverviewOut(BaseModel):
    """All servers the caller may see, grouped by owner.

    Administrators see every user's servers; a non-admin sees only their own
    (a single group). ``is_admin`` lets the UI adapt copy/columns.
    """

    is_admin: bool = False
    owners: list[OwnerServersOut] = Field(default_factory=list)
    # issue_local_032: admin-only summary counters for the Servers view's top
    # summary cards. total_users is every active application account
    # (including users with zero servers) -- not merely the owners with a
    # server record in ``owners`` below. total_servers is every server record
    # visible in this response (== sum of len(owner.servers) for owners)).
    # Both are 0 for a non-admin caller; the UI shows only a servers count for
    # a non-admin, derived client-side from their own single group instead.
    total_users: int = 0
    total_servers: int = 0


class ServerStatsPointOut(BaseModel):
    """One historical sample for a server's usage sparklines."""

    time: int = 0
    # CPU as a percentage 0-100 (Proxmox reports a 0-1 fraction).
    cpu_pct: float = 0.0
    mem: float = 0.0
    maxmem: float = 0.0
    disk: float = 0.0
    maxdisk: float = 0.0
    netin: float = 0.0
    netout: float = 0.0


class ServerStatsOut(BaseModel):
    """Historical usage for one server over a timeframe (Proxmox rrddata).

    ``available`` is false when stats cannot be read (no guest/vmid, provider
    not configured, or the read failed); ``detail`` explains why.
    """

    available: bool = False
    detail: str = ""
    timeframe: str = "hour"
    points: list[ServerStatsPointOut] = Field(default_factory=list)


class UpdateUserServerRequest(BaseModel):
    """Manual IP entry (VMs) and resource changes (LXC)."""

    ip_address: str | None = Field(default=None, max_length=15)
    cpus: int | None = Field(default=None, ge=1, le=1024)
    memory_gb: int | None = Field(default=None, ge=1, le=4096)
    disk_gb: int | None = Field(default=None, ge=1, le=65536)


class UpdateServerTemplateRequest(BaseModel):
    vmid: int | None = Field(default=None, ge=1, le=999999999)
    name: str | None = Field(default=None, min_length=1, max_length=60)
    kind: str | None = None
    admin_ssh_key_path: str | None = Field(default=None, max_length=4096)
    admin_ssh_key_id: int | None = Field(default=None, ge=1)
    main_os_user: str | None = Field(default=None, max_length=32)
    enable_sudo: bool | None = None
    enable_trusted_access: bool | None = None
    is_apps_server: bool | None = None

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str | None) -> str | None:
        if value is not None and value not in ("lxc", "vm"):
            raise ValueError("Template kind must be 'lxc' or 'vm'.")
        return value

    @field_validator("main_os_user")
    @classmethod
    def _check_main_os_user(cls, value: str | None) -> str | None:
        return None if value is None else _validate_main_os_user(value)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Template name must not be blank.")
        return value

    @field_validator("admin_ssh_key_path")
    @classmethod
    def _check_admin_key_path(cls, value: str | None) -> str | None:
        return None if value is None else _validate_admin_key_path(value)


class BrandingSettingsOut(BaseModel):
    app_name: str = ""
    app_logo: str = ""
    collaborators: list[str] = Field(default_factory=list)
    default_theme: str = "dark-modern"
    configured: bool = False


# issue_019: the UI themes an admin may set as the deployment default. Kept in
# sync with the frontend theme list (frontend/src/theme.tsx).
VALID_THEMES = ("dark-modern", "light", "energy", "classic")
DEFAULT_THEME = "dark-modern"


def normalize_theme(value: str | None) -> str:
    """Coerce a theme id to a known value, defaulting to dark-modern."""
    v = (value or "").strip()
    return v if v in VALID_THEMES else DEFAULT_THEME


class UpdateThemeRequest(BaseModel):
    """A user's own UI theme choice (issue_020). Empty resets to the default."""

    theme: str = Field(max_length=32)

    @field_validator("theme")
    @classmethod
    def _check_theme(cls, value: str) -> str:
        v = value.strip()
        if v and v not in VALID_THEMES:
            raise ValueError(f"Theme must be one of {VALID_THEMES}.")
        return v


# Admin-managed "Collaborators" shown on the About page. Each is a free-text
# name; the list is bounded so it stays small and the stored JSON cannot grow
# unbounded.
COLLABORATOR_NAME_MAX_LEN = 80
MAX_COLLABORATORS = 50


def _validate_collaborators(value: list[str]) -> list[str]:
    """Trim names, drop blanks, de-duplicate (case-insensitively, first wins),
    and enforce per-name length and count caps."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Each collaborator must be a name (text).")
        name = item.strip()
        if not name:
            continue
        if len(name) > COLLABORATOR_NAME_MAX_LEN:
            raise ValueError(
                f"Collaborator name must be at most {COLLABORATOR_NAME_MAX_LEN} "
                "characters."
            )
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
    if len(cleaned) > MAX_COLLABORATORS:
        raise ValueError(f"At most {MAX_COLLABORATORS} collaborators are allowed.")
    return cleaned


class UpdateBrandingSettingsRequest(BaseModel):
    app_name: str | None = Field(default=None, max_length=128)
    app_logo: str | None = Field(default=None, max_length=ICON_FIELD_MAX_LEN)
    collaborators: list[str] | None = None
    default_theme: str | None = Field(default=None, max_length=32)
    configured: bool | None = None

    @field_validator("app_name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()

    @field_validator("default_theme")
    @classmethod
    def _check_theme(cls, value: str | None) -> str | None:
        if value is None:
            return None
        v = value.strip()
        if v not in VALID_THEMES:
            raise ValueError(f"Theme must be one of {VALID_THEMES}.")
        return v

    @field_validator("app_logo")
    @classmethod
    def _check_logo(cls, value: str | None) -> str | None:
        # Reuse the application-logo policy: empty, a bundled relative path, an
        # absolute http(s) URL, or a capped raster data URI.
        return None if value is None else _validate_icon_value(value)

    @field_validator("collaborators")
    @classmethod
    def _check_collaborators(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_collaborators(value)


class MessageOut(BaseModel):
    detail: str


class TeamOut(BaseModel):
    id: int
    name: str
    sort_order: int
    icon: str = ""


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=TEAM_NAME_MAX_LEN)
    icon: str = Field(default="", max_length=ICON_FIELD_MAX_LEN)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_team_name(value)

    @field_validator("icon")
    @classmethod
    def _check_icon(cls, value: str) -> str:
        return _validate_team_icon(value)


class UpdateTeamRequest(BaseModel):
    name: str | None = Field(default=None, max_length=TEAM_NAME_MAX_LEN)
    icon: str | None = Field(default=None, max_length=ICON_FIELD_MAX_LEN)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        return None if value is None else _validate_team_name(value)

    @field_validator("icon")
    @classmethod
    def _check_icon(cls, value: str | None) -> str | None:
        return None if value is None else _validate_team_icon(value)


class ReorderTeamsRequest(BaseModel):
    team_ids: list[int] = Field(min_length=1)
