"""Runtime configuration.

All settings are read from ``APP_*`` environment variables so the service stays
deployment-neutral. Defaults favor a safe local run: authentication is enabled
and cookies are marked insecure only when explicitly running in development.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_prefix(value: str) -> str:
    """Validate and normalize a base path prefix.

    Empty string mounts at root. A non-empty prefix must start with ``/`` and
    must not end with ``/`` or contain ``//``.
    """
    value = value.strip()
    if value in ("", "/"):
        return ""
    if not value.startswith("/"):
        raise ValueError("APP_BASE_PREFIX must start with '/'")
    if value.endswith("/"):
        raise ValueError("APP_BASE_PREFIX must not end with '/'")
    if "//" in value:
        raise ValueError("APP_BASE_PREFIX must not contain '//'")
    return value


class Settings:
    """Resolved application settings."""

    def __init__(self) -> None:
        self.base_dir: Path = Path(
            os.environ.get("APP_BASE_DIR", Path(__file__).resolve().parents[2])
        ).resolve()
        self.data_dir: Path = Path(
            os.environ.get("APP_DATA_DIR", self.base_dir / "data")
        ).resolve()
        self.db_path: Path = Path(
            os.environ.get("APP_DB_PATH", self.data_dir / "app.db")
        ).resolve()
        self.frontend_dist: Path = Path(
            os.environ.get(
                "APP_FRONTEND_DIST", self.base_dir / "frontend" / "dist"
            )
        ).resolve()

        self.base_prefix: str = _normalize_prefix(
            os.environ.get("APP_BASE_PREFIX", "")
        )
        self.enable_auth: bool = _env_bool("APP_ENABLE_AUTH", True)
        self.dev_mode: bool = _env_bool("APP_DEV", False)
        # Secure cookies by default; only relax when explicitly in dev mode or
        # when the operator overrides it (e.g. terminating TLS upstream but
        # testing over plain HTTP locally).
        self.secure_cookies: bool = _env_bool(
            "APP_SECURE_COOKIES", not self.dev_mode
        )
        self.session_ttl_seconds: int = int(
            os.environ.get("APP_SESSION_TTL_SECONDS", str(12 * 60 * 60))
        )
        self.credentials_file: Path = self.data_dir / "first-run-admin-credentials.txt"

        self.sso_auto_provision: bool = _env_bool("APP_SSO_AUTO_PROVISION", True)
        self.sso_local_login_enabled: bool = _env_bool(
            "APP_SSO_LOCAL_LOGIN_ENABLED", True
        )
        self.sso_default_role: str = os.environ.get("APP_SSO_DEFAULT_ROLE", "user")
        if self.sso_default_role not in {"admin", "user"}:
            raise ValueError("APP_SSO_DEFAULT_ROLE must be 'admin' or 'user'")
        self.sso_email_domain_allowlist: list[str] = [
            domain.lower().lstrip("@")
            for domain in _env_list("APP_SSO_EMAIL_DOMAIN_ALLOWLIST")
        ]

        self.oidc_enabled: bool = _env_bool("APP_OIDC_ENABLED", False)
        self.oidc_provider: str = os.environ.get("APP_OIDC_PROVIDER", "oidc").strip()
        self.oidc_label: str = os.environ.get("APP_OIDC_LABEL", "Single Sign-On").strip()
        self.oidc_client_id: str = os.environ.get("APP_OIDC_CLIENT_ID", "").strip()
        self.oidc_client_secret: str = os.environ.get(
            "APP_OIDC_CLIENT_SECRET", ""
        ).strip()
        self.oidc_issuer: str = os.environ.get("APP_OIDC_ISSUER", "").strip().rstrip("/")
        self.oidc_authorization_endpoint: str = os.environ.get(
            "APP_OIDC_AUTHORIZATION_ENDPOINT", ""
        ).strip()
        self.oidc_token_endpoint: str = os.environ.get(
            "APP_OIDC_TOKEN_ENDPOINT", ""
        ).strip()
        self.oidc_userinfo_endpoint: str = os.environ.get(
            "APP_OIDC_USERINFO_ENDPOINT", ""
        ).strip()
        self.oidc_jwks_uri: str = os.environ.get("APP_OIDC_JWKS_URI", "").strip()
        self.oidc_scopes: str = os.environ.get(
            "APP_OIDC_SCOPES", "openid email profile"
        ).strip()
        self.microsoft_tenant: str = os.environ.get(
            "APP_MICROSOFT_TENANT", "common"
        ).strip()

        self.saml_enabled: bool = _env_bool("APP_SAML_ENABLED", False)
        self.saml_label: str = os.environ.get("APP_SAML_LABEL", "SAML SSO").strip()
        self.saml_sp_entity_id: str = os.environ.get(
            "APP_SAML_SP_ENTITY_ID", ""
        ).strip()
        self.saml_idp_entity_id: str = os.environ.get(
            "APP_SAML_IDP_ENTITY_ID", ""
        ).strip()
        self.saml_idp_sso_url: str = os.environ.get(
            "APP_SAML_IDP_SSO_URL", ""
        ).strip()
        self.saml_idp_x509_cert: str = os.environ.get(
            "APP_SAML_IDP_X509_CERT", ""
        ).strip()
        self.saml_nameid_format: str = os.environ.get(
            "APP_SAML_NAMEID_FORMAT",
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        ).strip()
        self.saml_email_attribute: str = os.environ.get(
            "APP_SAML_EMAIL_ATTRIBUTE", "email"
        ).strip()
        self.saml_first_name_attribute: str = os.environ.get(
            "APP_SAML_FIRST_NAME_ATTRIBUTE", "firstName"
        ).strip()
        self.saml_last_name_attribute: str = os.environ.get(
            "APP_SAML_LAST_NAME_ATTRIBUTE", "lastName"
        ).strip()

        # Logging. The application owns a single consolidated log file so the
        # same output is produced whether it runs backgrounded, in the
        # foreground (``--dev``), or under a bare ``uvicorn`` invocation.
        self.log_dir: Path = Path(
            os.environ.get("APP_LOG_DIR", self.base_dir / "logs")
        ).resolve()
        self.log_file: Path = self.log_dir / os.environ.get(
            "APP_LOG_FILE", "app.log"
        )
        self.log_level: str = os.environ.get("APP_LOG_LEVEL", "INFO").upper()
        self.log_to_file: bool = _env_bool("APP_LOG_TO_FILE", True)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
