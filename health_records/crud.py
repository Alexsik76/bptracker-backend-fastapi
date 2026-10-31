from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from health_records.models import HealthRecord, HealthRecordUpsert


async def batch_sync(
    session: AsyncSession,
    user_id: UUID,
    *,
    upserts: list[HealthRecordUpsert],
    deleted_ids: list[str],
) -> tuple[int, int, int]:
    """Upsert and delete health records in a single transaction.

    Returns (created_count, updated_count, deleted_count).
    """
    created_count = 0
    updated_count = 0

    if upserts:
        # If the same hc_record_id appears multiple times within the batch,
        # keep the last record to avoid PostgreSQL CardinalityViolation.
        deduped: dict[str, HealthRecordUpsert] = {}
        for item in upserts:
            deduped[item.hc_record_id] = item
        records = list(deduped.values())

        stmt = insert(HealthRecord).values(
            [
                {
                    "user_id": user_id,
                    "record_type": item.record_type,
                    "values": item.values,
                    "start_time": item.start_time,
                    "end_time": item.end_time,
                    "data_origin": item.data_origin,
                    "hc_record_id": item.hc_record_id,
                }
                for item in records
            ]
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_health_records_user_hc_record",
            set_={
                "record_type": stmt.excluded["record_type"],
                "values": stmt.excluded["values"],
                "start_time": stmt.excluded["start_time"],
                "end_time": stmt.excluded["end_time"],
                "data_origin": stmt.excluded["data_origin"],
                "updated_at": func.now(),
            },
        ).returning(literal_column("(xmax = 0)").label("is_created"))
        result = await session.exec(stmt)
        rows = result.all()
        created_count = sum(1 for row in rows if row[0])
        updated_count = len(rows) - created_count

    deleted_count = 0
    if deleted_ids:
        del_stmt = (
            delete(HealthRecord)
            .where(
                HealthRecord.user_id == user_id,
                col(HealthRecord.hc_record_id).in_(deleted_ids),
            )
            .returning(HealthRecord.id)
        )
        del_res = await session.exec(del_stmt)
        deleted_count = len(del_res.all())

    await session.commit()
    return created_count, updated_count, deleted_count


async def get_health_records(
    session: AsyncSession,
    user_id: UUID,
    *,
    record_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[Sequence[HealthRecord], int]:
    """Return a page of a user's health records, newest start_time first, plus total count."""
    filters = [HealthRecord.user_id == user_id]
    if record_type is not None:
        filters.append(HealthRecord.record_type == record_type)
    if date_from is not None:
        filters.append(col(HealthRecord.start_time) >= date_from)
    if date_to is not None:
        filters.append(col(HealthRecord.start_time) < date_to)

    count_statement = select(func.count()).select_from(HealthRecord).where(*filters)
    total = (await session.exec(count_statement)).one()

    statement = (
        select(HealthRecord)
        .where(*filters)
        .order_by(col(HealthRecord.start_time).desc(), col(HealthRecord.id).desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.exec(statement)
    return result.all(), total
