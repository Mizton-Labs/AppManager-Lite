"""Application catalogue routes.

Listing is available to any authenticated user but constrained to the teams the
caller belongs to (administrators and the auth-disabled identity see all). Only
approved applications appear in those listings.

Any authenticated user may submit an application for one of their own teams; it
stays ``pending`` until an administrator approves it, unless the submitter is a
self-service user (or an administrator), in which case it is approved on
creation. Editing and deleting are limited to the application's creator or an
administrator. All state-changing routes are CSRF-protected.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, repository, reverse_proxy, schemas
from ..config import get_settings
from ..db import get_connection
from ..deps import get_current_user, get_db, require_admin, verify_csrf
from ..schemas import (
    AliasConfigOut,
    ApplicationOut,
    CreateApplicationRequest,
    MessageOut,
    UpdateApplicationRequest,
    ApplicationStatisticsOut,
    ApplicationStatisticsRow,
    ApplicationStatisticsSettingsOut,
    ApplicationStatisticsSettingsUpdate,
    ApplicationTrendPoint,
    ApplicationTrendSeries,
    ApplicationStatisticsDetailOut,
    ApplicationUserActivityOut,
    ApplicationFavoriteUserOut,
    UserActivityRow,
)

router = APIRouter(tags=["applications"])

logger = logging.getLogger(__name__)
_launch_lock = threading.Lock()
_launch_last_seen: dict[tuple[int, int], float] = {}
_LAUNCH_MIN_INTERVAL_SECONDS = 5.0
# Application types AppManager mediates access to (behind login + owner/team/user
# gating), and which may therefore be marked private or shared with users: a
# managed alias, or an embedded app rendered only inside the authenticated portal.
_MEDIATED_TYPES = ("alias", "embedded")


def _app_out(
    app: dict[str, Any], *, include_creator: bool = False,
    is_favorite: bool = False, visits_7d: int | None = None,
    show_statistics: bool = False,
) -> ApplicationOut:
    return ApplicationOut(
        id=app["id"],
        name=app["name"],
        description=app["description"],
        url=app["url"],
        url_type=app["url_type"],
        icon_url=app["icon_url"],
        teams=app["teams"],
        is_active=app["is_active"],
        approval_status=app["approval_status"],
        sort_order=app["sort_order"],
        created_by=app.get("created_by_username"),
        created_by_id=app.get("created_by"),
        publisher_team=app.get("publisher_team", ""),
        last_push_status=app.get("last_push_status") if include_creator else None,
        last_push_log=app.get("last_push_log", "") if include_creator else "",
        last_push_at=app.get("last_push_at") if include_creator else None,
        apps_server=app.get("apps_server", "") if include_creator else "",
        apps_protocol=app.get("apps_protocol", "http") if include_creator else "http",
        apps_port=app.get("apps_port", "") if include_creator else "",
        apps_path=app.get("apps_path", "") if include_creator else "",
        alias_auth_required=bool(app.get("alias_auth_required", True)),
        apps_rewrite_root=bool(app.get("apps_rewrite_root", False)),
        pass_authenticated_user=bool(app.get("pass_authenticated_user", False)),
        pending_alias=app.get("pending_alias", "") if include_creator else "",
        pending_is_active=app.get("pending_is_active") if include_creator else None,
        pending_alias_auth_required=(
            app.get("pending_alias_auth_required") if include_creator else None
        ),
        pending_apps_rewrite_root=(
            app.get("pending_apps_rewrite_root") if include_creator else None
        ),
        pending_pass_authenticated_user=(
            app.get("pending_pass_authenticated_user") if include_creator else None
        ),
        needs_push=bool(app.get("needs_push")) if include_creator else False,
        is_favorite=is_favorite,
        visits_7d=visits_7d,
        show_statistics=show_statistics,
        is_private=bool(app.get("is_private")),
        shared_users=app.get("shared_users", []) if include_creator else [],
    )


def _visible_app(conn: sqlite3.Connection, app_id: int, user: dict[str, Any]) -> dict[str, Any] | None:
    app = repository.get_application(conn, app_id)
    return app if app and repository.can_access_application(conn, app, user) else None


def _validate_shared_users(
    conn: sqlite3.Connection, user_ids: list[int], owner_id: int | None
) -> list[int]:
    unique = list(dict.fromkeys(user_ids))
    for user_id in unique:
        target = repository.get_user_by_id(conn, user_id)
        if target is None or not target["is_active"]:
            raise HTTPException(status_code=400, detail=f"Shared user {user_id} is not active")
        if owner_id and user_id == owner_id:
            raise HTTPException(status_code=400, detail="The owner is already allowed")
    return unique


def _list_out(conn: sqlite3.Connection, apps: list[dict[str, Any],], user: dict[str, Any]) -> list[ApplicationOut]:
    show = bool(repository.get_settings_row(conn).get("show_app_statistics", 0))
    favorites, visits = repository.application_card_statistics(
        conn, [app["id"] for app in apps], user.get("id")
    )
    return [
        _app_out(app, is_favorite=app["id"] in favorites,
                 visits_7d=visits.get(app["id"]) if show else None,
                 show_statistics=show)
        for app in apps
    ]


def resolve_user_apps_server_host(
    conn: sqlite3.Connection, owner: dict[str, Any] | None
) -> str:
    """Resolve an owner's apps-server reference to a live, connectable host.

    issue_021: a user's account-level ``apps_server`` is set at account
    creation to the *name* of the apps-server template selected in the
    create-user form (or a custom hostname/IP typed by an administrator) --
    it is a reference, not necessarily a provisioned server's address. This
    resolves that reference to the actual host of a live, owned apps-server
    server whenever one exists, so alias pushes reach the real machine
    instead of a template name that was never a valid host.

    Preference order: a candidate whose own host the reference already names
    literally (the admin typed a real hostname/IP directly), else the server
    cloned from the template the reference names, else the owner's first
    apps-server server (by name), else the literal stored
    ``apps_server``/``apps_server_ip`` value (best effort -- e.g. before
    provisioning finishes, or for legacy custom entries that already are a
    real hostname/IP with no matching provisioned server).
    """
    if not owner:
        return ""
    reference = (owner.get("apps_server") or "").strip()
    literal_fallback = reference or (owner.get("apps_server_ip") or "")
    owner_id = owner.get("id")
    if not owner_id:
        return literal_fallback
    candidates = [
        s
        for s in repository.list_user_servers(conn, owner_id)
        if s.get("is_apps_server")
        and s.get("status") != "failed"
        and (s.get("hostname") or s.get("ip_address"))
    ]
    if not candidates:
        return literal_fallback
    if reference:
        for server in candidates:
            if reference in (server.get("hostname"), server.get("ip_address")):
                return reference
        templates_by_name = {
            t["name"]: t["id"] for t in repository.list_server_templates(conn)
        }
        template_id = templates_by_name.get(reference)
        if template_id is not None:
            for server in candidates:
                if server.get("template_id") == template_id:
                    return server.get("hostname") or server.get("ip_address") or ""
    first = candidates[0]
    return first.get("hostname") or first.get("ip_address") or ""


# Cap stored push transcripts so a verbose remote error can't bloat the row.
_MAX_PUSH_LOG = 16000


def _nginx_config_changed(
    existing: dict[str, Any], payload: UpdateApplicationRequest, *, is_admin: bool
) -> bool:
    checks: tuple[tuple[str, Any], ...] = (
        ("name", payload.name),
        ("url", payload.url),
        ("url_type", payload.url_type),
        ("apps_protocol", payload.apps_protocol),
        ("apps_port", payload.apps_port),
        ("apps_path", payload.apps_path),
        ("is_active", payload.is_active),
        ("alias_auth_required", payload.alias_auth_required),
        ("apps_rewrite_root", payload.apps_rewrite_root),
        ("pass_authenticated_user", payload.pass_authenticated_user),
        ("apps_server", payload.apps_server),
    )
    if any(value is not None and value != existing[key] for key, value in checks):
        return True
    if payload.is_private is not None and payload.is_private != existing.get("is_private", False):
        return True
    if payload.shared_user_ids is not None:
        if set(payload.shared_user_ids) != set(existing.get("shared_user_ids", [])):
            return True
    return False


def _push_alias_on_approval(
    application_id: int, actor: dict[str, Any]
) -> None:
    """Push an application's alias to the reverse proxy after approval.

    Runs out-of-band on its own connection (the request transaction is already
    committed by the time this matters), records the result on the application,
    and writes an audit entry. Never raises: a push failure must not break the
    approval that triggered it.
    """
    try:
        with get_connection() as conn:
            app = repository.get_application(conn, application_id)
            if app is None:
                return
            settings = repository.get_settings_row(conn)
            settings["ssh_key_path"] = repository.reverse_proxy_key_path(
                conn, settings
            )

            if app["url_type"] != "alias":
                result = reverse_proxy.PushResult(status="skipped")
                result.log("Skipped: application does not use a local alias.")
            else:
                # Resolve the upstream server and port for the alias. The
                # reverse-proxy settings host (nginx_host) is only the SSH target
                # used to push config -- it is never the upstream the alias
                # proxies to.
                #   - server: the application's own server (admin-set) first,
                #     then the owning user's configured apps host/IP.
                #   - protocol/path/port: the application's own upstream settings.
                owner_id = app.get("created_by")
                owner = (
                    repository.get_user_by_id(conn, owner_id)
                    if owner_id
                    else None
                )
                apps_port = app.get("apps_port") or ""
                apps_protocol = app.get("apps_protocol") or "http"
                apps_path = app.get("apps_path") or ""
                apps_server = app.get("apps_server") or (
                    resolve_user_apps_server_host(conn, owner) if owner else ""
                )
                if not (apps_server and apps_port):
                    result = reverse_proxy.PushResult(status="skipped")
                    result.log(
                        "Skipped: no apps server (on the application or its "
                        "owner's account) and/or port is configured for this "
                        "application."
                    )
                else:
                    result = reverse_proxy.push_alias(
                        settings,
                        apps_server=apps_server,
                        apps_protocol=apps_protocol,
                        apps_port=apps_port,
                        apps_path=apps_path,
                        alias=app["url"],
                        app_name=app["name"],
                        app_id=application_id,
                        is_active=app["is_active"],
                        alias_auth_required=app["alias_auth_required"],
                        apps_rewrite_root=bool(app.get("apps_rewrite_root")),
                        pass_authenticated_user=bool(app.get("pass_authenticated_user")),
                    )

            transcript = result.transcript[:_MAX_PUSH_LOG]
            needs_push = (
                app["url_type"] == "alias"
                and app["approval_status"] == "approved"
                and result.status != "ok"
            )
            repository.set_application_push_result(
                conn,
                application_id,
                status=result.status,
                log=transcript,
                needs_push=needs_push,
            )
            action = (
                "nginx_revert"
                if result.status == "reverted"
                else "nginx_push"
            )
            audit.record(
                conn,
                category=audit.CATEGORY_APPLICATION,
                action=action,
                actor=actor,
                target_type="application",
                target_id=application_id,
                target_name=app["name"],
                detail=f"status={result.status}\n{transcript}"[:_MAX_PUSH_LOG],
            )
    except Exception:  # pragma: no cover - defensive; push is best-effort
        logger.exception(
            "Reverse-proxy push failed unexpectedly for app id=%s", application_id
        )


def _can_manage_app(app: dict[str, Any], actor: dict[str, Any]) -> bool:
    return actor["role"] == "admin" or (
        bool(actor.get("id")) and app["created_by"] == actor["id"]
    )


def _remove_alias_on_delete(
    application_id: int, app_name: str, url_type: str, actor: dict[str, Any]
) -> None:
    """Remove a deleted application's alias block from the reverse proxy.

    Runs out-of-band on its own connection after the application row has been
    deleted, so it records the outcome only in the audit log (the row is gone).
    Only attempted for alias applications. Never raises: a removal failure must
    not affect the delete that triggered it.
    """
    if url_type != "alias":
        return
    try:
        with get_connection() as conn:
            settings = repository.get_settings_row(conn)
            settings["ssh_key_path"] = repository.reverse_proxy_key_path(
                conn, settings
            )
            result = reverse_proxy.remove_alias(settings, app_id=application_id)
            transcript = result.transcript[:_MAX_PUSH_LOG]
            action = (
                "nginx_remove_revert"
                if result.status == "reverted"
                else "nginx_remove"
            )
            audit.record(
                conn,
                category=audit.CATEGORY_APPLICATION,
                action=action,
                actor=actor,
                target_type="application",
                target_id=application_id,
                target_name=app_name,
                detail=f"status={result.status}\n{transcript}"[:_MAX_PUSH_LOG],
            )
    except Exception:  # pragma: no cover - defensive; removal is best-effort
        logger.exception(
            "Reverse-proxy alias removal failed unexpectedly for app id=%s",
            application_id,
        )


def _validate_teams(conn: sqlite3.Connection, teams: list[str]) -> None:
    known = set(repository.list_team_names(conn))
    unknown = [t for t in teams if t not in known]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown team(s): {', '.join(unknown)}",
        )


def _require_nonempty_teams(teams: list[str]) -> None:
    """A non-admin submission must target at least one team.

    Any signed-in user may share an application with any team (membership is no
    longer required); a non-admin submission is still scoped to one or more
    teams and remains subject to admin approval before it appears on those
    teams' pages.
    """
    if not teams:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one team.",
        )


def _enforce_embedded_alias_exists(
    conn: sqlite3.Connection, url: str, owner_id: int | None
) -> None:
    """Reject an embedded application that does not frame one of the owner's own
    existing aliases.

    An embedded app renders the same-origin alias path (served by the reverse
    proxy under the portal's own domain) in an in-portal iframe -- that is the
    only source reachable by external users and free of mixed-content. So an
    embedded app's ``url`` must be the slug of a real, active, approved alias
    application owned by the resulting owner. ``owner_id`` is the application's
    creator/owner -- for admins acting on behalf of an owner the alias must
    belong to that owner. If the alias does not exist yet it must be created
    first (as an alias application), then referenced here.
    """
    slug = (url or "").strip().lstrip("/")
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Embedded application must reference an existing alias.",
        )
    # The synthetic auth-disabled identity (id 0 / None) owns no applications;
    # there is no owner to scope the alias lookup to.
    if not owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Embedded application must reference an existing alias.",
        )
    alias = repository.find_owner_alias_by_slug(conn, slug, owner_id)
    if alias is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Embedded application must reference one of your existing "
                "aliases. Create the alias application first, then add the "
                "embedded app."
            ),
        )


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(
    team: str | None = Query(
        default=None, description="Limit to a single team the caller can access."
    ),
    publisher_team: str | None = Query(
        default=None,
        description="Limit to apps published by users assigned to this team.",
    ),
    include_inactive: bool = Query(
        default=False, description="Administrators only: include disabled apps."
    ),
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ApplicationOut]:
    is_admin = user["role"] == "admin"

    if include_inactive and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    active_only = not (include_inactive and is_admin)
    if team is not None and publisher_team is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use either team or publisher_team, not both",
        )

    if publisher_team is not None:
        if publisher_team not in set(repository.list_team_names(conn)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Unknown team"
            )
        apps = repository.list_applications_for_publisher_team(
            conn,
            publisher_team,
            visible_team_names=None,
            active_only=active_only,
        )
        if not is_admin:
            apps = [app for app in apps if repository.can_access_application(conn, app, user)]
    elif team is not None:
        if team not in set(repository.list_team_names(conn)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Unknown team"
            )
        if not is_admin and team not in set(user["teams"]):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not permitted for this team",
            )
        apps = repository.list_applications_for_team(
            conn, team, active_only=active_only
        )
        if not is_admin:
            apps = [app for app in apps if repository.can_access_application(conn, app, user)]
    elif is_admin:
        apps = repository.list_all_applications(conn, active_only=active_only)
    else:
        apps = repository.list_visible_applications(conn, user)

    return _list_out(conn, apps, user)


@router.get("/applications/mine", response_model=list[ApplicationOut])
def list_my_applications(
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ApplicationOut]:
    """Applications the caller created, in any approval state."""
    if not user.get("id"):
        # The auth-disabled identity owns nothing it can manage.
        return []
    apps = repository.list_applications_for_owner(conn, user["id"])
    return [_app_out(a, include_creator=True) for a in apps]


@router.post("/applications/{application_id}/favorite", response_model=MessageOut)
def favorite_application(
    application_id: int,
    user: dict[str, Any] = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    app = _visible_app(conn, application_id, user)
    if app is None or not user.get("id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    repository.set_application_favorite(conn, user["id"], application_id)
    return MessageOut(detail="Application favorited")


@router.delete("/applications/{application_id}/favorite", response_model=MessageOut)
def unfavorite_application(
    application_id: int,
    user: dict[str, Any] = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    app = _visible_app(conn, application_id, user)
    if app is None or not user.get("id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    repository.remove_application_favorite(conn, user["id"], application_id)
    return MessageOut(detail="Application unfavorited")


@router.post("/applications/{application_id}/launch", status_code=status.HTTP_204_NO_CONTENT)
def record_application_launch(
    application_id: int,
    user: dict[str, Any] = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    if _visible_app(conn, application_id, user) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    user_id = int(user.get("id", 0) or 0)
    key = (user_id, application_id)
    now = time.monotonic()
    with _launch_lock:
        if now - _launch_last_seen.get(key, 0.0) < _LAUNCH_MIN_INTERVAL_SECONDS:
            return
        _launch_last_seen[key] = now
    repository.record_application_launch(conn, application_id, f"user:{user_id or 'local'}")


@router.get("/application-statistics", response_model=ApplicationStatisticsOut)
def application_statistics(
    days: int = Query(default=30, ge=1, le=90),
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplicationStatisticsOut:
    rows = conn.execute(
        "SELECT usage_date, SUM(launch_count) launches, COUNT(DISTINCT visitor_key) unique_users "
        "FROM application_usage_daily WHERE usage_date >= date('now', ?) GROUP BY usage_date ORDER BY usage_date",
        (f"-{days - 1} days",),
    ).fetchall()
    totals_by_date = {
        row["usage_date"]: (row["launches"], row["unique_users"])
        for row in rows
    }
    first_day = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
    dates = [(first_day + timedelta(days=offset)).isoformat() for offset in range(days)]
    trend = [
        ApplicationTrendPoint(
            date=usage_date,
            launches=totals_by_date.get(usage_date, (0, 0))[0],
            unique_users=totals_by_date.get(usage_date, (0, 0))[1],
        )
        for usage_date in dates
    ]
    apps = conn.execute(
        "SELECT a.id, a.name, COALESCE(SUM(u.launch_count), 0) launches, "
        "COUNT(DISTINCT u.visitor_key) unique_users, "
        "(SELECT COUNT(*) FROM application_favorites f WHERE f.application_id = a.id) favorites, "
        "(SELECT COALESCE(SUM(launch_count),0) FROM application_usage_daily d WHERE d.application_id=a.id AND d.usage_date>=date('now','-6 days')) visits_7d, "
        "(SELECT COALESCE(SUM(request_count),0) FROM application_alias_usage_daily d2 WHERE d2.application_id=a.id AND d2.usage_date>=date('now', ?)) alias_visits, "
        "(SELECT COUNT(DISTINCT visitor_key) FROM application_alias_usage_daily d3 WHERE d3.application_id=a.id AND d3.usage_date>=date('now', ?) AND visitor_key LIKE 'user:%') unique_alias_users, "
        "(SELECT COALESCE(SUM(request_count),0) FROM application_alias_usage_daily d4 WHERE d4.application_id=a.id AND d4.usage_date>=date('now', ?) AND visitor_key='anonymous') anonymous_alias_visits "
        "FROM applications a LEFT JOIN application_usage_daily u ON u.application_id=a.id AND u.usage_date>=date('now', ?) "
        "GROUP BY a.id ORDER BY launches DESC, favorites DESC, a.name",
        (f"-{days - 1} days", f"-{days - 1} days", f"-{days - 1} days", f"-{days - 1} days"),
    ).fetchall()
    top_apps = [row for row in apps if row["launches"] > 0][:10]
    top_ids = [row["id"] for row in top_apps]
    series_by_id: dict[int, dict[str, int]] = {app_id: {} for app_id in top_ids}
    if top_ids:
        marks = ",".join("?" for _ in top_ids)
        for row in conn.execute(
            f"SELECT application_id, usage_date, SUM(launch_count) launches "
            f"FROM application_usage_daily WHERE application_id IN ({marks}) "
            f"AND usage_date >= date('now', ?) GROUP BY application_id, usage_date",
            (*top_ids, f"-{days - 1} days"),
        ):
            series_by_id[row["application_id"]][row["usage_date"]] = row["launches"]
    app_trends = [
        ApplicationTrendSeries(
            application_id=row["id"], name=row["name"], launches=row["launches"],
            points=[ApplicationTrendPoint(date=date, launches=series_by_id[row["id"]].get(date, 0), unique_users=0) for date in dates],
        ) for row in top_apps
    ]
    user_rows = conn.execute(
        "SELECT u.username, SUM(d.launch_count) launches, COUNT(DISTINCT d.application_id) applications_used "
        "FROM application_usage_daily d JOIN users u ON d.visitor_key = ('user:' || u.id) "
        "WHERE d.usage_date >= date('now', ?) GROUP BY u.id ORDER BY launches DESC, u.id LIMIT 10",
        (f"-{days - 1} days",),
    ).fetchall()
    total_launches = sum(point.launches for point in trend)
    unique_users = conn.execute(
        "SELECT COUNT(DISTINCT visitor_key) c FROM application_usage_daily WHERE usage_date >= date('now', ?)",
        (f"-{days - 1} days",),
    ).fetchone()["c"]
    favorites = conn.execute("SELECT COUNT(*) c FROM application_favorites").fetchone()["c"]
    alias_totals = conn.execute(
        "SELECT COALESCE(SUM(request_count),0) alias_visits, "
        "COUNT(DISTINCT CASE WHEN visitor_key LIKE 'user:%' THEN visitor_key END) unique_alias_users, "
        "COALESCE(SUM(CASE WHEN visitor_key='anonymous' THEN request_count ELSE 0 END),0) anonymous_alias_visits "
        "FROM application_alias_usage_daily WHERE usage_date >= date('now', ?)",
        (f"-{days - 1} days",),
    ).fetchone()
    return ApplicationStatisticsOut(
        days=days, launches=total_launches, unique_users=unique_users, favorites=favorites,
        trend=trend,
        applications=[
            ApplicationStatisticsRow(
                application_id=row["id"], name=row["name"], launches=row["launches"],
                unique_users=row["unique_users"], favorites=row["favorites"],
                visits_7d=row["visits_7d"], alias_visits=row["alias_visits"],
                unique_alias_users=row["unique_alias_users"],
                anonymous_alias_visits=row["anonymous_alias_visits"],
            )
            for row in apps
        ],
        app_trends=app_trends,
        user_activity=[UserActivityRow(user_id=repository.derive_user_id(row["username"]), launches=row["launches"], applications_used=row["applications_used"]) for row in user_rows],
        alias_visits=alias_totals["alias_visits"],
        unique_alias_users=alias_totals["unique_alias_users"],
        anonymous_alias_visits=alias_totals["anonymous_alias_visits"],
    )


@router.get("/application-statistics/{application_id}/users", response_model=ApplicationStatisticsDetailOut)
def application_statistics_users(
    application_id: int,
    days: int = Query(default=30, ge=1, le=90),
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplicationStatisticsDetailOut:
    if repository.get_application(conn, application_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    activity = conn.execute(
        "SELECT u.username, SUM(d.launch_count) launches, COUNT(DISTINCT d.usage_date) active_days, MAX(d.usage_date) last_activity "
        "FROM application_usage_daily d JOIN users u ON d.visitor_key = ('user:' || u.id) "
        "WHERE d.application_id = ? AND d.usage_date >= date('now', ?) "
        "GROUP BY u.id ORDER BY launches DESC, last_activity DESC",
        (application_id, f"-{days - 1} days"),
    ).fetchall()
    favorites = conn.execute(
        "SELECT u.username, f.created_at FROM application_favorites f JOIN users u ON u.id = f.user_id "
        "WHERE f.application_id = ? ORDER BY f.created_at DESC",
        (application_id,),
    ).fetchall()
    return ApplicationStatisticsDetailOut(
        application_id=application_id,
        activity_users=[ApplicationUserActivityOut(user_id=repository.derive_user_id(row["username"]), launches=row["launches"], active_days=row["active_days"], last_activity=row["last_activity"]) for row in activity],
        favorite_users=[ApplicationFavoriteUserOut(user_id=repository.derive_user_id(row["username"]), starred_at=row["created_at"]) for row in favorites],
    )


@router.get("/application-statistics/settings", response_model=ApplicationStatisticsSettingsOut)
def application_statistics_settings(
    _: dict[str, Any] = Depends(require_admin), conn: sqlite3.Connection = Depends(get_db)
) -> ApplicationStatisticsSettingsOut:
    return ApplicationStatisticsSettingsOut(show_app_statistics=bool(repository.get_settings_row(conn).get("show_app_statistics", 0)))


@router.patch("/application-statistics/settings", response_model=ApplicationStatisticsSettingsOut)
def update_application_statistics_settings(
    payload: ApplicationStatisticsSettingsUpdate,
    actor: dict[str, Any] = Depends(require_admin), _: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplicationStatisticsSettingsOut:
    conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    conn.execute(
        "UPDATE settings SET show_app_statistics = ?, updated_at = datetime('now') WHERE id = 1",
        (int(payload.show_app_statistics),),
    )
    audit.record(conn, category=audit.CATEGORY_SYSTEM, action="application_statistics_settings", actor=actor, target_type="settings", target_id=1, target_name="application statistics", detail=f"show_app_statistics={int(payload.show_app_statistics)}")
    return ApplicationStatisticsSettingsOut(show_app_statistics=payload.show_app_statistics)


@router.get("/applications/manage", response_model=list[ApplicationOut])
def list_managed_applications(
    _actor: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ApplicationOut]:
    """Every application with its creator and status, for administrators."""
    apps = repository.list_all_applications_admin(conn)
    return [_app_out(a, include_creator=True) for a in apps]


@router.get("/applications/{application_id}/alias-config", response_model=AliasConfigOut)
def get_application_alias_config(
    application_id: int,
    actor: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> AliasConfigOut:
    app = repository.get_application(conn, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if not _can_manage_app(app, actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only inspect applications you created",
        )

    if app["url_type"] != "alias":
        return AliasConfigOut(
            status="skipped",
            log="Skipped: application does not use a local alias.",
        )

    _rp_settings = repository.get_settings_row(conn)
    _rp_settings["ssh_key_path"] = repository.reverse_proxy_key_path(
        conn, _rp_settings
    )
    result = reverse_proxy.read_alias_config(
        _rp_settings, app_id=application_id
    )
    return AliasConfigOut(
        status=result.status,
        log=result.transcript,
        alias=result.alias,
        apps_protocol=result.apps_protocol,
        apps_server=result.apps_server,
        apps_port=result.apps_port,
        apps_path=result.apps_path,
        alias_auth_required=result.alias_auth_required,
        apps_rewrite_root=result.apps_rewrite_root,
        pass_authenticated_user=result.pass_authenticated_user,
    )


@router.post("/applications", response_model=ApplicationOut, status_code=201)
def create_application(
    payload: CreateApplicationRequest,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplicationOut:
    is_admin = actor["role"] == "admin"
    _validate_teams(conn, payload.teams)
    shared_user_ids = _validate_shared_users(conn, payload.shared_user_ids, actor.get("id"))
    if (payload.is_private or shared_user_ids) and not get_settings().enable_auth:
        raise HTTPException(status_code=400, detail="Private or user-restricted applications require authentication to be enabled")
    if shared_user_ids and payload.url_type not in _MEDIATED_TYPES:
        raise HTTPException(status_code=400, detail="Specific-user sharing requires a managed alias or embedded app")
    if payload.is_private:
        if payload.url_type not in _MEDIATED_TYPES:
            raise HTTPException(status_code=400, detail="Private applications require a managed alias or embedded app")
        if payload.teams or shared_user_ids:
            raise HTTPException(status_code=400, detail="Private applications cannot have team or user shares")
    elif not is_admin and not (payload.teams or shared_user_ids):
        _require_nonempty_teams(payload.teams)

    if payload.url_type == "embedded":
        _enforce_embedded_alias_exists(conn, payload.url, actor.get("id"))

    # Passing the authenticated user's identity upstream must never be
    # possible for anything other than an authenticated managed alias: it is
    # rejected outright rather than silently downgraded, since the caller
    # explicitly asked for it.
    effective_alias_auth_required = (
        True if (payload.is_private or shared_user_ids) else payload.alias_auth_required
    )
    if payload.pass_authenticated_user:
        if payload.url_type != "alias":
            raise HTTPException(
                status_code=400,
                detail="Passing the authenticated user header requires a managed alias",
            )
        if not effective_alias_auth_required:
            raise HTTPException(
                status_code=400,
                detail="Passing the authenticated user header requires alias authentication",
            )
        if not get_settings().enable_auth:
            raise HTTPException(
                status_code=400,
                detail="Passing the authenticated user header requires authentication to be enabled",
            )

    # Administrators and self-service users bypass review; everyone else queues
    # the application for approval.
    auto_approved = is_admin or bool(actor.get("self_service"))
    approval_status = "approved" if auto_approved else "pending"
    # The synthetic auth-disabled identity has id 0 and no users row, so leave
    # the owner unset rather than violate the created_by foreign key.
    created_by = actor["id"] if actor.get("id") else None

    # Alias apps carry their own upstream settings. The host is prefilled from
    # the user's configured apps host in the UI, but users may override it.
    apps_server = payload.apps_server
    apps_protocol = payload.apps_protocol
    apps_port = payload.apps_port
    apps_path = payload.apps_path

    app = repository.create_application(
        conn,
        name=payload.name,
        url=payload.url,
        url_type=payload.url_type,
        description=payload.description,
        icon_url=payload.icon_url,
        teams=payload.teams,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
        approval_status=approval_status,
        created_by=created_by,
        apps_server=apps_server,
        apps_protocol=apps_protocol,
        apps_port=apps_port,
        apps_path=apps_path,
        alias_auth_required=effective_alias_auth_required,
        apps_rewrite_root=payload.apps_rewrite_root,
        pass_authenticated_user=payload.pass_authenticated_user,
        is_private=payload.is_private,
        shared_user_ids=shared_user_ids,
    )
    logger.info(
        "Application created id=%s name=%r url_type=%s teams=%s approval=%s "
        "by=%r",
        app["id"],
        app["name"],
        app["url_type"],
        app["teams"],
        approval_status,
        actor.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_APPLICATION,
        action="create" if approval_status == "approved" else "request",
        actor=actor,
        target_type="application",
        target_id=app["id"],
        target_name=app["name"],
        detail=f"teams={app['teams']} approval={approval_status}",
    )
    # Apps that are auto-approved on create (admin/self-service) get their alias
    # pushed to the reverse proxy too. Commit first so the push sees the row.
    if approval_status == "approved":
        conn.commit()
        _push_alias_on_approval(app["id"], actor)
    result = repository.get_application(conn, app["id"], include_creator=True)
    assert result is not None
    return _app_out(result, include_creator=True)


@router.post("/applications/reorder", response_model=list[ApplicationOut])
def reorder_applications(
    payload: schemas.ReorderApplicationsRequest,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ApplicationOut]:
    """Atomically persist a staged drag/keyboard reorder (issue_local_032).

    Each group is a set of applications the caller reordered together in the
    UI -- always sharing the same visible ownership scope and approval
    status, since reordering across those boundaries is not offered. Every
    group is independently validated and applied; the whole request is
    rejected (nothing is changed) if any group fails validation or is stale.
    """
    is_admin = actor.get("role") == "admin"
    touched_ids: list[int] = []
    # Hold the write lock for the whole request (all groups): the earlier
    # per-group read of the current order must not be followed by a window in
    # which a concurrent request could commit a conflicting change before this
    # request's own write -- that would let the 409 staleness check above pass
    # for two racing requests when only one should logically succeed. A plain
    # SELECT does not open a transaction, so without this, the read and this
    # request's own UPDATE could straddle another connection's entire
    # read-modify-write cycle. BEGIN IMMEDIATE takes the lock immediately.
    conn.execute("BEGIN IMMEDIATE")
    for group in payload.groups:
        rows = repository.get_applications_by_ids(conn, group.application_ids)
        if not is_admin:
            # Deliberately identical response for "doesn't exist" and "exists
            # but isn't yours": a non-admin must not be able to learn whether
            # an arbitrary application id exists (e.g. a private application
            # they cannot otherwise see) by comparing status codes/messages.
            if len(rows) != len(group.application_ids) or any(
                row.get("created_by") != actor.get("id") for row in rows
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You may only reorder your own applications.",
                )
        elif len(rows) != len(group.application_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more applications in the request no longer exist.",
            )
        # Defense in depth: the client only ever groups applications that share
        # an approval status, but never trust that without re-checking, since a
        # cross-status swap would otherwise appear to save and then silently
        # snap back (management lists sort by approval status first).
        if len({row["approval_status"] for row in rows}) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="All applications in a reorder group must share the same "
                "approval status.",
            )
        # Conflict check: the group's current DB order (by sort_order) must
        # exactly match what the client believed it was reordering.
        current_order = [
            row["id"] for row in sorted(rows, key=lambda r: r["sort_order"])
        ]
        if current_order != group.expected_application_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This application list changed elsewhere; reload and "
                "try again.",
            )
        # Assign fresh, strictly increasing sort_order values anchored at the
        # group's current minimum. Newly created applications commonly all
        # share the same default sort_order (0), so simply permuting the
        # existing values would be a no-op when they are not already
        # distinct; this always produces a distinct order for the group while
        # keeping it anchored near its previous position (ties against
        # applications outside the group are broken by name, then id, so
        # exact numeric adjacency across groups/owners is not required).
        base = min(row["sort_order"] for row in rows)
        new_sort_orders = [base + i for i in range(len(group.application_ids))]
        repository.reorder_applications(conn, group.application_ids, new_sort_orders)
        touched_ids.extend(group.application_ids)
    # Cap the logged id list: up to 20 groups of 500 ids each could otherwise
    # produce an oversized audit row for a single request.
    _AUDIT_ID_PREVIEW_LIMIT = 50
    ids_preview = touched_ids[:_AUDIT_ID_PREVIEW_LIMIT]
    ids_suffix = (
        f" (+{len(touched_ids) - _AUDIT_ID_PREVIEW_LIMIT} more)"
        if len(touched_ids) > _AUDIT_ID_PREVIEW_LIMIT
        else ""
    )
    audit.record(
        conn,
        category=audit.CATEGORY_APPLICATION,
        action="reorder",
        actor=actor,
        target_type="application",
        target_id=touched_ids[0] if touched_ids else 0,
        target_name=f"{len(touched_ids)} application(s)",
        detail=f"application_ids={ids_preview}{ids_suffix}",
    )
    out = []
    for application_id in touched_ids:
        updated = repository.get_application(conn, application_id, include_creator=True)
        assert updated is not None
        out.append(_app_out(updated, include_creator=True))
    return out


@router.patch("/applications/{application_id}", response_model=ApplicationOut)
def update_application(
    application_id: int,
    payload: UpdateApplicationRequest,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplicationOut:
    existing = repository.get_application(conn, application_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Application not found")

    is_admin = actor["role"] == "admin"
    is_owner = bool(actor.get("id")) and existing["created_by"] == actor["id"]
    if not (is_admin or is_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only modify applications you created",
        )

    # Only administrators may move an application through the approval workflow.
    if payload.approval_status is not None and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can change approval status",
        )
    if payload.created_by is not None:
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can transfer ownership",
            )
        if repository.get_user_by_id(conn, payload.created_by) is None:
            raise HTTPException(status_code=404, detail="New owner not found")

    has_staged_change = (
        bool(existing.get("pending_alias"))
        or existing.get("pending_is_active") is not None
        or existing.get("pending_alias_auth_required") is not None
        or existing.get("pending_apps_rewrite_root") is not None
        or existing.get("pending_pass_authenticated_user") is not None
    )
    # An approved application cannot be rejected; only disable or delete it.
    # Staged changes on an otherwise-approved app can be approved, but they are
    # not modelled as a rejected application state.
    if (
        payload.approval_status == "rejected"
        and existing["approval_status"] == "approved"
        and not has_staged_change
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot reject an already-approved application; "
                "disable or delete it instead"
            ),
        )

    if payload.teams is not None:
        _validate_teams(conn, payload.teams)
    resulting_private = payload.is_private if payload.is_private is not None else existing["is_private"]
    resulting_type = payload.url_type or existing["url_type"]
    resulting_teams = payload.teams if payload.teams is not None else existing["teams"]
    resulting_users = payload.shared_user_ids if payload.shared_user_ids is not None else existing.get("shared_user_ids", [])
    resulting_owner = payload.created_by if payload.created_by is not None else existing.get("created_by")
    resulting_users = _validate_shared_users(conn, resulting_users, resulting_owner)
    if resulting_private:
        if resulting_type not in _MEDIATED_TYPES:
            raise HTTPException(status_code=400, detail="Private applications require a managed alias or embedded app")
        if resulting_teams or resulting_users:
            raise HTTPException(status_code=400, detail="Private applications cannot have team or user shares")
    elif not is_admin and not (resulting_teams or resulting_users):
        raise HTTPException(status_code=400, detail="Select at least one team or shared user")
    if (resulting_private or resulting_users) and not get_settings().enable_auth:
        raise HTTPException(status_code=400, detail="Private or user-restricted applications require authentication to be enabled")
    if resulting_users and resulting_type not in _MEDIATED_TYPES:
        raise HTTPException(status_code=400, detail="Specific-user sharing requires a managed alias or embedded app")
    # Embedded apps are not served through nginx, so alias auth is not applicable
    # to them; the alias-auth constraint only applies to alias apps.
    if (resulting_private or resulting_users) and resulting_type == "alias" and payload.alias_auth_required is False:
        raise HTTPException(status_code=400, detail="Private or user-restricted aliases must require authentication")
    # Passing the authenticated user's identity upstream is rejected outright
    # whenever the resulting state cannot guarantee it: not a managed alias,
    # not requiring alias authentication, or global auth disabled.
    resulting_alias_auth_required = (
        True
        if (resulting_private or resulting_users)
        else (
            payload.alias_auth_required
            if payload.alias_auth_required is not None
            else existing["alias_auth_required"]
        )
    )
    resulting_pass_authenticated_user = (
        payload.pass_authenticated_user
        if payload.pass_authenticated_user is not None
        else existing.get("pass_authenticated_user", False)
    )
    if resulting_pass_authenticated_user:
        if resulting_type != "alias":
            raise HTTPException(
                status_code=400,
                detail="Passing the authenticated user header requires a managed alias",
            )
        if not resulting_alias_auth_required:
            raise HTTPException(
                status_code=400,
                detail="Passing the authenticated user header requires alias authentication",
            )
        if not get_settings().enable_auth:
            raise HTTPException(
                status_code=400,
                detail="Passing the authenticated user header requires authentication to be enabled",
            )
    # Re-validate a changed url against the RESULTING type. The Update schema
    # accepts either an alias slug or an http URL when url_type is omitted (it
    # cannot know the stored type); enforce the correct shape here now that the
    # resulting type is known. (Embedded is additionally checked below against
    # an existing owner alias.)
    if payload.url is not None:
        try:
            if resulting_type in ("alias", "embedded"):
                payload.url = schemas._validate_alias(payload.url)
            else:
                payload.url = schemas._validate_http_url(payload.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Embedded apps must frame one of the owner's own existing aliases. Only
    # enforce when the source (url), the type, or the owner is actually being
    # changed: re-validating an unchanged reference on every edit would freeze
    # metadata edits (rename, enable/disable, re-team) if the referenced alias
    # were later removed. A broken reference is surfaced to the user via a card
    # warning instead of blocking unrelated edits.
    changing_embedded_source = (
        resulting_type == "embedded"
        and (
            payload.url is not None
            or (payload.url_type is not None and payload.url_type != existing["url_type"])
            or (payload.created_by is not None and payload.created_by != existing.get("created_by"))
        )
    )
    if changing_embedded_source:
        _enforce_embedded_alias_exists(
            conn, payload.url if payload.url is not None else existing["url"],
            resulting_owner,
        )

    # Resolve the resulting approval status.
    if is_admin:
        # Admin edits leave the status untouched unless one is supplied.
        new_status = payload.approval_status
    else:
        # A non-self-service owner's substantive edits re-enter the review
        # queue; toggling is_active or sort_order alone does not.
        substantive = any(
            value is not None
            for value in (
                payload.name,
                payload.url,
                payload.url_type,
                payload.description,
                payload.icon_url,
                payload.teams,
                payload.is_active,
                payload.apps_protocol,
                payload.apps_port,
                payload.apps_path,
                payload.alias_auth_required,
                payload.apps_rewrite_root,
                payload.pass_authenticated_user,
                payload.is_private,
                payload.shared_user_ids,
            )
        )
        new_status = (
            "pending"
            if substantive and not actor.get("self_service")
            else None
        )

    # Alias-change staging: when a non-self-service owner changes the alias of an
    # already-approved alias application, the application keeps serving its
    # current alias while the new value waits for approval. The new alias is held
    # in ``pending_alias`` and only applied to ``url`` once an administrator
    # approves. Admin and self-service edits apply immediately (no staging).
    resolved_url_type = payload.url_type or existing["url_type"]
    stages_alias = (
        not is_admin
        and not actor.get("self_service")
        and existing["approval_status"] == "approved"
        and resolved_url_type == "alias"
        and payload.url is not None
        and payload.url != existing["url"]
    )
    stages_active = (
        not is_admin
        and not actor.get("self_service")
        and existing["approval_status"] == "approved"
        and payload.is_active is not None
        and payload.is_active != existing["is_active"]
    )
    stages_alias_auth = (
        not is_admin
        and not actor.get("self_service")
        and existing["approval_status"] == "approved"
        and resolved_url_type == "alias"
        and payload.alias_auth_required is not None
        and payload.alias_auth_required != existing["alias_auth_required"]
    )
    stages_rewrite_root = (
        not is_admin
        and not actor.get("self_service")
        and existing["approval_status"] == "approved"
        and resolved_url_type == "alias"
        and payload.apps_rewrite_root is not None
        and payload.apps_rewrite_root != existing.get("apps_rewrite_root", False)
    )
    stages_pass_authenticated_user = (
        not is_admin
        and not actor.get("self_service")
        and existing["approval_status"] == "approved"
        and resolved_url_type == "alias"
        and payload.pass_authenticated_user is not None
        and payload.pass_authenticated_user != existing.get("pass_authenticated_user", False)
    )
    changes_access_scope = (
        payload.is_private is not None
        and payload.is_private != existing.get("is_private", False)
    ) or (
        payload.shared_user_ids is not None
        and set(payload.shared_user_ids) != set(existing.get("shared_user_ids", []))
    )
    if changes_access_scope and (
        stages_alias or stages_active or stages_alias_auth or stages_rewrite_root
        or stages_pass_authenticated_user
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Submit alias/active/auth/rewrite changes separately from privacy or user-sharing changes",
        )
    if stages_alias:
        # Do not change the live URL; record the requested alias as pending and
        # leave the application active and approved on its current config until
        # an administrator approves the change.
        url_for_update: str | None = None
        pending_alias_for_update: str | None = payload.url
        new_status = "approved"
    else:
        url_for_update = payload.url
        pending_alias_for_update = None
    if stages_active:
        is_active_for_update: bool | None = None
        pending_is_active_for_update: bool | None = payload.is_active
        new_status = "approved"
    else:
        is_active_for_update = payload.is_active
        pending_is_active_for_update = None
    if stages_alias_auth:
        alias_auth_required_for_update: bool | None = None
        pending_alias_auth_required_for_update: bool | None = payload.alias_auth_required
        new_status = "approved"
    else:
        alias_auth_required_for_update = payload.alias_auth_required
        pending_alias_auth_required_for_update = None
    if stages_rewrite_root:
        apps_rewrite_root_for_update: bool | None = None
        pending_apps_rewrite_root_for_update: bool | None = payload.apps_rewrite_root
        new_status = "approved"
    else:
        apps_rewrite_root_for_update = payload.apps_rewrite_root
        pending_apps_rewrite_root_for_update = None
    if stages_pass_authenticated_user:
        pass_authenticated_user_for_update: bool | None = None
        pending_pass_authenticated_user_for_update: bool | None = payload.pass_authenticated_user
        new_status = "approved"
    else:
        pass_authenticated_user_for_update = payload.pass_authenticated_user
        pending_pass_authenticated_user_for_update = None

    config_changed = _nginx_config_changed(existing, payload, is_admin=is_admin)
    mark_needs_push = (
        existing["approval_status"] == "approved"
        and existing["url_type"] == "alias"
        and config_changed
        and not (is_admin or actor.get("self_service"))
    )

    updated = repository.update_application(
        conn,
        application_id,
        name=payload.name,
        url=url_for_update,
        url_type=payload.url_type,
        description=payload.description,
        icon_url=payload.icon_url,
        is_active=is_active_for_update,
        approval_status=new_status,
        sort_order=payload.sort_order,
        created_by=payload.created_by,
        teams=payload.teams,
        # Alias upstream settings may be changed by the owner or an admin.
        apps_server=payload.apps_server,
        apps_protocol=payload.apps_protocol,
        apps_port=payload.apps_port,
        apps_path=payload.apps_path,
        alias_auth_required=(True if resulting_private or resulting_users else alias_auth_required_for_update),
        apps_rewrite_root=apps_rewrite_root_for_update,
        pass_authenticated_user=pass_authenticated_user_for_update,
        is_private=payload.is_private,
        shared_user_ids=payload.shared_user_ids,
        pending_alias=pending_alias_for_update,
        pending_is_active=pending_is_active_for_update,
        pending_alias_auth_required=pending_alias_auth_required_for_update,
        pending_apps_rewrite_root=pending_apps_rewrite_root_for_update,
        pending_pass_authenticated_user=pending_pass_authenticated_user_for_update,
        needs_push=True if mark_needs_push else None,
    )
    assert updated is not None
    if stages_alias:
        logger.info(
            "Application alias change staged id=%s pending_alias=%r by=%r",
            application_id,
            payload.url,
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="alias_change_requested",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"pending_alias={payload.url}",
        )
    if stages_active:
        requested = "enable" if payload.is_active else "disable"
        logger.info(
            "Application active-state change staged id=%s pending_is_active=%s by=%r",
            application_id,
            payload.is_active,
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action=f"{requested}_requested",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"pending_is_active={payload.is_active}",
        )
    if stages_alias_auth:
        logger.info(
            "Application alias auth change staged id=%s pending=%s by=%r",
            application_id,
            payload.alias_auth_required,
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="alias_auth_change_requested",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"pending_alias_auth_required={payload.alias_auth_required}",
        )
    if stages_pass_authenticated_user:
        logger.info(
            "Application authenticated-user header change staged id=%s pending=%s by=%r",
            application_id,
            payload.pass_authenticated_user,
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="pass_authenticated_user_change_requested",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"pending_pass_authenticated_user={payload.pass_authenticated_user}",
        )
    if is_admin and payload.approval_status is not None:
        logger.info(
            "Application approval changed id=%s status=%s by=%r",
            application_id,
            payload.approval_status,
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="approve" if payload.approval_status == "approved" else "reject",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"status={payload.approval_status}",
        )
    logger.info(
        "Application updated id=%s name=%r is_active=%s teams=%s status=%s "
        "by=%r",
        application_id,
        updated["name"],
        payload.is_active,
        payload.teams,
        updated["approval_status"],
        actor.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_APPLICATION,
        action="update",
        actor=actor,
        target_type="application",
        target_id=application_id,
        target_name=updated["name"],
        detail=f"is_active={payload.is_active} teams={payload.teams} "
        f"status={updated['approval_status']}",
    )
    # Reverse-proxy push triggers (out-of-band; each records its own result +
    # audit entry; the approval is committed first so the push sees it):
    #   1. A normal pending/rejected -> approved transition.
    #   2. An administrator approving a *staged alias change* on an application
    #      that is already approved (the app stayed live on its old alias while
    #      the new one waited for review). The staged alias is applied to the
    #      live URL and cleared before the push renders the proxy config.
    staged = existing.get("pending_alias") or ""
    staged_is_active = existing.get("pending_is_active")
    staged_alias_auth = existing.get("pending_alias_auth_required")
    staged_rewrite_root = existing.get("pending_apps_rewrite_root")
    normal_approval = (
        is_admin
        and payload.approval_status == "approved"
        and existing["approval_status"] != "approved"
    )
    staged_approval = (
        is_admin and payload.approval_status == "approved" and bool(staged)
    )
    staged_active_approval = (
        is_admin
        and payload.approval_status == "approved"
        and staged_is_active is not None
    )
    staged_alias_auth_approval = (
        is_admin
        and payload.approval_status == "approved"
        and staged_alias_auth is not None
    )
    staged_rewrite_root_approval = (
        is_admin
        and payload.approval_status == "approved"
        and staged_rewrite_root is not None
    )
    staged_pass_authenticated_user = existing.get("pending_pass_authenticated_user")
    staged_pass_authenticated_user_approval = (
        is_admin
        and payload.approval_status == "approved"
        and staged_pass_authenticated_user is not None
    )
    if staged_approval:
        repository.update_application(
            conn,
            application_id,
            url=staged,
            pending_alias="",
        )
        logger.info(
            "Applied staged alias change id=%s url=%r by=%r",
            application_id,
            staged,
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="alias_change_approved",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"url={staged}",
        )
    if staged_active_approval:
        repository.update_application(
            conn,
            application_id,
            is_active=bool(staged_is_active),
            clear_pending_is_active=True,
        )
        logger.info(
            "Applied staged active-state change id=%s is_active=%s by=%r",
            application_id,
            bool(staged_is_active),
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="active_change_approved",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"is_active={bool(staged_is_active)}",
        )
    if staged_alias_auth_approval:
        current_scope = repository.get_application(conn, application_id)
        assert current_scope is not None
        applied_alias_auth = bool(staged_alias_auth)
        if current_scope["is_private"] or current_scope.get("shared_user_ids"):
            applied_alias_auth = True
        update_kwargs: dict[str, Any] = {
            "alias_auth_required": applied_alias_auth,
            "clear_pending_alias_auth_required": True,
        }
        # Never leave the authenticated-user header live once alias
        # authentication is no longer required -- there would be no
        # authenticated session left to assert an identity for.
        if not applied_alias_auth and current_scope.get("pass_authenticated_user"):
            update_kwargs["pass_authenticated_user"] = False
        repository.update_application(conn, application_id, **update_kwargs)
        logger.info(
            "Applied staged alias auth change id=%s required=%s by=%r",
            application_id,
            applied_alias_auth,
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="alias_auth_change_approved",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"alias_auth_required={bool(staged_alias_auth)}",
        )
    if staged_rewrite_root_approval:
        repository.update_application(
            conn,
            application_id,
            apps_rewrite_root=bool(staged_rewrite_root),
            clear_pending_apps_rewrite_root=True,
        )
        logger.info(
            "Applied staged rewrite-root change id=%s enabled=%s by=%r",
            application_id,
            bool(staged_rewrite_root),
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="rewrite_root_change_approved",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"apps_rewrite_root={bool(staged_rewrite_root)}",
        )
    if staged_pass_authenticated_user_approval:
        current_scope = repository.get_application(conn, application_id)
        assert current_scope is not None
        applied_pass_authenticated_user = bool(staged_pass_authenticated_user)
        # Never let a previously valid staged request silently start exposing
        # identity if the live alias no longer requires authentication (e.g.
        # authentication was disabled between the request and the approval).
        if applied_pass_authenticated_user and (
            current_scope["url_type"] != "alias"
            or not current_scope["alias_auth_required"]
            or not get_settings().enable_auth
        ):
            applied_pass_authenticated_user = False
        repository.update_application(
            conn,
            application_id,
            pass_authenticated_user=applied_pass_authenticated_user,
            clear_pending_pass_authenticated_user=True,
        )
        logger.info(
            "Applied staged authenticated-user header change id=%s enabled=%s by=%r",
            application_id,
            applied_pass_authenticated_user,
            actor.get("username"),
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="pass_authenticated_user_change_approved",
            actor=actor,
            target_type="application",
            target_id=application_id,
            target_name=updated["name"],
            detail=f"pass_authenticated_user={applied_pass_authenticated_user}",
        )
    auto_config_push = (
        (is_admin or actor.get("self_service"))
        and config_changed
        and existing["approval_status"] == "approved"
        and (updated["approval_status"] == "approved")
        and (existing["url_type"] == "alias" or updated["url_type"] == "alias")
        and not (
            normal_approval
            or staged_approval
            or staged_active_approval
            or staged_alias_auth_approval
            or staged_rewrite_root_approval
            or staged_pass_authenticated_user_approval
        )
    )
    if (
        normal_approval
        or staged_approval
        or staged_active_approval
        or staged_alias_auth_approval
        or staged_rewrite_root_approval
        or staged_pass_authenticated_user_approval
        or auto_config_push
    ):
        conn.commit()
        _push_alias_on_approval(application_id, actor)
    result = repository.get_application(conn, application_id, include_creator=True)
    assert result is not None
    return _app_out(result, include_creator=True)


@router.delete("/applications/{application_id}", response_model=MessageOut)
def delete_application(
    application_id: int,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    existing = repository.get_application(conn, application_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Application not found")

    is_admin = actor["role"] == "admin"
    is_owner = bool(actor.get("id")) and existing["created_by"] == actor["id"]
    if not (is_admin or is_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete applications you created",
        )

    # Capture what the alias-removal needs before the row is deleted.
    deleted_name = existing["name"]
    deleted_url_type = existing["url_type"]

    repository.delete_application(conn, application_id)
    logger.info(
        "Application deleted id=%s name=%r by=%r",
        application_id,
        deleted_name,
        actor.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_APPLICATION,
        action="delete",
        actor=actor,
        target_type="application",
        target_id=application_id,
        target_name=deleted_name,
    )
    # Remove the app's alias block from the reverse proxy (out-of-band; records
    # its own audit entry). Commit the deletion first.
    conn.commit()
    _remove_alias_on_delete(application_id, deleted_name, deleted_url_type, actor)
    return MessageOut(detail="Application deleted")


@router.post(
    "/applications/{application_id}/push-retry", response_model=ApplicationOut
)
def retry_application_push(
    application_id: int,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplicationOut:
    """Re-run the reverse-proxy alias push for an already-approved application.

    Used to retry after a failed push. Only approved applications can be pushed;
    the push records its own result + audit entry (failure-safe).
    """
    existing = repository.get_application(conn, application_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if existing["approval_status"] != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only approved applications can be pushed.",
        )
    # The push uses its own connection; commit any pending read state first.
    conn.commit()
    _push_alias_on_approval(application_id, actor)
    result = repository.get_application(conn, application_id, include_creator=True)
    assert result is not None
    return _app_out(result, include_creator=True)
