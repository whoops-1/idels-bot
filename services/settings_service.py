from __future__ import annotations

import json
import time

from database.connection import get_db
from models.settings import ChatSettings


async def get_chat_settings(chat_id: int) -> ChatSettings:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))
    if not rows:
        await db.execute("INSERT INTO chats (chat_id) VALUES (?)", (chat_id,))
        await db.commit()
        rows = await db.execute_fetchall("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))

    r = dict(rows[0])
    g = r.get  # shorthand for get with defaults
    return ChatSettings(
        chat_id=g("chat_id", 0),
        owner_id=g("owner_id", 0),
        welcome_enabled=bool(g("welcome_enabled", 1)),
        welcome_message=g("welcome_message", ""),
        goodbye_enabled=bool(g("goodbye_enabled", 1)),
        goodbye_message=g("goodbye_message", ""),
        welcome_media=g("welcome_media", ""),
        welcome_media_type=g("welcome_media_type", ""),
        welcome_delete_seconds=g("welcome_delete_seconds", 0),
        goodbye_delete_seconds=g("goodbye_delete_seconds", 0),
        antispam_enabled=bool(g("antispam_enabled", 1)),
        flood_limit=g("flood_limit", 5),
        flood_window=g("flood_window", 10),
        flood_action=g("flood_action", "mute"),
        flood_mute_duration=g("flood_mute_duration", 300),
        link_filter_enabled=bool(g("link_filter_enabled", 0)),
        link_allowlist=json.loads(g("link_allowlist", "[]")),
        censor_enabled=bool(g("censor_enabled", 1)),
        warn_threshold=g("warn_threshold", 3),
        warn_action=g("warn_action", "ban"),
        warn_mute_duration=g("warn_mute_duration", 3600),
        warn_expire_hours=g("warn_expire_hours", 0),
        rules_text=g("rules_text", ""),
        locked_types=json.loads(g("locked_types", "[]")),
        global_lock=bool(g("global_lock", 0)),
        auto_scan_enabled=bool(g("auto_scan_enabled", 0)),
        auto_scan_interval=g("auto_scan_interval", 86400),
        last_scan=g("last_scan", 0),
        captcha_enabled=bool(g("captcha_enabled", 0)),
        captcha_type=g("captcha_type", "button"),
        captcha_timeout=g("captcha_timeout", 120),
        captcha_action=g("captcha_action", "kick"),
        script_filter_enabled=bool(g("script_filter_enabled", 0)),
        script_filter_action=g("script_filter_action", "mute"),
        anti_raid_enabled=bool(g("anti_raid_enabled", 0)),
        raid_threshold=g("raid_threshold", 10),
        raid_window=g("raid_window", 30),
        raid_action=g("raid_action", "lock"),
        purge_join=bool(g("purge_join", 0)),
        purge_leave=bool(g("purge_leave", 0)),
        purge_pin=bool(g("purge_pin", 0)),
        purge_photo_change=bool(g("purge_photo_change", 0)),
        night_mode_enabled=bool(g("night_mode_enabled", 0)),
        night_start=g("night_start", "23:00"),
        night_end=g("night_end", "06:00"),
        night_action=g("night_action", "mute"),
        log_channel_id=g("log_channel_id", 0),
        slow_mode_seconds=g("slow_mode_seconds", 0),
        triggers_enabled=bool(g("triggers_enabled", 1)),
        federation_id=g("federation_id", 0),
    )


_ALLOWED_COLUMNS = {
    "owner_id", "title", "welcome_enabled", "welcome_message", "goodbye_enabled",
    "goodbye_message", "antispam_enabled", "flood_limit", "flood_window",
    "flood_action", "flood_mute_duration", "link_filter_enabled", "link_allowlist",
    "censor_enabled", "warn_threshold", "warn_action", "warn_mute_duration", "rules_text",
    "locked_types", "auto_scan_enabled", "auto_scan_interval", "last_scan",
    "captcha_enabled", "captcha_type", "captcha_timeout", "captcha_action",
    "script_filter_enabled", "script_filter_action",
    "anti_raid_enabled", "raid_threshold", "raid_window", "raid_action",
    "global_lock", "warn_expire_hours",
    "welcome_media", "welcome_media_type", "welcome_delete_seconds", "goodbye_delete_seconds",
    "purge_join", "purge_leave", "purge_pin", "purge_photo_change",
    "night_mode_enabled", "night_start", "night_end", "night_action",
    "log_channel_id", "slow_mode_seconds", "triggers_enabled", "federation_id",
}


async def update_chat_setting(chat_id: int, key: str, value) -> None:
    if key not in _ALLOWED_COLUMNS:
        raise ValueError(f"Invalid column name: {key}")
    db = await get_db()
    await db.execute(
        f"UPDATE chats SET {key} = ?, updated_at = ? WHERE chat_id = ?",
        (value, int(time.time()), chat_id),
    )
    await db.commit()


async def update_link_allowlist(chat_id: int, allowlist: list[str]) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE chats SET link_allowlist = ?, updated_at = ? WHERE chat_id = ?",
        (json.dumps(allowlist), int(time.time()), chat_id),
    )
    await db.commit()


