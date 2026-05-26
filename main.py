from __future__ import annotations

import asyncio
import json
import logging
import time

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from tornado.web import RequestHandler, Application

from config import BOT_TOKEN, WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_LISTEN, WEBHOOK_CERT, WEBHOOK_KEY, LOG_LEVEL
from database.schema import init_db
from database.connection import close_db, get_db
from handlers import moderation, welcome, antispam, censor, scheduled, admin, info, errors, pm_panel, security, triggers
from handlers.antispam import check_message
from handlers.admin import handle_settings_input, check_locked_content
from handlers.info import check_afk
from handlers.security import (
    captcha_button_callback, check_expired_captcha, check_night_mode,
    check_script_filter, global_lock_callback,
)
from handlers.triggers import check_triggers
from handlers.welcome import purge_service_messages
from handlers.moderation import userinfo_callback, handle_note_input
from services.schedule_service import load_scheduled_jobs
from services.settings_service import log_action
from utils.constants import (
    CB_PREFIX_SETTINGS,
    CB_PREFIX_WARN_ACTION,
    CB_PREFIX_BOT_JOIN,
    CB_PREFIX_USER_JOIN,
    CB_PREFIX_HELP,
    CB_PREFIX_REPORT,
    CB_PREFIX_SCAN,
    CB_PREFIX_PM_GROUP,
    CB_PREFIX_PM_SETTINGS,
    CB_PREFIX_CAPTCHA,
    CB_PREFIX_FED,
    CB_PREFIX_PURGE,
    CB_PREFIX_GLOBAL_LOCK,
    CB_PREFIX_RAID,
    CB_PREFIX_USERINFO,
    CB_PREFIX_NIGHT,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL),
)
logger = logging.getLogger(__name__)

_DB_RETRY_ATTEMPTS = 5
_DB_RETRY_DELAY = 3  # seconds between retries
_start_time = time.time()


class HealthHandler(RequestHandler):
    async def get(self):
        db_status = "ok"
        try:
            pool = await get_db()
            await pool.fetchval("SELECT 1")
        except Exception as e:
            db_status = f"error: {e}"

        status = "ok" if db_status == "ok" else "degraded"
        self.set_status(200 if status == "ok" else 503)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({
            "status": status,
            "uptime_seconds": round(time.time() - _start_time),
            "database": db_status,
        }))


async def post_init(application) -> None:
    last_error: Exception | None = None
    for attempt in range(1, _DB_RETRY_ATTEMPTS + 1):
        try:
            await init_db()
            logger.info("Database initialized successfully.")
            break
        except Exception as e:
            last_error = e
            logger.warning(
                f"DB connection attempt {attempt}/{_DB_RETRY_ATTEMPTS} failed: {e}"
                + (f" — retrying in {_DB_RETRY_DELAY}s" if attempt < _DB_RETRY_ATTEMPTS else " — giving up")
            )
            if attempt < _DB_RETRY_ATTEMPTS:
                await asyncio.sleep(_DB_RETRY_DELAY)
    else:
        raise RuntimeError(
            f"Could not connect to the database after {_DB_RETRY_ATTEMPTS} attempts. "
            f"Last error: {last_error}"
        )

    try:
        await load_scheduled_jobs(application)
        logger.info("Scheduled jobs loaded.")
    except Exception as e:
        logger.error(f"Failed to load scheduled jobs: {e}")

    if application.job_queue:
        application.job_queue.run_repeating(check_expired_captcha, interval=30, first=30, name="captcha_check")
        application.job_queue.run_repeating(moderation.check_expired_warnings, interval=3600, first=60, name="warn_expiry")
        application.job_queue.run_repeating(check_night_mode, interval=60, first=60, name="night_mode")
        logger.info("Periodic jobs registered.")


async def post_shutdown(application) -> None:
    await close_db()
    logger.info("Database connection closed.")


