from __future__ import annotations

import logging

from database.connection import get_db

logger = logging.getLogger(__name__)


async def init_db() -> None:
    pool = await get_db()
    async with pool.acquire() as conn:
        # --- Tables ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id             BIGINT PRIMARY KEY,
                title               TEXT NOT NULL DEFAULT '',
                owner_id            BIGINT NOT NULL DEFAULT 0,
                welcome_enabled     INTEGER NOT NULL DEFAULT 1,
                welcome_message     TEXT NOT NULL DEFAULT 'Welcome to {chat_name}, {user_mention}!',
                goodbye_enabled     INTEGER NOT NULL DEFAULT 1,
                goodbye_message     TEXT NOT NULL DEFAULT 'Goodbye, {user_name}!',
                antispam_enabled    INTEGER NOT NULL DEFAULT 1,
                flood_limit         INTEGER NOT NULL DEFAULT 5,
                flood_window        INTEGER NOT NULL DEFAULT 10,
                flood_action        TEXT NOT NULL DEFAULT 'mute',
                flood_mute_duration INTEGER NOT NULL DEFAULT 300,
                link_filter_enabled INTEGER NOT NULL DEFAULT 0,
                link_allowlist      TEXT NOT NULL DEFAULT '[]',
                censor_enabled      INTEGER NOT NULL DEFAULT 1,
                warn_threshold      INTEGER NOT NULL DEFAULT 3,
                warn_action         TEXT NOT NULL DEFAULT 'ban',
                warn_mute_duration  INTEGER NOT NULL DEFAULT 3600,
                rules_text          TEXT NOT NULL DEFAULT '',
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                updated_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id             BIGINT PRIMARY KEY,
                username            TEXT NOT NULL DEFAULT '',
                first_name          TEXT NOT NULL DEFAULT '',
                last_name           TEXT NOT NULL DEFAULT '',
                is_bot              INTEGER NOT NULL DEFAULT 0,
                first_seen          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                last_seen           BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_id             BIGINT NOT NULL,
                user_id             BIGINT NOT NULL,
                role                TEXT NOT NULL DEFAULT 'member',
                is_muted            INTEGER NOT NULL DEFAULT 0,
                muted_until         BIGINT NOT NULL DEFAULT 0,
                joined_at           BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                PRIMARY KEY (chat_id, user_id),
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                user_id             BIGINT NOT NULL,
                reason              TEXT NOT NULL DEFAULT '',
                issued_by           BIGINT NOT NULL,
                issued_at           BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                expires_at          BIGINT NOT NULL DEFAULT 0,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (issued_by) REFERENCES users(user_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS banned_words (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                word                TEXT NOT NULL,
                is_regex            INTEGER NOT NULL DEFAULT 0,
                action              TEXT NOT NULL DEFAULT 'warn',
                added_by            BIGINT NOT NULL,
                added_at            BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                UNIQUE(chat_id, word),
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                message_type        TEXT NOT NULL DEFAULT 'text',
                text                TEXT NOT NULL DEFAULT '',
                poll_question       TEXT NOT NULL DEFAULT '',
                poll_options        TEXT NOT NULL DEFAULT '[]',
                poll_anonymous      INTEGER NOT NULL DEFAULT 1,
                poll_type           TEXT NOT NULL DEFAULT 'regular',
                poll_correct_option INTEGER NOT NULL DEFAULT -1,
                cron_expression     TEXT NOT NULL DEFAULT '',
                interval_seconds    INTEGER NOT NULL DEFAULT 0,
                once_at             BIGINT NOT NULL DEFAULT 0,
                next_run            BIGINT NOT NULL DEFAULT 0,
                created_by          BIGINT NOT NULL,
                is_active           INTEGER NOT NULL DEFAULT 1,
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                name                TEXT NOT NULL,
                content             TEXT NOT NULL,
                created_by          BIGINT NOT NULL,
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                UNIQUE(chat_id, name),
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                reported_user_id    BIGINT NOT NULL,
                reporter_user_id    BIGINT NOT NULL,
                message_text        TEXT NOT NULL DEFAULT '',
                status              TEXT NOT NULL DEFAULT 'pending',
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS triggers (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                keyword             TEXT NOT NULL,
                response            TEXT NOT NULL,
                is_regex            INTEGER NOT NULL DEFAULT 0,
                delete_trigger      INTEGER NOT NULL DEFAULT 0,
                created_by          BIGINT NOT NULL,
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                UNIQUE(chat_id, keyword),
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_notes (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                user_id             BIGINT NOT NULL,
                note                TEXT NOT NULL,
                created_by          BIGINT NOT NULL,
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS captcha_sessions (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                user_id             BIGINT NOT NULL,
                answer              TEXT NOT NULL,
                message_id          BIGINT NOT NULL DEFAULT 0,
                expires_at          BIGINT NOT NULL,
                solved              INTEGER NOT NULL DEFAULT 0,
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS action_log (
                id                  SERIAL PRIMARY KEY,
                chat_id             BIGINT NOT NULL,
                action_type         TEXT NOT NULL,
                actor_id            BIGINT NOT NULL,
                target_id           BIGINT NOT NULL DEFAULT 0,
                details             TEXT NOT NULL DEFAULT '',
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS federations (
                id                  SERIAL PRIMARY KEY,
                name                TEXT NOT NULL UNIQUE,
                owner_id            BIGINT NOT NULL,
                description         TEXT NOT NULL DEFAULT '',
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS federation_members (
                federation_id       INTEGER NOT NULL,
                chat_id             BIGINT NOT NULL,
                joined_at           BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                PRIMARY KEY (federation_id, chat_id),
                FOREIGN KEY (federation_id) REFERENCES federations(id),
                FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS federation_bans (
                id                  SERIAL PRIMARY KEY,
                federation_id       INTEGER NOT NULL,
                user_id             BIGINT NOT NULL,
                reason              TEXT NOT NULL DEFAULT '',
                banned_by           BIGINT NOT NULL,
                created_at          BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT,
                UNIQUE(federation_id, user_id),
                FOREIGN KEY (federation_id) REFERENCES federations(id)
            )
        """)

        # --- Indexes ---
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_warnings_chat_user ON warnings(chat_id, user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_members_chat ON chat_members(chat_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_next_run ON scheduled_messages(next_run) WHERE is_active = 1")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_banned_words_chat ON banned_words(chat_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_chat ON notes(chat_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_triggers_chat ON triggers(chat_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_notes_chat_user ON user_notes(chat_id, user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_captcha_chat_user ON captcha_sessions(chat_id, user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_action_log_chat ON action_log(chat_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_federation_bans_fed ON federation_bans(federation_id)")

        # --- Safe ALTER TABLE migrations (ADD COLUMN IF NOT EXISTS) ---
        migrations = [
            ("chat_members", "afk_reason", "TEXT NOT NULL DEFAULT ''"),
            ("chat_members", "afk_since", "BIGINT NOT NULL DEFAULT 0"),
            ("chats", "locked_types", "TEXT NOT NULL DEFAULT '[]'"),
            ("chats", "auto_scan_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "auto_scan_interval", "INTEGER NOT NULL DEFAULT 86400"),
            ("chats", "last_scan", "BIGINT NOT NULL DEFAULT 0"),
            ("chats", "captcha_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "captcha_type", "TEXT NOT NULL DEFAULT 'button'"),
            ("chats", "captcha_timeout", "INTEGER NOT NULL DEFAULT 120"),
            ("chats", "captcha_action", "TEXT NOT NULL DEFAULT 'kick'"),
            ("chats", "script_filter_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "script_filter_action", "TEXT NOT NULL DEFAULT 'mute'"),
            ("chats", "anti_raid_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "raid_threshold", "INTEGER NOT NULL DEFAULT 10"),
            ("chats", "raid_window", "INTEGER NOT NULL DEFAULT 30"),
            ("chats", "raid_action", "TEXT NOT NULL DEFAULT 'lock'"),
            ("chats", "global_lock", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "warn_expire_hours", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "welcome_media", "TEXT NOT NULL DEFAULT ''"),
            ("chats", "welcome_media_type", "TEXT NOT NULL DEFAULT ''"),
            ("chats", "welcome_delete_seconds", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "goodbye_delete_seconds", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "purge_join", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "purge_leave", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "purge_pin", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "purge_photo_change", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "night_mode_enabled", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "night_start", "TEXT NOT NULL DEFAULT '23:00'"),
            ("chats", "night_end", "TEXT NOT NULL DEFAULT '06:00'"),
            ("chats", "night_action", "TEXT NOT NULL DEFAULT 'mute'"),
            ("chats", "log_channel_id", "BIGINT NOT NULL DEFAULT 0"),
            ("chats", "slow_mode_seconds", "INTEGER NOT NULL DEFAULT 0"),
            ("chats", "triggers_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("chats", "federation_id", "INTEGER NOT NULL DEFAULT 0"),
            ("chat_members", "warn_expire_at", "BIGINT NOT NULL DEFAULT 0"),
            ("banned_words", "is_regex", "INTEGER NOT NULL DEFAULT 0"),
            ("captcha_sessions", "message_id", "BIGINT NOT NULL DEFAULT 0"),
            ("warnings", "expires_at", "BIGINT NOT NULL DEFAULT 0"),
        ]
        for table, column, col_def in migrations:
            try:
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_def}"
                )
            except Exception:
                pass
