from __future__ import annotations

import time

from database.connection import get_db
from models.warning import WarningRecord


async def add_warning(chat_id: int, user_id: int, issued_by: int, reason: str = "") -> int:
    """Add a warning and return the new total count."""
    db = await get_db()
    await db.execute(
        "INSERT INTO warnings (chat_id, user_id, reason, issued_by) VALUES (?, ?, ?, ?)",
        (chat_id, user_id, reason, issued_by),
    )
    await db.commit()
    return await get_warning_count(chat_id, user_id)


async def get_warning_count(chat_id: int, user_id: int) -> int:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM warnings WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    )
    return rows[0][0]


async def get_warnings(chat_id: int, user_id: int) -> list[WarningRecord]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM warnings WHERE chat_id = ? AND user_id = ? ORDER BY issued_at DESC",
        (chat_id, user_id),
    )
    return [
        WarningRecord(
            id=r["id"],
            chat_id=r["chat_id"],
            user_id=r["user_id"],
            reason=r["reason"],
            issued_by=r["issued_by"],
            issued_at=r["issued_at"],
        )
        for r in rows
    ]


async def remove_latest_warning(chat_id: int, user_id: int) -> WarningRecord | None:
    """Remove and return the most recent warning, or None if no warnings."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM warnings WHERE chat_id = ? AND user_id = ? ORDER BY issued_at DESC LIMIT 1",
        (chat_id, user_id),
    )
    if not rows:
        return None
    r = dict(rows[0])
    await db.execute("DELETE FROM warnings WHERE id = ?", (r["id"],))
    await db.commit()
    return WarningRecord(**r)


async def clear_warnings(chat_id: int, user_id: int) -> None:
    """Remove all warnings for a user in a chat."""
    db = await get_db()
    await db.execute(
        "DELETE FROM warnings WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    )
    await db.commit()
