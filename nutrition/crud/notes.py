from datetime import date
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.models import MenuNote, MenuNoteKind


async def create_menu_note(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    kind: MenuNoteKind,
    text: str,
    answer: str | None = None,
) -> MenuNote:
    """Create a new menu note (info or correction)."""
    note = MenuNote(
        user_id=user_id,
        date=menu_date,
        kind=kind,
        text=text,
        answer=answer,
    )
    session.add(note)
    await session.flush()
    return note


async def get_menu_notes(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    kind: MenuNoteKind | None = None,
) -> list[MenuNote]:
    """Retrieve all menu notes for a user on a given date, optionally filtered by kind."""
    stmt = (
        select(MenuNote)
        .where(MenuNote.user_id == user_id, MenuNote.date == menu_date)
        .order_by(col(MenuNote.created_at).asc())
    )
    if kind is not None:
        stmt = stmt.where(MenuNote.kind == kind)
    result = await session.exec(stmt)
    return list(result.all())