def build_application():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ── Command handlers ──
    app.add_handler(CommandHandler("start", info.start_command))
    app.add_handler(CommandHandler("help", info.help_command))
    app.add_handler(CommandHandler("id", info.id_command))
    app.add_handler(CommandHandler("rules", info.rules_command))
    app.add_handler(CommandHandler("afk", info.afk_command))
    app.add_handler(CommandHandler("stats", info.stats_command))
    app.add_handler(CommandHandler("report", moderation.report_command))
    app.add_handler(CommandHandler("info", moderation.userinfo_command))

    # Moderation
    app.add_handler(CommandHandler("kick", moderation.kick_command))
    app.add_handler(CommandHandler("ban", moderation.ban_command))
    app.add_handler(CommandHandler("unban", moderation.unban_command))
    app.add_handler(CommandHandler("mute", moderation.mute_command))
    app.add_handler(CommandHandler("unmute", moderation.unmute_command))
    app.add_handler(CommandHandler("warn", moderation.warn_command))
    app.add_handler(CommandHandler("unwarn", moderation.unwarn_command))
    app.add_handler(CommandHandler("warnings", moderation.warnings_command))
    app.add_handler(CommandHandler("scan", moderation.scan_command))
    app.add_handler(CommandHandler("pin", moderation.pin_command))
    app.add_handler(CommandHandler("unpin", moderation.unpin_command))
    app.add_handler(CommandHandler("tagall", moderation.tagall_command))
    app.add_handler(CommandHandler("purge", moderation.purge_command))
    app.add_handler(CommandHandler("blacklist", moderation.blacklist_command))

    # User notes
    app.add_handler(CommandHandler("unote", moderation.unote_command))
    app.add_handler(CommandHandler("unotes", moderation.unotes_command))
    app.add_handler(CommandHandler("delunote", moderation.delunote_command))

    # Censoring
    app.add_handler(CommandHandler("addword", censor.add_banned_word))
    app.add_handler(CommandHandler("removeword", censor.remove_banned_word))
    app.add_handler(CommandHandler("listwords", censor.list_banned_words))

    # Link filter
    app.add_handler(CommandHandler("linkadd", antispam.add_allowed_link))
    app.add_handler(CommandHandler("linkremove", antispam.remove_allowed_link))
    app.add_handler(CommandHandler("linklist", antispam.list_allowed_links))

    # Scheduled messages
    app.add_handler(CommandHandler("scheduletext", scheduled.schedule_text))
    app.add_handler(CommandHandler("schedulepoll", scheduled.schedule_poll))
    app.add_handler(CommandHandler("listjobs", scheduled.list_scheduled))
    app.add_handler(CommandHandler("canceljob", scheduled.cancel_scheduled))

    # Settings & admin
    app.add_handler(CommandHandler("settings", admin.settings_command))
    app.add_handler(CommandHandler("setowner", admin.set_owner))
    app.add_handler(CommandHandler("promote", admin.promote_user))
    app.add_handler(CommandHandler("demote", admin.demote_user))
    app.add_handler(CommandHandler("setwelcome", admin.set_welcome))
    app.add_handler(CommandHandler("setgoodbye", admin.set_goodbye))
    app.add_handler(CommandHandler("setrules", admin.set_rules))
    app.add_handler(CommandHandler("lock", admin.lock_command))
    app.add_handler(CommandHandler("unlock", admin.unlock_command))
    app.add_handler(CommandHandler("lockall", security.global_lock_command))

    # Notes
    app.add_handler(CommandHandler("save", admin.save_note))
    app.add_handler(CommandHandler("get", admin.get_note))
    app.add_handler(CommandHandler("notes", admin.list_notes))
    app.add_handler(CommandHandler("delnote", admin.delete_note))

    # Security
    app.add_handler(CommandHandler("captcha", security.captcha_command))
    app.add_handler(CommandHandler("scriptfilter", security.scriptfilter_command))
    app.add_handler(CommandHandler("antiraid", security.antiraid_command))
    app.add_handler(CommandHandler("nightmode", security.nightmode_command))
    app.add_handler(CommandHandler("slowmode", security.slowmode_command))
    app.add_handler(CommandHandler("logchannel", security.logchannel_command))

    # Federation
    app.add_handler(CommandHandler("fedcreate", security.fed_create))
    app.add_handler(CommandHandler("fedjoin", security.fed_join))
    app.add_handler(CommandHandler("fedleave", security.fed_leave))
    app.add_handler(CommandHandler("fedban", security.fed_ban))
    app.add_handler(CommandHandler("fedunban", security.fed_unban))
    app.add_handler(CommandHandler("fedinfo", security.fed_info))

    # Triggers
    app.add_handler(CommandHandler("addtrigger", triggers.add_trigger_command))
    app.add_handler(CommandHandler("removetrigger", triggers.remove_trigger_command))
    app.add_handler(CommandHandler("triggers", triggers.list_triggers))

    # PM panel
    app.add_handler(CommandHandler("mygroups", pm_panel.mygroups_command))

    # ── Member join/leave handlers ──
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome.handle_member_join))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, welcome.handle_member_leave))

    # ── Callback query handlers ──
    app.add_handler(CallbackQueryHandler(admin.settings_callback, pattern=f"^{CB_PREFIX_SETTINGS}"))
    app.add_handler(CallbackQueryHandler(moderation.unwarn_confirm_callback, pattern=r"^wc:"))
    app.add_handler(CallbackQueryHandler(moderation.unwarn_cancel_callback, pattern=r"^wn:"))
    app.add_handler(CallbackQueryHandler(scheduled.cancel_job_callback, pattern=r"^canceljob:"))
    app.add_handler(CallbackQueryHandler(moderation.warn_action_callback, pattern=f"^{CB_PREFIX_WARN_ACTION}"))
    app.add_handler(CallbackQueryHandler(welcome.bot_join_callback, pattern=f"^{CB_PREFIX_BOT_JOIN}"))
    app.add_handler(CallbackQueryHandler(welcome.user_join_callback, pattern=f"^{CB_PREFIX_USER_JOIN}"))
    app.add_handler(CallbackQueryHandler(info.help_callback, pattern=f"^{CB_PREFIX_HELP}"))
    app.add_handler(CallbackQueryHandler(moderation.report_action_callback, pattern=f"^{CB_PREFIX_REPORT}"))
    app.add_handler(CallbackQueryHandler(moderation.scan_action_callback, pattern=f"^{CB_PREFIX_SCAN}"))
    app.add_handler(CallbackQueryHandler(pm_panel.pm_group_callback, pattern=f"^{CB_PREFIX_PM_GROUP}"))
    app.add_handler(CallbackQueryHandler(pm_panel.pm_settings_callback, pattern=f"^{CB_PREFIX_PM_SETTINGS}"))
    app.add_handler(CallbackQueryHandler(captcha_button_callback, pattern=f"^{CB_PREFIX_CAPTCHA}"))
    app.add_handler(CallbackQueryHandler(global_lock_callback, pattern=f"^{CB_PREFIX_GLOBAL_LOCK}"))
    app.add_handler(CallbackQueryHandler(moderation.purge_callback, pattern=f"^{CB_PREFIX_PURGE}"))
    app.add_handler(CallbackQueryHandler(userinfo_callback, pattern=f"^{CB_PREFIX_USERINFO}"))

    # ── Message handlers ──
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, _note_input_handler), group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & filters.ChatType.GROUPS, _media_input_handler), group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & filters.ChatType.GROUPS, _group1_handler), group=1)

    # ── Error handler ──
    app.add_error_handler(errors.error_handler)

    return app


