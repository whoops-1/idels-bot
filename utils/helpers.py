from __future__ import annotations

import re
import time
import json
from datetime import datetime

import aiosqlite

from config import OWNER_IDS
from utils.constants import Role


async def upsert_user(db: aiosqlite.Connection, user) -> None:
    """Insert or update a user from a telegram.User object."""
    now = int(time.time())
    await db.execute(
        """INSERT INTO users (user_id, username, first_name, last_name, is_bot, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               username = excluded.username,
               first_name = excluded.first_name,
               last_name = excluded.last_name,
               is_bot = excluded.is_bot,
               last_seen = excluded.last_seen""",
        (user.id, user.username or "", user.first_name or "", user.last_name or "", int(user.is_bot), now, now),
    )
    await db.commit()


async def get_or_create_chat(db: aiosqlite.Connection, chat_id: int, title: str = "") -> None:
    """Ensure a row exists in the chats table, updating title if provided."""
    await db.execute(
        "INSERT INTO chats (chat_id, title) VALUES (?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title WHERE excluded.title != ''",
        (chat_id, title),
    )
    await db.commit()


async def ensure_chat_member(db: aiosqlite.Connection, chat_id: int, user_id: int, role: str = "member") -> None:
    """Ensure a row exists in chat_members."""
    await db.execute(
        "INSERT OR IGNORE INTO chat_members (chat_id, user_id, role) VALUES (?, ?, ?)",
        (chat_id, user_id, role),
    )
    await db.commit()


async def get_user_role(chat_id: int, user_id: int, bot=None) -> Role:
    """Get the role of a user in a chat. Falls back to Telegram API."""
    if user_id in OWNER_IDS:
        return Role.OWNER

    from database.connection import get_db
    db = await get_db()
    row = await db.execute_fetchall(
        "SELECT role FROM chat_members WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    )
    if row:
        role_str = row[0][0]
        try:
            return Role(role_str)
        except ValueError:
            pass

    if bot:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status == "creator":
                return Role.OWNER
            if member.status == "administrator":
                return Role.ADMIN
        except Exception:
            pass

    return Role.MEMBER


async def has_permission(chat_id: int, user_id: int, required_role: Role, bot=None) -> bool:
    """Check if user meets or exceeds the required role."""
    user_role = await get_user_role(chat_id, user_id, bot)
    return Role.hierarchy()[user_role.value] >= Role.hierarchy()[required_role.value]


def format_duration(seconds: int) -> str:
    """Convert seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m {s}s" if s else f"{m}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d {h}h" if h else f"{d}d"


def parse_duration(text: str) -> int | None:
    """Parse '2h30m', '15m', '1d', '30s' into seconds. Returns None if invalid."""
    text = text.strip().lower()
    if not text:
        return None

    match = re.fullmatch(r"(\d+)\s*([smhd])(?:(\d+)\s*([smhd]))?", text)
    if not match:
        return None

    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    total = int(match.group(1)) * multipliers[match.group(2)]
    if match.group(3) and match.group(4):
        total += int(match.group(3)) * multipliers[match.group(4)]
    return total


def parse_time_spec(text: str) -> dict | None:
    """Parse time specifications for scheduled messages.
    Returns dict with one of: interval_seconds, cron_expression, once_at.
    """
    text = text.strip().lower()

    # "every 30m", "every 2h", "every 1d"
    m = re.fullmatch(r"every\s+(\d+)([smhd])", text)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        return {"interval_seconds": int(m.group(1)) * mult[m.group(2)]}

    # "daily 09:00"
    m = re.fullmatch(r"daily\s+(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return {"cron_expression": f"{mi} {h} * * *"}

    # "weekly mon 14:00"
    days = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}
    m = re.fullmatch(r"weekly\s+(\w{3})\s+(\d{1,2}):(\d{2})", text)
    if m and m.group(1) in days:
        h, mi = int(m.group(2)), int(m.group(3))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return {"cron_expression": f"{mi} {h} * * {days[m.group(1)]}"}

    # "YYYY-MM-DD HH:MM"
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})", text)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M")
            return {"once_at": int(dt.timestamp())}
        except ValueError:
            pass

    return None


def escape_markdown(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    special = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(special)}])", r"\\\1", text)


def build_user_mention(user) -> str:
    """Returns a MarkdownV2-formatted mention link."""
    name = escape_markdown(user.first_name or "User")
    return f"[{name}](tg://user?id={user.id})"


def parse_json_list(text: str) -> list:
    """Safely parse a JSON array string."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