async def get_chat_owner_id(chat_id: int) -> int:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT owner_id FROM chats WHERE chat_id = ?", (chat_id,))
    return rows[0][0] if rows else 0


# ──────────────────────────────────────────────
# ACTION LOG
# ──────────────────────────────────────────────

async def log_action(chat_id: int, action_type: str, actor_id: int, target_id: int = 0, details: str = "") -> None:
    """Log an admin action to the action_log table and optionally to the log channel."""
    db = await get_db()
    await db.execute(
        "INSERT INTO action_log (chat_id, action_type, actor_id, target_id, details) VALUES (?, ?, ?, ?, ?)",
        (chat_id, action_type, actor_id, target_id, details),
    )
    await db.commit()


# ──────────────────────────────────────────────
# TRIGGERS (Custom Commands)
# ──────────────────────────────────────────────

async def get_trigger(chat_id: int, keyword: str) -> dict | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM triggers WHERE chat_id = ? AND keyword = ? COLLATE NOCASE",
        (chat_id, keyword),
    )
    return dict(rows[0]) if rows else None


async def get_all_triggers(chat_id: int) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM triggers WHERE chat_id = ? ORDER BY keyword", (chat_id,)
    )
    return [dict(r) for r in rows]


async def add_trigger(chat_id: int, keyword: str, response: str, created_by: int, is_regex: int = 0, delete_trigger: int = 0) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO triggers (chat_id, keyword, response, is_regex, delete_trigger, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id, keyword) DO UPDATE SET response = excluded.response",
        (chat_id, keyword.lower(), response, is_regex, delete_trigger, created_by),
    )
    await db.commit()


async def remove_trigger(chat_id: int, keyword: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM triggers WHERE chat_id = ? AND keyword = ? COLLATE NOCASE",
        (chat_id, keyword),
    )
    await db.commit()
    return cursor.rowcount > 0


# ──────────────────────────────────────────────
# USER NOTES
# ──────────────────────────────────────────────

async def add_user_note(chat_id: int, user_id: int, note: str, created_by: int) -> int:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO user_notes (chat_id, user_id, note, created_by) VALUES (?, ?, ?, ?)",
        (chat_id, user_id, note, created_by),
    )
    await db.commit()
    return cursor.lastrowid


async def get_user_notes(chat_id: int, user_id: int) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM user_notes WHERE chat_id = ? AND user_id = ? ORDER BY created_at DESC",
        (chat_id, user_id),
    )
    return [dict(r) for r in rows]


async def delete_user_note(note_id: int, chat_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM user_notes WHERE id = ? AND chat_id = ?", (note_id, chat_id)
    )
    await db.commit()
    return cursor.rowcount > 0


# ──────────────────────────────────────────────
# FEDERATIONS
# ──────────────────────────────────────────────