async def _note_input_handler(update: Update, context) -> None:
    if await handle_note_input(update, context):
        return
    await handle_settings_input(update, context)


async def _media_input_handler(update: Update, context) -> None:
    if not update.message:
        return
    editing = context.user_data.get("editing_setting") if context.user_data else None
    if not editing:
        return
    await handle_settings_input(update, context)


async def _group1_handler(update: Update, context) -> None:
    if not update.effective_user or update.effective_chat.type == "private":
        return

    try:
        from database.connection import get_db
        from utils.helpers import upsert_user
        import time as _time
        db = await get_db()
        await upsert_user(db, update.effective_user)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        existing = await db.fetch(
            "SELECT 1 FROM chat_members WHERE chat_id = $1 AND user_id = $2",
            chat_id, user_id,
        )
        if not existing:
            role = "member"
            try:
                tg_member = await context.bot.get_chat_member(chat_id, user_id)
                if tg_member.status == "creator":
                    role = "owner"
                elif tg_member.status == "administrator":
                    role = "admin"
            except Exception:
                pass
            await db.execute(
                "INSERT INTO chat_members (chat_id, user_id, role, joined_at) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                chat_id, user_id, role, int(_time.time()),
            )
    except Exception:
        pass

    if await security.handle_captcha_text(update, context):
        return

    if update.message:
        is_service = (
            update.message.new_chat_members or
            update.message.left_chat_member or
            update.message.pinned_message or
            update.message.new_chat_photo or
            update.message.delete_chat_photo
        )
        if is_service:
            await purge_service_messages(update, context)

    if await check_script_filter(update, context):
        return

    if await check_triggers(update, context):
        return

    if await check_locked_content(update, context):
        return

    await check_message(update, context)
    await check_afk(update, context)


def build_tornado_app(ptb_app) -> Application:
    """Build a tornado Application with the health route + PTB webhook route."""
    from telegram.ext._utils.webhookhandler import WebhookHandler  # PTB's internal handler

    return Application([
        (r"/", HealthHandler),
        (f"/{BOT_TOKEN}", WebhookHandler, {"update_queue": ptb_app.update_queue}),
    ])


import os as _os


def _start_health_server(port: int) -> None:
    """Start a minimal HTTP server on the given port for Render port binding."""
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    def _run():
        try:
            server = HTTPServer(("0.0.0.0", port), _Handler)
            logger.info(f"Health server listening on port {port}")
            server.serve_forever()
        except Exception as e:
            logger.warning(f"Health server error: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def main() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    port = int(_os.environ.get("PORT", "10000"))

    # Always start health server on PORT — Render requires a bound port
    _start_health_server(port)

    application = build_application()

    if WEBHOOK_URL:
        # Webhook mode: bot receives updates via HTTP POSTs to /{BOT_TOKEN}
        # The health server on "/" runs alongside
        logger.info(f"Starting in webhook mode on {WEBHOOK_LISTEN}:{WEBHOOK_PORT}")
        application.run_webhook(
            listen=WEBHOOK_LISTEN,
            port=WEBHOOK_PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
            cert=WEBHOOK_CERT or None,
            key=WEBHOOK_KEY or None,
        )
    else:
        # Polling mode: bot polls Telegram API, health server keeps Render alive
        logger.info("Starting in polling mode")
        application.run_polling(
            allowed_updates=["message", "callback_query", "chat_member"],
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()