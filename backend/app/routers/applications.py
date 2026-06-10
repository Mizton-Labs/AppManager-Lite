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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, repository, reverse_proxy
from ..db import get_connection
from ..deps import get_current_user, get_db, require_admin, verify_csrf
from ..schemas import (
    ApplicationOut,
    CreateApplicationRequest,
    MessageOut,
    UpdateApplicationRequest,
)

router = APIRouter(tags=["applications"])

logger = logging.getLogger(__name__)


def _app_out(
    app: dict[str, Any], *, include_creator: bool = False
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
        apps_port=app.get("apps_port", "") if include_creator else "",
        pending_alias=app.get("pending_alias", "") if include_creator else "",
        pending_is_active=app.get("pending_is_active") if include_creator else None,
        needs_push=bool(app.get("needs_push")) if include_creator else False,
    )


# Cap stored push transcripts so a verbose remote error can't bloat the row.
_MAX_PUSH_LOG = 16000


def _nginx_config_changed(
    existing: dict[str, Any], payload: UpdateApplicationRequest, *, is_admin: bool
) -> bool:
    checks: tuple[tuple[str, Any], ...] = (
        ("name", payload.name),
        ("url", payload.url),
        ("url_type", payload.url_type),
        ("apps_port", payload.apps_port),
        ("is_active", payload.is_active),
    )
    if any(value is not None and value != existing[key] for key, value in checks):
        return True
    return bool(
        is_admin
        and payload.apps_server is not None
        and payload.apps_server != existing["apps_server"]
    )


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
                #   - port: the application's own port (set by any user).
                owner_id = app.get("created_by")
                owner = (
                    repository.get_user_by_id(conn, owner_id)
                    if owner_id
                    else None
                )
                apps_port = app.get("apps_port") or ""
                apps_server = app.get("apps_server") or (
                    (owner["apps_server"] or owner["apps_server_ip"]) if owner else ""
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
                        apps_port=apps_port,
                        alias=app["url"],
                        app_name=app["name"],
                        app_id=application_id,
                        is_active=app["is_active"],
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
            visible_team_names=None if is_admin else user["teams"],
            active_only=active_only,
        )
    elif team is not None:
        if team not in set(repository.list_team_names(conn)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Unknown team"
            )
        apps = repository.list_applications_for_team(
            conn, team, active_only=active_only
        )
    elif is_admin:
        apps = repository.list_all_applications(conn, active_only=active_only)
    else:
        apps = repository.list_applications_for_teams(conn, user["teams"])

    return [_app_out(a) for a in apps]


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


@router.get("/applications/manage", response_model=list[ApplicationOut])
def list_managed_applications(
    _actor: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ApplicationOut]:
    """Every application with its creator and status, for administrators."""
    apps = repository.list_all_applications_admin(conn)
    return [_app_out(a, include_creator=True) for a in apps]


@router.post("/applications", response_model=ApplicationOut, status_code=201)
def create_application(
    payload: CreateApplicationRequest,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplicationOut:
    is_admin = actor["role"] == "admin"
    _validate_teams(conn, payload.teams)
    if not is_admin:
        _require_nonempty_teams(payload.teams)

    # Administrators and self-service users bypass review; everyone else queues
    # the application for approval.
    auto_approved = is_admin or bool(actor.get("self_service"))
    approval_status = "approved" if auto_approved else "pending"
    # The synthetic auth-disabled identity has id 0 and no users row, so leave
    # the owner unset rather than violate the created_by foreign key.
    created_by = actor["id"] if actor.get("id") else None

    # The application's own port may be set by any user (each alias app has its
    # own port). The application's own server host is administrator-only; other
    # users rely on the owning user's configured apps host (resolved at push
    # time). Admins -- who have no per-user apps host -- can set the app's host.
    apps_server = payload.apps_server if is_admin else ""
    apps_port = payload.apps_port

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
        apps_port=apps_port,
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

    has_staged_change = bool(existing.get("pending_alias")) or existing.get(
        "pending_is_active"
    ) is not None
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
        if not is_admin:
            _require_nonempty_teams(payload.teams)

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
                payload.apps_port,
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
        # The application's own server host is administrator-only; its port may
        # be changed by any user (owner or admin).
        apps_server=payload.apps_server if is_admin else None,
        apps_port=payload.apps_port,
        pending_alias=pending_alias_for_update,
        pending_is_active=pending_is_active_for_update,
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
    auto_config_push = (
        (is_admin or actor.get("self_service"))
        and config_changed
        and existing["approval_status"] == "approved"
        and (updated["approval_status"] == "approved")
        and (existing["url_type"] == "alias" or updated["url_type"] == "alias")
        and not (normal_approval or staged_approval or staged_active_approval)
    )
    if normal_approval or staged_approval or staged_active_approval or auto_config_push:
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
