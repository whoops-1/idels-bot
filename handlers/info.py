from __future__ import annotations

import time
import logging
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import get_db
from utils.helpers import get_or_create_chat, get_user_role, upsert_user
from utils.constants import Role, CB_PREFIX_HELP, CB_PREFIX_USER_JOIN

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    chat_type = update.effective_chat.type
    bot_username = context.bot.username or "bot"

    if chat_type == "private":
        # Check if start has a parameter (from group link)
        args = context.args
        if args and args[0].startswith("group_"):
            chat_id = int(args[0].split("_", 1)[1])
            await update.message.reply_text(
                f"You can now manage group {chat_id} from here.\n"
                f"Use /mygroups to see all your groups."
            )
            return

        text = (
            "Welcome to the **Community Management Bot**!\n\n"
            "I help you manage Telegram groups with moderation, anti-spam, "
            "welcome messages, scheduled messages, and more.\n\n"
            "Add me to a group and make me an admin to get started!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Add to Group", url=f"https://t.me/{bot_username}?startgroup=true")],
            [
                InlineKeyboardButton("My Groups", callback_data=f"{CB_PREFIX_HELP}mygroups"),
                InlineKeyboardButton("Help", callback_data=f"{CB_PREFIX_HELP}help"),
            ],
        ])
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        db = await get_db()
        await get_or_create_chat(db, update.effective_chat.id, update.effective_chat.title or "")
        await update.message.reply_text("Bot is active! Use /help to see available commands.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    role = await get_user_role(chat_id, user_id, context.bot)
    hierarchy = Role.hierarchy()

    text = "**Available Commands**\n\n"
    text += "**General**\n"
    text += "/help - Show this message\n"
    text += "/id - Show your user ID and chat ID\n"
    text += "/rules - Show group rules\n"
    text += "/report - Report a message (reply to it)\n"
    text += "/info @user - Detailed user info\n"
    text += "/afk [reason] - Set AFK status\n"
    text += "/stats - Group statistics\n\n"

    if hierarchy[role.value] >= Role.hierarchy()[Role.ADMIN.value]:
        text += "**Moderation** (Admin)\n"
        text += "/kick @user - Kick a user\n"
        text += "/ban @user [reason] - Ban a user\n"
        text += "/unban @user - Unban a user\n"
        text += "/mute @user [duration] - Mute a user\n"
        text += "/unmute @user - Unmute a user\n"
        text += "/warn @user [reason] - Warn a user\n"
        text += "/unwarn @user - Remove latest warning\n"
        text += "/warnings @user - List warnings\n"
        text += "/purge <count> - Delete N messages\n"
        text += "/scan - Scan for deleted/inactive users\n"
        text += "/pin - Pin replied message\n"
        text += "/unpin - Unpin last message\n"
        text += "/tagall [msg] - Mention all members\n"
        text += "/blacklist - View ban history\n\n"

        text += "**User Notes** (Admin)\n"
        text += "/unote <text> - Add note (reply to user)\n"
        text += "/unotes @user - View user notes\n"
        text += "/delunote <id> - Delete a note\n\n"

        text += "**Content Control** (Admin)\n"
        text += "/addword <word> - Add banned word\n"
        text += "/removeword <word> - Remove banned word\n"
        text += "/listwords - List banned words\n"
        text += "/lock <type> - Lock content type\n"
        text += "/unlock <type> - Unlock content type\n"
        text += "/lockall - Emergency global lock\n\n"

        text += "**Links** (Admin)\n"
        text += "/linkadd <domain> - Allow a domain\n"
        text += "/linkremove <domain> - Remove domain\n"
        text += "/linklist - List allowed domains\n\n"

        text += "**Triggers** (Admin)\n"
        text += "/addtrigger <keyword> <response> - Auto-reply\n"
        text += "/removetrigger <keyword> - Remove trigger\n"
        text += "/triggers - List triggers\n\n"

        text += "**Scheduled Messages** (Admin)\n"
        text += "/scheduletext <time> | <msg> - Schedule text\n"
        text += "/schedulepoll <time> | <q> | <opts> - Schedule poll\n"
        text += "/listjobs - List scheduled messages\n"
        text += "/canceljob <id> - Cancel a job\n\n"

        text += "**Notes** (Admin)\n"
        text += "/save <name> <content> - Save a note\n"
        text += "/get <name> - Retrieve a note\n"
        text += "/notes - List all notes\n"
        text += "/delnote <name> - Delete a note\n\n"

        text += "**Security** (Admin)\n"
        text += "/captcha on|off - Join verification\n"
        text += "/scriptfilter on|off - Block non-Latin scripts\n"
        text += "/antiraid on|off - Anti-raid protection\n"
        text += "/nightmode on|off - Scheduled group lock\n"
        text += "/slowmode <seconds> - Set slow mode\n"
        text += "/logchannel <id> - Set log channel\n\n"

        text += "**Settings** (Admin)\n"
        text += "/settings - Open settings panel\n"
        text += "/setwelcome <msg> - Set welcome message\n"
        text += "/setgoodbye <msg> - Set goodbye message\n"
        text += "/setrules <text> - Set group rules\n"

    if hierarchy[role.value] >= Role.hierarchy()[Role.OWNER.value]:
        text += "\n**Owner**\n"
        text += "/setowner @user - Transfer ownership\n"
        text += "/promote @user - Promote to admin\n"
        text += "/demote @user - Demote to member\n"
        text += "/fedcreate <name> - Create federation\n"
        text += "/fedjoin <id> - Join federation\n"
        text += "/fedleave - Leave federation\n"
        text += "/fedban @user - Federation ban\n"
        text += "/fedunban @user - Federation unban\n"
        text += "/fedinfo - Federation info\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Settings", callback_data=f"{CB_PREFIX_HELP}settings"),
            InlineKeyboardButton("Rules", callback_data=f"{CB_PREFIX_HELP}rules"),
        ]
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle help menu buttons."""
    query = update.callback_query
    await query.answer()

    data = query.data[len(CB_PREFIX_HELP):]

    if data == "settings":
        from handlers.admin import _build_main_settings_keyboard
        from services.settings_service import get_chat_settings
        settings = await get_chat_settings(query.message.chat_id)
        await query.edit_message_text(
            "**Chat Settings**\nTap a button to toggle or edit.",
            reply_markup=_build_main_settings_keyboard(settings),
            parse_mode="Markdown",
        )
    elif data == "rules":
        from services.settings_service import get_chat_settings
        settings = await get_chat_settings(query.message.chat_id)
        if settings.rules_text:
            try:
                await query.message.reply_text(settings.rules_text, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(settings.rules_text)
        else:
            await query.message.reply_text("No rules have been set for this group.")
    elif data == "mygroups":
        from handlers.pm_panel import show_my_groups
        await show_my_groups(update, context)
    elif data == "help":
        text = (
            "**DM Commands**\n\n"
            "/start - Welcome message\n"
            "/help - Show commands\n"
            "/mygroups - Manage your groups from PM\n"
        )
        await query.edit_message_text(text, parse_mode="Markdown")


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"User ID: `{user_id}`\nChat ID: `{chat_id}`", parse_mode="Markdown")


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    from services.settings_service import get_chat_settings
    settings = await get_chat_settings(update.effective_chat.id)
    if settings.rules_text:
        try:
            await update.message.reply_text(settings.rules_text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(settings.rules_text)
    else:
        await update.message.reply_text("No rules have been set for this group.")


# ──────────────────────────────────────────────
# AFK SYSTEM
# ──────────────────────────────────────────────

async def afk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set AFK status."""
    if not update.message or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    reason = " ".join(context.args) if context.args else "No reason"

    db = await get_db()
    now = int(time.time())
    await db.execute(
        "UPDATE chat_members SET afk_reason = ?, afk_since = ? WHERE chat_id = ? AND user_id = ?",
        (reason, now, chat_id, user_id),
    )
    await db.commit()

    await update.message.reply_text(
        f"{update.effective_user.first_name} is now AFK.\nReason: {reason}"
    )


