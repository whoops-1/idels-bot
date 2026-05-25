from __future__ import annotations

import json

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, CommandHandler, filters

from database.connection import get_db
from middleware.permissions import require_role
from services.settings_service import get_chat_settings, update_chat_setting, update_link_allowlist
from utils.constants import Role, CB_PREFIX_SETTINGS
from utils.helpers import ensure_chat_member, upsert_user, parse_json_list

logger = logging.getLogger(__name__)

# Conversation states for text input
SETTING_WELCOME, SETTING_GOODBYE, SETTING_RULES, SETTING_FLOOD_LIMIT, SETTING_FLOOD_WINDOW = range(5)


@require_role(Role.OWNER)
async def set_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        await update.message.reply_text("Reply to a user's message to set them as owner.")
        return

    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id
    db = await get_db()

    await upsert_user(db, target)
    await ensure_chat_member(db, chat_id, target.id, "owner")

    await db.execute(
        "UPDATE chats SET owner_id = ? WHERE chat_id = ?",
        (target.id, chat_id),
    )
    await db.execute(
        "UPDATE chat_members SET role = 'owner' WHERE chat_id = ? AND user_id = ?",
        (chat_id, target.id),
    )
    await db.commit()
    await update.message.reply_text(f"Ownership transferred to {target.first_name}.")