async def create_federation(name: str, owner_id: int, description: str = "") -> int:
    db = await get_db()
    cursor = await db.execute(
        "INSERT INTO federations (name, owner_id, description) VALUES (?, ?, ?)",
        (name, owner_id, description),
    )
    await db.commit()
    return cursor.lastrowid


async def join_federation(federation_id: int, chat_id: int) -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO federation_members (federation_id, chat_id) VALUES (?, ?)",
        (federation_id, chat_id),
    )
    await db.execute(
        "UPDATE chats SET federation_id = ? WHERE chat_id = ?",
        (federation_id, chat_id),
    )
    await db.commit()


async def leave_federation(chat_id: int) -> None:
    db = await get_db()
    settings = await get_chat_settings(chat_id)
    if settings.federation_id:
        await db.execute(
            "DELETE FROM federation_members WHERE federation_id = ? AND chat_id = ?",
            (settings.federation_id, chat_id),
        )
    await db.execute("UPDATE chats SET federation_id = 0 WHERE chat_id = ?", (chat_id,))
    await db.commit()


async def get_federation_by_name(name: str) -> dict | None:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM federations WHERE name = ? COLLATE NOCASE", (name,)
    )
    return dict(rows[0]) if rows else None


async def get_federation_by_id(fed_id: int) -> dict | None:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM federations WHERE id = ?", (fed_id,))
    return dict(rows[0]) if rows else None


async def fed_ban_user(federation_id: int, user_id: int, reason: str, banned_by: int) -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO federation_bans (federation_id, user_id, reason, banned_by) VALUES (?, ?, ?, ?)",
        (federation_id, user_id, reason, banned_by),
    )
    await db.commit()


async def fed_unban_user(federation_id: int, user_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM federation_bans WHERE federation_id = ? AND user_id = ?",
        (federation_id, user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def is_fed_banned(federation_id: int, user_id: int) -> dict | None:
    if not federation_id:
        return None
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM federation_bans WHERE federation_id = ? AND user_id = ?",
        (federation_id, user_id),
    )
    return dict(rows[0]) if rows else None


async def get_fed_member_chats(federation_id: int) -> list[int]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT chat_id FROM federation_members WHERE federation_id = ?",
        (federation_id,),
    )
    return [r[0] for r in rows]


# ──────────────────────────────────────────────
# CAPTCHA SESSIONS
# ──────────────────────────────────────────────

async def create_captcha_session(chat_id: int, user_id: int, answer: str, timeout: int, message_id: int = 0) -> None:
    db = await get_db()
    now = int(time.time())
    # Clean old sessions
    await db.execute(
        "DELETE FROM captcha_sessions WHERE chat_id = ? AND user_id = ? AND solved = 0",
        (chat_id, user_id),
    )
    await db.execute(
        "INSERT INTO captcha_sessions (chat_id, user_id, answer, message_id, expires_at) VALUES (?, ?, ?, ?, ?)",
        (chat_id, user_id, answer, message_id, now + timeout),
    )
    await db.commit()


async def verify_captcha(chat_id: int, user_id: int, answer: str) -> bool:
    db = await get_db()
    now = int(time.time())
    rows = await db.execute_fetchall(
        "SELECT * FROM captcha_sessions WHERE chat_id = ? AND user_id = ? AND solved = 0 AND expires_at > ?",
        (chat_id, user_id, now),
    )
    if not rows:
        return False
    session = dict(rows[0])
    if session["answer"].lower() == answer.lower().strip():
        await db.execute("UPDATE captcha_sessions SET solved = 1 WHERE id = ?", (session["id"],))
        await db.commit()
        return True
    return False


async def get_expired_captcha_sessions() -> list[dict]:
    db = await get_db()
    now = int(time.time())
    rows = await db.execute_fetchall(
        "SELECT * FROM captcha_sessions WHERE solved = 0 AND expires_at <= ?",
        (now,),
    )
    return [dict(r) for r in rows]


async def clear_captcha_session(chat_id: int, user_id: int) -> None:
    db = await get_db()
    await db.execute(
        "DELETE FROM captcha_sessions WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    )
    await db.commit()
