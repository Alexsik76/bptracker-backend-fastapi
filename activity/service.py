from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import String, case, delete, func, text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from activity.models import ErrorEvent, RequestEvent
from activity.schemas import (
    ErrorEventRead,
    PaginatedErrorEvents,
    PaginatedRequestEvents,
    RequestEventRead,
    UsageGroupBy,
    UsageStatItem,
    UserOverviewItem,
)
from auth.models import Session
from auth.webauthn.models import WebAuthnCredential
from measurements.models import Measurement
from nutrition.models import GenerationLog
from prescriptions.models import Prescription
from reminders.models import IntakeReport
from users.models import User


async def get_usage_stats(
    session: AsyncSession,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    group_by: UsageGroupBy = UsageGroupBy.MODULE,
    user_id: UUID | None = None,
    module: str | None = None,
) -> list[UsageStatItem]:
    conditions = []
    if date_from is not None:
        conditions.append(RequestEvent.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(RequestEvent.occurred_at <= date_to)
    if user_id is not None:
        conditions.append(RequestEvent.user_id == user_id)
    if module is not None:
        conditions.append(RequestEvent.module == module)

    requests_expr = func.count(RequestEvent.id).label("requests")
    errors_expr = func.count(case((RequestEvent.status_code >= 500, 1))).label("errors")
    client_errors_expr = func.count(
        case(((RequestEvent.status_code >= 400) & (RequestEvent.status_code <= 499), 1))
    ).label("client_errors")
    avg_ms_expr = func.coalesce(func.avg(RequestEvent.duration_ms), 0.0).label("avg_ms")
    p95_ms_expr = func.coalesce(
        func.percentile_cont(0.95).within_group(RequestEvent.duration_ms), 0.0
    ).label("p95_ms")

    if group_by == UsageGroupBy.USER:
        key_expr = func.coalesce(func.cast(RequestEvent.user_id, String), "anonymous").label("key")
        label_expr = User.email.label("label")
        stmt = (
            select(
                key_expr,
                label_expr,
                requests_expr,
                errors_expr,
                client_errors_expr,
                avg_ms_expr,
                p95_ms_expr,
            )
            .select_from(RequestEvent)
            .outerjoin(User, User.id == RequestEvent.user_id)
            .where(*conditions)
            .group_by(RequestEvent.user_id, User.email)
            .order_by(requests_expr.desc(), key_expr.asc())
        )
    elif group_by == UsageGroupBy.MODULE:
        key_expr = RequestEvent.module.label("key")
        label_expr = RequestEvent.module.label("label")
        stmt = (
            select(
                key_expr,
                label_expr,
                requests_expr,
                errors_expr,
                client_errors_expr,
                avg_ms_expr,
                p95_ms_expr,
            )
            .select_from(RequestEvent)
            .where(*conditions)
            .group_by(RequestEvent.module)
            .order_by(requests_expr.desc(), key_expr.asc())
        )
    elif group_by == UsageGroupBy.CLIENT:
        key_expr = RequestEvent.client.label("key")
        label_expr = RequestEvent.client.label("label")
        stmt = (
            select(
                key_expr,
                label_expr,
                requests_expr,
                errors_expr,
                client_errors_expr,
                avg_ms_expr,
                p95_ms_expr,
            )
            .select_from(RequestEvent)
            .where(*conditions)
            .group_by(RequestEvent.client)
            .order_by(requests_expr.desc(), key_expr.asc())
        )
    elif group_by == UsageGroupBy.DAY:
        day_expr = func.to_char(RequestEvent.occurred_at, text("'YYYY-MM-DD'"))
        key_expr = day_expr.label("key")
        label_expr = day_expr.label("label")
        stmt = (
            select(
                key_expr,
                label_expr,
                requests_expr,
                errors_expr,
                client_errors_expr,
                avg_ms_expr,
                p95_ms_expr,
            )
            .select_from(RequestEvent)
            .where(*conditions)
            .group_by(day_expr)
            .order_by(requests_expr.desc(), day_expr.asc())
        )
    elif group_by == UsageGroupBy.ROUTE:
        key_expr = RequestEvent.route.label("key")
        label_expr = RequestEvent.route.label("label")
        stmt = (
            select(
                key_expr,
                label_expr,
                requests_expr,
                errors_expr,
                client_errors_expr,
                avg_ms_expr,
                p95_ms_expr,
            )
            .select_from(RequestEvent)
            .where(*conditions)
            .group_by(RequestEvent.route)
            .order_by(requests_expr.desc(), key_expr.asc())
        )

    rows = (await session.exec(stmt)).all()
    return [
        UsageStatItem(
            key=row.key,
            label=row.label,
            requests=row.requests,
            errors=row.errors,
            client_errors=row.client_errors,
            avg_ms=round(float(row.avg_ms), 1),
            p95_ms=round(float(row.p95_ms), 1),
        )
        for row in rows
    ]


async def get_stats_overview(session: AsyncSession) -> list[UserOverviewItem]:
    now = datetime.now(UTC)
    since_30d = now - timedelta(days=30)

    last_request_at = (
        select(func.max(RequestEvent.occurred_at))
        .where(RequestEvent.user_id == User.id)
        .scalar_subquery()
    )
    requests_30d = (
        select(func.count(RequestEvent.id))
        .where((RequestEvent.user_id == User.id) & (RequestEvent.occurred_at >= since_30d))
        .scalar_subquery()
    )
    measurements_total = (
        select(func.count(Measurement.id)).where(Measurement.user_id == User.id).scalar_subquery()
    )
    measurements_30d = (
        select(func.count(Measurement.id))
        .where((Measurement.user_id == User.id) & (Measurement.recorded_at >= since_30d))
        .scalar_subquery()
    )
    prescriptions_active = (
        select(func.count(Prescription.id))
        .where((Prescription.user_id == User.id) & (Prescription.is_active.is_(True)))
        .scalar_subquery()
    )
    intake_reports_30d = (
        select(func.count(IntakeReport.id))
        .where((IntakeReport.user_id == User.id) & (IntakeReport.recorded_at >= since_30d))
        .scalar_subquery()
    )
    menu_generations_30d = (
        select(func.count(GenerationLog.id))
        .where((GenerationLog.user_id == User.id) & (GenerationLog.created_at >= since_30d))
        .scalar_subquery()
    )
    webauthn_credentials = (
        select(func.count(WebAuthnCredential.id))
        .where(WebAuthnCredential.user_id == User.id)
        .scalar_subquery()
    )
    active_sessions = (
        select(func.count(Session.id))
        .where(
            (Session.user_id == User.id)
            & (Session.revoked_at.is_(None))
            & (Session.expires_at > now)
        )
        .scalar_subquery()
    )

    stmt = (
        select(
            User.email,
            User.display_name,
            User.created_at,
            last_request_at.label("last_request_at"),
            func.coalesce(requests_30d, 0).label("requests_30d"),
            func.coalesce(measurements_total, 0).label("measurements_total"),
            func.coalesce(measurements_30d, 0).label("measurements_30d"),
            func.coalesce(prescriptions_active, 0).label("prescriptions_active"),
            func.coalesce(intake_reports_30d, 0).label("intake_reports_30d"),
            func.coalesce(menu_generations_30d, 0).label("menu_generations_30d"),
            func.coalesce(webauthn_credentials, 0).label("webauthn_credentials"),
            func.coalesce(active_sessions, 0).label("active_sessions"),
        )
        .select_from(User)
        .order_by(User.created_at.desc())
    )

    rows = (await session.exec(stmt)).all()
    return [
        UserOverviewItem(
            email=row.email,
            display_name=row.display_name,
            created_at=row.created_at,
            last_request_at=row.last_request_at,
            requests_30d=row.requests_30d,
            measurements_total=row.measurements_total,
            measurements_30d=row.measurements_30d,
            prescriptions_active=row.prescriptions_active,
            intake_reports_30d=row.intake_reports_30d,
            menu_generations_30d=row.menu_generations_30d,
            webauthn_credentials=row.webauthn_credentials,
            active_sessions=row.active_sessions,
        )
        for row in rows
    ]


async def list_events(
    session: AsyncSession,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    user_id: UUID | None = None,
    module: str | None = None,
    client: str | None = None,
    status_min: int | None = None,
    status_max: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PaginatedRequestEvents:
    conditions = []
    if date_from is not None:
        conditions.append(RequestEvent.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(RequestEvent.occurred_at <= date_to)
    if user_id is not None:
        conditions.append(RequestEvent.user_id == user_id)
    if module is not None:
        conditions.append(RequestEvent.module == module)
    if client is not None:
        conditions.append(RequestEvent.client == client)
    if status_min is not None:
        conditions.append(RequestEvent.status_code >= status_min)
    if status_max is not None:
        conditions.append(RequestEvent.status_code <= status_max)

    count_stmt = select(func.count(RequestEvent.id)).where(*conditions)
    total = (await session.exec(count_stmt)).one()

    stmt = (
        select(RequestEvent)
        .where(*conditions)
        .order_by(RequestEvent.occurred_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = (await session.exec(stmt)).all()

    return PaginatedRequestEvents(
        items=[RequestEventRead.model_validate(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )


async def list_errors(
    session: AsyncSession,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PaginatedErrorEvents:
    conditions = []
    if date_from is not None:
        conditions.append(ErrorEvent.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(ErrorEvent.occurred_at <= date_to)

    count_stmt = select(func.count(ErrorEvent.id)).where(*conditions)
    total = (await session.exec(count_stmt)).one()

    stmt = (
        select(ErrorEvent)
        .where(*conditions)
        .order_by(ErrorEvent.occurred_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = (await session.exec(stmt)).all()

    return PaginatedErrorEvents(
        items=[ErrorEventRead.model_validate(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_error(session: AsyncSession, error_id: UUID) -> ErrorEventRead | None:
    stmt = select(ErrorEvent).where(ErrorEvent.id == error_id)
    error = (await session.exec(stmt)).first()
    if error is None:
        return None
    return ErrorEventRead.model_validate(error)


async def purge_expired_activity_events(
    session: AsyncSession,
    retention_days: int = 180,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)

    stmt_req = delete(RequestEvent).where(RequestEvent.occurred_at < cutoff)
    res_req = await session.exec(stmt_req)
    deleted_requests = res_req.rowcount

    stmt_err = delete(ErrorEvent).where(ErrorEvent.occurred_at < cutoff)
    res_err = await session.exec(stmt_err)
    deleted_errors = res_err.rowcount

    return deleted_requests, deleted_errors