@require_role(Role.OWNER)
async def promote_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    target_id = await _resolve_target(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    db = await get_db()
    await db.execute(
        "UPDATE chat_members SET role = 'admin' WHERE chat_id = ? AND user_id = ?",
        (chat_id, target_id),
    )
    await db.commit()
    await update.message.reply_text("User promoted to admin.")


@require_role(Role.OWNER)
async def demote_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    target_id = await _resolve_target(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    db = await get_db()
    await db.execute(
        "UPDATE chat_members SET role = 'member' WHERE chat_id = ? AND user_id = ?",
        (chat_id, target_id),
    )
    await db.commit()
    await update.message.reply_text("User demoted to member.")


async def _resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        return update.message.reply_to_message.from_user.id
    if context.args:
        arg = context.args[0].lstrip("@")
        if arg.isdigit():
            return int(arg)
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT user_id FROM users WHERE username = ? COLLATE NOCASE", (arg,)
        )
        if rows:
            return rows[0][0]
        await update.message.reply_text(f"Could not find user @{arg}.")
        return None
    await update.message.reply_text("Reply to a user's message or specify @username.")
    return None


@require_role(Role.ADMIN)
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the settings panel with inline keyboard."""
    if not update.message:
        return

    settings = await get_chat_settings(update.effective_chat.id)
    keyboard = _build_main_settings_keyboard(settings)
    await update.message.reply_text(
        "**Chat Settings**\nTap a button to toggle or edit.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


def _build_main_settings_keyboard(settings) -> InlineKeyboardMarkup:
    def _on_off(val: bool) -> str:
        return "ON" if val else "OFF"

    buttons = [
        [
            InlineKeyboardButton(f"Welcome: {_on_off(settings.welcome_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_welcome"),
            InlineKeyboardButton(f"Goodbye: {_on_off(settings.goodbye_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_goodbye"),
        ],
        [
            InlineKeyboardButton(f"Anti-Spam: {_on_off(settings.antispam_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_antispam"),
            InlineKeyboardButton(f"Censor: {_on_off(settings.censor_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_censor"),
        ],
        [
            InlineKeyboardButton(f"Link Filter: {_on_off(settings.link_filter_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_linkfilter"),
            InlineKeyboardButton(f"Captcha: {_on_off(settings.captcha_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_captcha"),
        ],
        [
            InlineKeyboardButton(f"Script Filter: {_on_off(settings.script_filter_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_scriptfilter"),
            InlineKeyboardButton(f"Anti-Raid: {_on_off(settings.anti_raid_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_antiraid"),
        ],
        [
            InlineKeyboardButton(f"Night Mode: {_on_off(settings.night_mode_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_nightmode"),
            InlineKeyboardButton(f"Triggers: {_on_off(settings.triggers_enabled)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_triggers"),
        ],
        [
            InlineKeyboardButton(f"Purge Join: {_on_off(settings.purge_join)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_purge_join"),
            InlineKeyboardButton(f"Purge Leave: {_on_off(settings.purge_leave)}", callback_data=f"{CB_PREFIX_SETTINGS}toggle_purge_leave"),
        ],
        [
            InlineKeyboardButton(f"Warn Expire: {settings.warn_expire_hours}h", callback_data=f"{CB_PREFIX_SETTINGS}cycle_wexpire"),
        ],
        [
            InlineKeyboardButton("Flood Settings", callback_data=f"{CB_PREFIX_SETTINGS}flood_menu"),
            InlineKeyboardButton("Warn Settings", callback_data=f"{CB_PREFIX_SETTINGS}warn_menu"),
        ],
        [
            InlineKeyboardButton("Edit Welcome Msg", callback_data=f"{CB_PREFIX_SETTINGS}edit_welcome"),
            InlineKeyboardButton("Edit Goodbye Msg", callback_data=f"{CB_PREFIX_SETTINGS}edit_goodbye"),
        ],
        [
            InlineKeyboardButton("Edit Rules", callback_data=f"{CB_PREFIX_SETTINGS}edit_rules"),
            InlineKeyboardButton("Edit Welcome Media", callback_data=f"{CB_PREFIX_SETTINGS}edit_welcome_media"),
        ],
        [
            InlineKeyboardButton("Delete After Settings", callback_data=f"{CB_PREFIX_SETTINGS}delete_settings"),
        ],
        [
            InlineKeyboardButton("Close", callback_data=f"{CB_PREFIX_SETTINGS}close"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def _build_flood_keyboard(settings) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(f"Limit: {settings.flood_limit}", callback_data=f"{CB_PREFIX_SETTINGS}flood_limit"),
            InlineKeyboardButton(f"Window: {settings.flood_window}s", callback_data=f"{CB_PREFIX_SETTINGS}flood_window"),
        ],
        [
            InlineKeyboardButton(f"Action: {settings.flood_action}", callback_data=f"{CB_PREFIX_SETTINGS}flood_action"),
            InlineKeyboardButton(f"Mute Duration: {settings.flood_mute_duration}s", callback_data=f"{CB_PREFIX_SETTINGS}flood_mute_dur"),
        ],
        [
            InlineKeyboardButton("Back", callback_data=f"{CB_PREFIX_SETTINGS}main"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def _build_warn_keyboard(settings) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(f"Threshold: {settings.warn_threshold}", callback_data=f"{CB_PREFIX_SETTINGS}warn_threshold"),
            InlineKeyboardButton(f"Action: {settings.warn_action}", callback_data=f"{CB_PREFIX_SETTINGS}warn_action"),
        ],
        [
            InlineKeyboardButton("Back", callback_data=f"{CB_PREFIX_SETTINGS}main"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all settings inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data[len(CB_PREFIX_SETTINGS):]
    chat_id = query.message.chat_id
    settings = await get_chat_settings(chat_id)

    # Toggle boolean settings
    if data == "toggle_welcome":
        new_val = not settings.welcome_enabled
        await update_chat_setting(chat_id, "welcome_enabled", int(new_val))
        settings.welcome_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_goodbye":
        new_val = not settings.goodbye_enabled
        await update_chat_setting(chat_id, "goodbye_enabled", int(new_val))
        settings.goodbye_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_antispam":
        new_val = not settings.antispam_enabled
        await update_chat_setting(chat_id, "antispam_enabled", int(new_val))
        settings.antispam_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_censor":
        new_val = not settings.censor_enabled
        await update_chat_setting(chat_id, "censor_enabled", int(new_val))
        settings.censor_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_linkfilter":
        new_val = not settings.link_filter_enabled
        await update_chat_setting(chat_id, "link_filter_enabled", int(new_val))
        settings.link_filter_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    # Sub-menus
    elif data == "main":
        settings = await get_chat_settings(chat_id)
        await query.edit_message_text(
            "**Chat Settings**\nTap a button to toggle or edit.",
            reply_markup=_build_main_settings_keyboard(settings),
            parse_mode="Markdown",
        )

    elif data == "flood_menu":
        await query.edit_message_text(
            "**Flood Settings**",
            reply_markup=_build_flood_keyboard(settings),
            parse_mode="Markdown",
        )

    elif data == "warn_menu":
        await query.edit_message_text(
            "**Warning Settings**",
            reply_markup=_build_warn_keyboard(settings),
            parse_mode="Markdown",
        )

    # Cycle flood action
    elif data == "flood_action":
        actions = ["mute", "kick", "ban"]
        idx = actions.index(settings.flood_action) if settings.flood_action in actions else 0
        new_action = actions[(idx + 1) % len(actions)]
        await update_chat_setting(chat_id, "flood_action", new_action)
        settings.flood_action = new_action
        await query.edit_message_reply_markup(reply_markup=_build_flood_keyboard(settings))

    # Cycle warn action
    elif data == "warn_action":
        actions = ["ban", "kick", "mute"]
        idx = actions.index(settings.warn_action) if settings.warn_action in actions else 0
        new_action = actions[(idx + 1) % len(actions)]
        await update_chat_setting(chat_id, "warn_action", new_action)
        settings.warn_action = new_action
        await query.edit_message_reply_markup(reply_markup=_build_warn_keyboard(settings))

    # Cycle warn threshold (2-10)
    elif data == "warn_threshold":
        new_val = (settings.warn_threshold % 10) + 1
        await update_chat_setting(chat_id, "warn_threshold", new_val)
        settings.warn_threshold = new_val
        await query.edit_message_reply_markup(reply_markup=_build_warn_keyboard(settings))

    # Cycle flood limit (3-20)
    elif data == "flood_limit":
        new_val = (settings.flood_limit - 2) % 18 + 3
        await update_chat_setting(chat_id, "flood_limit", new_val)
        settings.flood_limit = new_val
        await query.edit_message_reply_markup(reply_markup=_build_flood_keyboard(settings))

    # Cycle flood window (5-60s)
    elif data == "flood_window":
        options = [5, 10, 15, 20, 30, 45, 60]
        idx = options.index(settings.flood_window) if settings.flood_window in options else 1
        new_val = options[(idx + 1) % len(options)]
        await update_chat_setting(chat_id, "flood_window", new_val)
        settings.flood_window = new_val
        await query.edit_message_reply_markup(reply_markup=_build_flood_keyboard(settings))

    # Cycle flood mute duration
    elif data == "flood_mute_dur":
        options = [60, 300, 600, 1800, 3600]
        idx = options.index(settings.flood_mute_duration) if settings.flood_mute_duration in options else 1
        new_val = options[(idx + 1) % len(options)]
        await update_chat_setting(chat_id, "flood_mute_duration", new_val)
        settings.flood_mute_duration = new_val
        await query.edit_message_reply_markup(reply_markup=_build_flood_keyboard(settings))

    # Close
    elif data == "toggle_captcha":
        new_val = not settings.captcha_enabled
        await update_chat_setting(chat_id, "captcha_enabled", int(new_val))
        settings.captcha_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_scriptfilter":
        new_val = not settings.script_filter_enabled
        await update_chat_setting(chat_id, "script_filter_enabled", int(new_val))
        settings.script_filter_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_antiraid":
        new_val = not settings.anti_raid_enabled
        await update_chat_setting(chat_id, "anti_raid_enabled", int(new_val))
        settings.anti_raid_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_nightmode":
        new_val = not settings.night_mode_enabled
        await update_chat_setting(chat_id, "night_mode_enabled", int(new_val))
        settings.night_mode_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_triggers":
        new_val = not settings.triggers_enabled
        await update_chat_setting(chat_id, "triggers_enabled", int(new_val))
        settings.triggers_enabled = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_purge_join":
        new_val = not settings.purge_join
        await update_chat_setting(chat_id, "purge_join", int(new_val))
        settings.purge_join = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "toggle_purge_leave":
        new_val = not settings.purge_leave
        await update_chat_setting(chat_id, "purge_leave", int(new_val))
        settings.purge_leave = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "cycle_wexpire":
        options = [0, 1, 6, 12, 24, 48, 72, 168]
        idx = options.index(settings.warn_expire_hours) if settings.warn_expire_hours in options else 0
        new_val = options[(idx + 1) % len(options)]
        await update_chat_setting(chat_id, "warn_expire_hours", new_val)
        settings.warn_expire_hours = new_val
        await query.edit_message_reply_markup(reply_markup=_build_main_settings_keyboard(settings))

    elif data == "delete_settings":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"Welcome Delete: {settings.welcome_delete_seconds}s", callback_data=f"{CB_PREFIX_SETTINGS}cycle_welcome_del"),
                InlineKeyboardButton(f"Goodbye Delete: {settings.goodbye_delete_seconds}s", callback_data=f"{CB_PREFIX_SETTINGS}cycle_goodbye_del"),
            ],
            [InlineKeyboardButton("Back", callback_data=f"{CB_PREFIX_SETTINGS}main")],
        ])
        await query.edit_message_text("**Auto-Delete Settings**\n\nHow long before welcome/goodbye messages are auto-deleted (0 = never):", reply_markup=keyboard, parse_mode="Markdown")

    elif data == "cycle_welcome_del":
        options = [0, 30, 60, 120, 300, 600]
        idx = options.index(settings.welcome_delete_seconds) if settings.welcome_delete_seconds in options else 0
        new_val = options[(idx + 1) % len(options)]
        await update_chat_setting(chat_id, "welcome_delete_seconds", new_val)
        settings.welcome_delete_seconds = new_val
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"Welcome Delete: {settings.welcome_delete_seconds}s", callback_data=f"{CB_PREFIX_SETTINGS}cycle_welcome_del"),
                InlineKeyboardButton(f"Goodbye Delete: {settings.goodbye_delete_seconds}s", callback_data=f"{CB_PREFIX_SETTINGS}cycle_goodbye_del"),
            ],
            [InlineKeyboardButton("Back", callback_data=f"{CB_PREFIX_SETTINGS}main")],
        ])
        await query.edit_message_reply_markup(reply_markup=keyboard)

    elif data == "cycle_goodbye_del":
        options = [0, 30, 60, 120, 300, 600]
        idx = options.index(settings.goodbye_delete_seconds) if settings.goodbye_delete_seconds in options else 0
        new_val = options[(idx + 1) % len(options)]
        await update_chat_setting(chat_id, "goodbye_delete_seconds", new_val)
        settings.goodbye_delete_seconds = new_val
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"Welcome Delete: {settings.welcome_delete_seconds}s", callback_data=f"{CB_PREFIX_SETTINGS}cycle_welcome_del"),
                InlineKeyboardButton(f"Goodbye Delete: {settings.goodbye_delete_seconds}s", callback_data=f"{CB_PREFIX_SETTINGS}cycle_goodbye_del"),
            ],
            [InlineKeyboardButton("Back", callback_data=f"{CB_PREFIX_SETTINGS}main")],
        ])
        await query.edit_message_reply_markup(reply_markup=keyboard)

    elif data == "edit_welcome_media":
        context.user_data["editing_setting"] = "welcome_media"
        await query.edit_message_text(
            "Send a photo/video/gif to use as welcome media, or send 'none' to remove.\n\n"
            "The media will be attached to welcome messages."
        )

    elif data == "close":
        await query.edit_message_text("Settings closed.")

    # Text input requests — store state and prompt
    elif data == "edit_welcome":
        context.user_data["editing_setting"] = "welcome"
        await query.edit_message_text("Send the new welcome message.\n\nPlaceholders: {user_mention}, {user_name}, {user_first_name}, {chat_name}, {member_count}")

    elif data == "edit_goodbye":
        context.user_data["editing_setting"] = "goodbye"
        await query.edit_message_text("Send the new goodbye message.\n\nPlaceholders: {user_name}, {user_first_name}, {chat_name}")

    elif data == "edit_rules":
        context.user_data["editing_setting"] = "rules"
        await query.edit_message_text("Send the new rules text.")


async def handle_settings_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text replies for settings editing."""
    if not update.message:
        return

    editing = context.user_data.get("editing_setting")
    if not editing:
        return

    chat_id = update.effective_chat.id

    # Handle media uploads for welcome media
    if editing == "welcome_media":
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            await update_chat_setting(chat_id, "welcome_media", file_id)
            await update_chat_setting(chat_id, "welcome_media_type", "photo")
            await update.message.reply_text("Welcome photo set!")
            del context.user_data["editing_setting"]
            return
        elif update.message.video:
            file_id = update.message.video.file_id
            await update_chat_setting(chat_id, "welcome_media", file_id)
            await update_chat_setting(chat_id, "welcome_media_type", "video")
            await update.message.reply_text("Welcome video set!")
            del context.user_data["editing_setting"]
            return
        elif update.message.animation:
            file_id = update.message.animation.file_id
            await update_chat_setting(chat_id, "welcome_media", file_id)
            await update_chat_setting(chat_id, "welcome_media_type", "gif")
            await update.message.reply_text("Welcome GIF set!")
            del context.user_data["editing_setting"]
            return
        elif update.message.text and update.message.text.lower() == "none":
            await update_chat_setting(chat_id, "welcome_media", "")
            await update_chat_setting(chat_id, "welcome_media_type", "")
            await update.message.reply_text("Welcome media removed.")
            del context.user_data["editing_setting"]
            return
        return  # Wait for valid media or "none"

    if not update.message.text:
        return

    text = update.message.text

    if editing == "welcome":
        await update_chat_setting(chat_id, "welcome_message", text)
        await update.message.reply_text("Welcome message updated.")
    elif editing == "goodbye":
        await update_chat_setting(chat_id, "goodbye_message", text)
        await update.message.reply_text("Goodbye message updated.")
    elif editing == "rules":
        await update_chat_setting(chat_id, "rules_text", text)
        await update.message.reply_text("Rules updated.")

    del context.user_data["editing_setting"]


def _extract_after_command(update: Update) -> str | None:
    """Extract the text after the /command (preserving newlines and formatting)."""
    if not update.message or not update.message.text:
        return None
    full = update.message.text
    # Split only on the first space to separate the command word
    parts = full.split(None, 1)
    if len(parts) < 2:
        return None
    return parts[1].strip()


@require_role(Role.ADMIN)
async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    text = _extract_after_command(update)
    if not text:
        await update.message.reply_text(
            "Usage: /setwelcome <message>\n\n"
            "Placeholders: {user_mention}, {user_name}, {user_first_name}, {chat_name}, {member_count}"
        )
        return
    await update_chat_setting(update.effective_chat.id, "welcome_message", text)
    await update.message.reply_text("Welcome message updated.")


@require_role(Role.ADMIN)
async def set_goodbye(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    text = _extract_after_command(update)
    if not text:
        await update.message.reply_text(
            "Usage: /setgoodbye <message>\n\n"
            "Placeholders: {user_name}, {user_first_name}, {chat_name}"
        )
        return
    await update_chat_setting(update.effective_chat.id, "goodbye_message", text)
    await update.message.reply_text("Goodbye message updated.")


@require_role(Role.ADMIN)
async def set_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    text = _extract_after_command(update)
    if not text:
        await update.message.reply_text("Usage: /setrules <rules text>")
        return
    await update_chat_setting(update.effective_chat.id, "rules_text", text)
    await update.message.reply_text("Rules updated.")


# ──────────────────────────────────────────────
# LOCK / UNLOCK
# ──────────────────────────────────────────────

VALID_LOCK_TYPES = {"media", "sticker", "gif", "forward", "url", "game", "inline", "photo", "video", "audio", "voice", "document"}


@require_role(Role.ADMIN)
async def lock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lock a content type in the group."""
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text(
            f"Usage: /lock <type>\n\nValid types: {', '.join(sorted(VALID_LOCK_TYPES))}"
        )
        return

    lock_type = context.args[0].lower()
    if lock_type not in VALID_LOCK_TYPES:
        await update.message.reply_text(f"Invalid type. Valid: {', '.join(sorted(VALID_LOCK_TYPES))}")
        return

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    locked = list(settings.locked_types)

    if lock_type in locked:
        await update.message.reply_text(f"`{lock_type}` is already locked.", parse_mode="Markdown")
        return

    locked.append(lock_type)
    await update_chat_setting(chat_id, "locked_types", json.dumps(locked))
    await update.message.reply_text(f"Locked `{lock_type}`.", parse_mode="Markdown")


@require_role(Role.ADMIN)
async def unlock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unlock a content type in the group."""
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text(f"Usage: /unlock <type>\n\nValid types: {', '.join(sorted(VALID_LOCK_TYPES))}")
        return

    lock_type = context.args[0].lower()
    if lock_type not in VALID_LOCK_TYPES:
        await update.message.reply_text(f"Invalid type. Valid: {', '.join(sorted(VALID_LOCK_TYPES))}")
        return

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    locked = list(settings.locked_types)

    if lock_type not in locked:
        await update.message.reply_text(f"`{lock_type}` is not locked.", parse_mode="Markdown")
        return

    locked.remove(lock_type)
    await update_chat_setting(chat_id, "locked_types", json.dumps(locked))
    await update.message.reply_text(f"Unlocked `{lock_type}`.", parse_mode="Markdown")


async def check_locked_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if a message violates locked content types. Returns True if message was deleted."""
    if not update.message:
        return False
    if update.effective_chat.type == "private":
        return False

    from utils.helpers import has_permission
    # Don't restrict admins
    if await has_permission(update.effective_chat.id, update.effective_user.id, Role.ADMIN, context.bot):
        return False

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    if not settings.locked_types:
        return False

    locked = set(settings.locked_types)
    msg = update.message
    violation = None

    if "media" in locked and (msg.photo or msg.video or msg.audio or msg.voice or msg.document):
        violation = "media"
    elif "photo" in locked and msg.photo:
        violation = "photos"
    elif "video" in locked and msg.video:
        violation = "videos"
    elif "audio" in locked and msg.audio:
        violation = "audio"
    elif "voice" in locked and msg.voice:
        violation = "voice messages"
    elif "document" in locked and msg.document:
        violation = "documents"
    elif "sticker" in locked and msg.sticker:
        violation = "stickers"
    elif "gif" in locked and (msg.animation or (msg.document and msg.document.mime_type and "gif" in msg.document.mime_type)):
        violation = "GIFs"
    elif "forward" in locked and msg.forward_origin:
        violation = "forwarded messages"
    elif "url" in locked and msg.entities:
        for entity in msg.entities:
            if entity.type in ("url", "text_link"):
                violation = "links"
                break
    elif "game" in locked and msg.game:
        violation = "games"
    elif "inline" in locked and msg.via_bot:
        violation = "inline messages"

    if violation:
        try:
            await msg.delete()
            await msg.reply_text(
                f"{msg.from_user.first_name}, {violation} are not allowed in this chat.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return True

    return False


# ──────────────────────────────────────────────
# NOTES SYSTEM
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def save_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save a note. Usage: /save <name> <content>"""
    if not update.message or not update.effective_user:
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /save <name> <content>")
        return

    name = context.args[0].lower()
    content = " ".join(context.args[1:])
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    db = await get_db()
    await db.execute(
        "INSERT INTO notes (chat_id, name, content, created_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id, name) DO UPDATE SET content = excluded.content",
        (chat_id, name, content, user_id),
    )
    await db.commit()
    await update.message.reply_text(f"Note `{name}` saved.", parse_mode="Markdown")


async def get_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Retrieve a note. Usage: /get <name>"""
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /get <name>")
        return

    name = context.args[0].lower()
    chat_id = update.effective_chat.id

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT content FROM notes WHERE chat_id = ? AND name = ?",
        (chat_id, name),
    )
    if not rows:
        await update.message.reply_text(f"Note `{name}` not found.", parse_mode="Markdown")
        return

    await update.message.reply_text(rows[0][0])


@require_role(Role.ADMIN)
async def list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all notes in the chat."""
    if not update.message:
        return
    chat_id = update.effective_chat.id

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT name FROM notes WHERE chat_id = ? ORDER BY name", (chat_id,)
    )
    if not rows:
        await update.message.reply_text("No notes saved in this chat.")
        return

    text = "**Notes:**\n"
    for row in rows:
        text += f"- `{row[0]}` — /get {row[0]}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


@require_role(Role.ADMIN)
async def delete_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a note. Usage: /delnote <name>"""
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /delnote <name>")
        return

    name = context.args[0].lower()
    chat_id = update.effective_chat.id

    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM notes WHERE chat_id = ? AND name = ?", (chat_id, name)
    )
    await db.commit()
    if cursor.rowcount > 0:
        await update.message.reply_text(f"Note `{name}` deleted.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Note `{name}` not found.", parse_mode="Markdown")