async def check_afk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check if mentioned user is AFK, and auto-clear AFK when user sends a message."""
    if not update.message or not update.effective_user:
        return
    if update.effective_chat.type == "private":
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    db = await get_db()

    # Auto-clear AFK when user sends a message
    rows = await db.execute_fetchall(
        "SELECT afk_reason FROM chat_members WHERE chat_id = ? AND user_id = ? AND afk_since > 0",
        (chat_id, user_id),
    )
    if rows and rows[0][0]:
        await db.execute(
            "UPDATE chat_members SET afk_reason = '', afk_since = 0 WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        await db.commit()
        await update.message.reply_text(f"Welcome back, {update.effective_user.first_name}! AFK cleared.")

    # Check if mentioned users are AFK
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention":
                mentioned_id = entity.user.id
            elif entity.type == "mention":
                username = update.message.text[entity.offset + 1:entity.offset + entity.length]
                user_rows = await db.execute_fetchall(
                    "SELECT user_id FROM users WHERE username = ? COLLATE NOCASE", (username,)
                )
                if not user_rows:
                    continue
                mentioned_id = user_rows[0][0]
            else:
                continue

            afk_rows = await db.execute_fetchall(
                "SELECT afk_reason, afk_since FROM chat_members WHERE chat_id = ? AND user_id = ? AND afk_since > 0",
                (chat_id, mentioned_id),
            )
            if afk_rows and afk_rows[0][0]:
                reason = afk_rows[0][0]
                since = afk_rows[0][1]
                elapsed = int(time.time()) - since
                from utils.helpers import format_duration
                await update.message.reply_text(
                    f"This user is AFK: {reason} (since {format_duration(elapsed)} ago)"
                )


# ──────────────────────────────────────────────
# STATS
# ──────────────────────────────────────────────

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show group statistics."""
    if not update.message:
        return

    chat_id = update.effective_chat.id
    db = await get_db()

    # Member count
    try:
        member_count = await context.bot.get_chat_member_count(chat_id)
    except Exception:
        member_count = "?"

    # Warning count
    warn_rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM warnings WHERE chat_id = ?", (chat_id,)
    )
    warn_count = warn_rows[0][0] if warn_rows else 0

    # Banned words count
    word_rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM banned_words WHERE chat_id = ?", (chat_id,)
    )
    word_count = word_rows[0][0] if word_rows else 0

    # Scheduled jobs count
    job_rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM scheduled_messages WHERE chat_id = ? AND is_active = 1", (chat_id,)
    )
    job_count = job_rows[0][0] if job_rows else 0

    # Notes count
    note_rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM notes WHERE chat_id = ?", (chat_id,)
    )
    note_count = note_rows[0][0] if note_rows else 0

    # Reports count
    report_rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM reports WHERE chat_id = ?", (chat_id,)
    )
    report_count = report_rows[0][0] if report_rows else 0

    text = (
        f"**Group Statistics**\n\n"
        f"Members: {member_count}\n"
        f"Active Warnings: {warn_count}\n"
        f"Banned Words: {word_count}\n"
        f"Scheduled Jobs: {job_count}\n"
        f"Notes: {note_count}\n"
        f"Reports: {report_count}\n"
    )

    await update.message.reply_text(text, parse_mode="Markdown")
