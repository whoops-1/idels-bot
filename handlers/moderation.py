from __future__ import annotations

import time
import logging

from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import get_db
from middleware.permissions import require_role
from services import warning_service
from services.settings_service import get_chat_settings
from utils.constants import (
    Role,
    WarnAction,
    CB_PREFIX_WARN_CONFIRM,
    CB_PREFIX_WARN_CANCEL,
    CB_PREFIX_UNWARN_LIST,
    CB_PREFIX_WARN_ACTION,
    CB_PREFIX_REPORT,
    CB_PREFIX_SCAN,
)
from utils.helpers import (
    upsert_user,
    ensure_chat_member,
    parse_duration,
    format_duration,
    escape_markdown,
    build_user_mention,
)

logger = logging.getLogger(__name__)


async def _resolve_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Determine the target user ID from reply or argument."""
    if not update.message:
        return None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        return update.message.reply_to_message.from_user.id

    if context.args:
        arg = context.args[0]
        if arg.startswith("@"):
            arg = arg[1:]
        if arg.isdigit():
            return int(arg)
        db = await get_db()
        rows = await db.fetch(
            "SELECT user_id FROM users WHERE LOWER(username) = LOWER($1)", arg,
        )
        if rows:
            return rows[0]["user_id"]
        await update.message.reply_text(f"Could not find user @{arg}. Reply to their message instead.")
        return None

    await update.message.reply_text("Reply to a user's message or specify @username.")
    return None


async def _full_permissions():
    return ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=True,
        can_invite_users=True,
        can_pin_messages=True,
    )


# ──────────────────────────────────────────────
# MODERATION COMMANDS
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target_id)
        await context.bot.unban_chat_member(chat_id, target_id)
        await update.message.reply_text("User has been kicked.")
    except Exception as e:
        await update.message.reply_text(f"Failed to kick user: {e}")


@require_role(Role.ADMIN)
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    is_reply = update.message.reply_to_message is not None
    reason = " ".join(context.args) if is_reply else " ".join(context.args[1:])
    try:
        await context.bot.ban_chat_member(chat_id, target_id)
        msg = "User has been banned."
        if reason:
            msg += f"\nReason: {reason}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Failed to ban user: {e}")


@require_role(Role.ADMIN)
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    try:
        await context.bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        await update.message.reply_text("User has been unbanned.")
    except Exception as e:
        await update.message.reply_text(f"Failed to unban user: {e}")


@require_role(Role.ADMIN)
async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    duration_seconds = 0

    if len(context.args) > 1:
        dur = parse_duration(context.args[1])
        if dur:
            duration_seconds = dur

    try:
        await context.bot.restrict_chat_member(
            chat_id, target_id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        db = await get_db()
        muted_until = int(time.time()) + duration_seconds if duration_seconds else 0
        await db.execute(
            "UPDATE chat_members SET is_muted = 1, muted_until = $1 WHERE chat_id = $2 AND user_id = $3",
            muted_until, chat_id, target_id,
        )

        msg = "User has been muted"
        if duration_seconds:
            msg += f" for {format_duration(duration_seconds)}"
            context.job_queue.run_once(
                _unmute_job, when=duration_seconds,
                data={"chat_id": chat_id, "user_id": target_id},
                name=f"unmute_{chat_id}_{target_id}",
            )
        msg += "."
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Failed to mute user: {e}")


async def _unmute_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    chat_id, user_id = data["chat_id"], data["user_id"]
    try:
        await context.bot.restrict_chat_member(chat_id, user_id, permissions=await _full_permissions())
        db = await get_db()
        await db.execute(
            "UPDATE chat_members SET is_muted = 0, muted_until = 0 WHERE chat_id = $1 AND user_id = $2",
            chat_id, user_id,
        )
    except Exception as e:
        logger.error(f"Failed to auto-unmute user {user_id} in chat {chat_id}: {e}")


@require_role(Role.ADMIN)
async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    try:
        await context.bot.restrict_chat_member(chat_id, target_id, permissions=await _full_permissions())
        db = await get_db()
        await db.execute(
            "UPDATE chat_members SET is_muted = 0, muted_until = 0 WHERE chat_id = $1 AND user_id = $2",
            chat_id, target_id,
        )
        current_jobs = context.job_queue.get_jobs_by_name(f"unmute_{chat_id}_{target_id}")
        for job in current_jobs:
            job.schedule_removal()
        await update.message.reply_text("User has been unmuted.")
    except Exception as e:
        await update.message.reply_text(f"Failed to unmute user: {e}")


# ──────────────────────────────────────────────
# WARN SYSTEM WITH ACTION BUTTONS
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    issuer_id = update.effective_user.id
    is_reply = update.message.reply_to_message is not None
    reason = " ".join(context.args) if is_reply else " ".join(context.args[1:])

    db = await get_db()
    await upsert_user(db, update.effective_user)
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        await upsert_user(db, update.message.reply_to_message.from_user)

    settings = await get_chat_settings(chat_id)
    count = await warning_service.add_warning(chat_id, target_id, issuer_id, reason)

    msg = f"User has been warned ({count}/{settings.warn_threshold})."
    if reason:
        msg += f"\nReason: {reason}"

    if count >= settings.warn_threshold:
        action = settings.warn_action
        try:
            if action == WarnAction.BAN.value:
                await context.bot.ban_chat_member(chat_id, target_id)
                msg += "\n\nUser has been automatically banned (warning threshold reached)."
            elif action == WarnAction.KICK.value:
                await context.bot.ban_chat_member(chat_id, target_id)
                await context.bot.unban_chat_member(chat_id, target_id)
                msg += "\n\nUser has been automatically kicked (warning threshold reached)."
            elif action == WarnAction.MUTE.value:
                await context.bot.restrict_chat_member(
                    chat_id, target_id,
                    permissions=ChatPermissions(can_send_messages=False),
                )
                msg += f"\n\nUser has been automatically muted for {format_duration(settings.warn_mute_duration)} (warning threshold reached)."
                context.job_queue.run_once(
                    _unmute_job, when=settings.warn_mute_duration,
                    data={"chat_id": chat_id, "user_id": target_id},
                    name=f"unmute_{chat_id}_{target_id}",
                )
        except Exception as e:
            msg += f"\n\nFailed to execute auto-action: {e}"
        await warning_service.clear_warnings(chat_id, target_id)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"Reset Warns ({count})", callback_data=f"{CB_PREFIX_WARN_ACTION}reset:{chat_id}:{target_id}"),
            InlineKeyboardButton("Mute", callback_data=f"{CB_PREFIX_WARN_ACTION}mute:{chat_id}:{target_id}"),
            InlineKeyboardButton("Ban", callback_data=f"{CB_PREFIX_WARN_ACTION}ban:{chat_id}:{target_id}"),
        ]
    ])
    await update.message.reply_text(msg, reply_markup=keyboard)


async def warn_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle warn action buttons: reset/mute/ban."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    from utils.helpers import has_permission
    if not await has_permission(chat_id, user_id, Role.ADMIN, context.bot):
        await query.answer("Only admins can use these buttons.", show_alert=True)
        return

    data = query.data[len(CB_PREFIX_WARN_ACTION):]
    parts = data.split(":")
    action = parts[0]
    target_chat_id = int(parts[1])
    target_user_id = int(parts[2])

    if action == "reset":
        await warning_service.clear_warnings(target_chat_id, target_user_id)
        settings = await get_chat_settings(target_chat_id)
        await query.edit_message_text(
            f"{query.message.text}\n\n<b>Warns reset by admin.</b> (0/{settings.warn_threshold})",
            parse_mode="HTML",
        )

    elif action == "mute":
        try:
            await context.bot.restrict_chat_member(
                target_chat_id, target_user_id,
                permissions=ChatPermissions(can_send_messages=False),
            )
            settings = await get_chat_settings(target_chat_id)
            context.job_queue.run_once(
                _unmute_job, when=settings.warn_mute_duration,
                data={"chat_id": target_chat_id, "user_id": target_user_id},
                name=f"unmute_{target_chat_id}_{target_user_id}",
            )
            await query.edit_message_text(
                f"{query.message.text}\n\n<b>User muted by admin for {format_duration(settings.warn_mute_duration)}.</b>",
                parse_mode="HTML",
            )
        except Exception as e:
            await query.edit_message_text(f"{query.message.text}\n\nFailed to mute: {e}")

    elif action == "ban":
        try:
            await context.bot.ban_chat_member(target_chat_id, target_user_id)
            await query.edit_message_text(
                f"{query.message.text}\n\n<b>User banned by admin.</b>",
                parse_mode="HTML",
            )
        except Exception as e:
            await query.edit_message_text(f"{query.message.text}\n\nFailed to ban: {e}")


@require_role(Role.ADMIN)
async def unwarn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    warnings = await warning_service.get_warnings(chat_id, target_id)

    if not warnings:
        await update.message.reply_text("This user has no warnings.")
        return

    latest = warnings[0]
    reason_text = f"\nReason: {latest.reason}" if latest.reason else ""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirm", callback_data=f"{CB_PREFIX_WARN_CONFIRM}{latest.id}"),
            InlineKeyboardButton("Cancel", callback_data=f"{CB_PREFIX_WARN_CANCEL}{latest.id}"),
        ]
    ])
    await update.message.reply_text(
        f"Remove latest warning (issued at <code>{latest.issued_at}</code>)?{reason_text}",
        reply_markup=keyboard, parse_mode="HTML",
    )


async def unwarn_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    warning_id = int(query.data[len(CB_PREFIX_WARN_CONFIRM):])
    db = await get_db()
    row = await db.fetchrow("DELETE FROM warnings WHERE id = $1 RETURNING id", warning_id)
    if not row:
        await query.edit_message_text("Warning not found or already removed.")
        return
    await query.edit_message_text("Warning removed.")


async def unwarn_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Cancelled.")


@require_role(Role.ADMIN)
async def warnings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    warnings = await warning_service.get_warnings(chat_id, target_id)

    if not warnings:
        await update.message.reply_text("This user has no warnings.")
        return

    text = f"Warnings for user ({len(warnings)}/{settings.warn_threshold}):\n\n"
    for i, w in enumerate(warnings[:20], 1):
        reason = w.reason or "No reason"
        text += f"{i}. {reason}\n"

    await update.message.reply_text(text)


# ──────────────────────────────────────────────
# REPORT SYSTEM
# ──────────────────────────────────────────────

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any member can report a message by replying to it."""
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Reply to a message to report it.")
        return

    reporter = update.effective_user
    reported = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id

    if not reported or reported.id == reporter.id:
        await update.message.reply_text("You can't report yourself.")
        return

    if reported.id == context.bot.id:
        await update.message.reply_text("You can't report the bot.")
        return

    message_text = update.message.reply_to_message.text or ""
    db = await get_db()
    await db.execute(
        "INSERT INTO reports (chat_id, reported_user_id, reporter_user_id, message_text) VALUES ($1, $2, $3, $4)",
        chat_id, reported.id, reporter.id, message_text[:500],
    )

    mention = f'<a href="tg://user?id={reporter.id}">{reporter.first_name}</a>'
    reported_mention = f'<a href="tg://user?id={reported.id}">{reported.first_name}</a>'

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Ban", callback_data=f"{CB_PREFIX_REPORT}ban:{chat_id}:{reported.id}"),
            InlineKeyboardButton("Mute", callback_data=f"{CB_PREFIX_REPORT}mute:{chat_id}:{reported.id}"),
            InlineKeyboardButton("Warn", callback_data=f"{CB_PREFIX_REPORT}warn:{chat_id}:{reported.id}"),
            InlineKeyboardButton("Ignore", callback_data=f"{CB_PREFIX_REPORT}ignore:{chat_id}:{reported.id}"),
        ]
    ])

    text = (
        f"Report by {mention}\n"
        f"Reported user: {reported_mention}\n"
    )
    if message_text:
        text += f"Message: {message_text[:200]}"

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def report_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle report action buttons."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    from utils.helpers import has_permission
    if not await has_permission(chat_id, user_id, Role.ADMIN, context.bot):
        await query.answer("Only admins can use these buttons.", show_alert=True)
        return

    data = query.data[len(CB_PREFIX_REPORT):]
    parts = data.split(":")
    action = parts[0]
    target_chat_id = int(parts[1])
    target_user_id = int(parts[2])

    db = await get_db()
    await db.execute(
        "UPDATE reports SET status = $1 WHERE chat_id = $2 AND reported_user_id = $3 AND status = 'pending'",
        action, target_chat_id, target_user_id,
    )

    if action == "ban":
        try:
            await context.bot.ban_chat_member(target_chat_id, target_user_id)
            await query.edit_message_text(f"{query.message.text}\n\n<b>Action: Banned by admin.</b>", parse_mode="HTML")
        except Exception as e:
            await query.edit_message_text(f"{query.message.text}\n\nFailed: {e}")

    elif action == "mute":
        try:
            await context.bot.restrict_chat_member(
                target_chat_id, target_user_id,
                permissions=ChatPermissions(can_send_messages=False),
            )
            await query.edit_message_text(f"{query.message.text}\n\n<b>Action: Muted by admin.</b>", parse_mode="HTML")
        except Exception as e:
            await query.edit_message_text(f"{query.message.text}\n\nFailed: {e}")

    elif action == "warn":
        issuer_id = query.from_user.id
        db = await get_db()
        await upsert_user(db, query.from_user)
        settings = await get_chat_settings(target_chat_id)
        count = await warning_service.add_warning(target_chat_id, target_user_id, issuer_id, "Reported by user")
        await query.edit_message_text(
            f"{query.message.text}\n\n<b>Action: Warned by admin ({count}/{settings.warn_threshold}).</b>",
            parse_mode="HTML",
        )

    elif action == "ignore":
        await query.edit_message_text(f"{query.message.text}\n\n<b>Action: Ignored.</b>", parse_mode="HTML")


# ──────────────────────────────────────────────
# GROUP SCAN (DELETED/INACTIVE USERS)
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scan group for deleted and inactive users."""
    if not update.message:
        return

    chat_id = update.effective_chat.id
    msg = await update.message.reply_text("Scanning group members... This may take a moment.")

    deleted_users = []
    inactive_users = []
    now = int(time.time())
    inactivity_threshold = 90 * 86400  # 90 days

    db = await get_db()
    rows = await db.fetch(
        "SELECT user_id, last_seen FROM chat_members WHERE chat_id = $1", chat_id,
    )

    for row in rows:
        uid = row["user_id"]
        last_seen = row["last_seen"]
        try:
            member = await context.bot.get_chat_member(chat_id, uid)
            if member.status in ("left", "kicked"):
                continue
            if getattr(member.user, "is_deleted", False):
                deleted_users.append(uid)
            elif last_seen > 0 and (now - last_seen) > inactivity_threshold:
                inactive_users.append((uid, last_seen))
        except Exception:
            deleted_users.append(uid)

    if not deleted_users and not inactive_users:
        await msg.edit_text("Scan complete! No deleted or inactive users found.")
        return

    text = f"<b>Scan Results</b>\n\n"
    if deleted_users:
        text += f"Deleted accounts: {len(deleted_users)}\n"
    if inactive_users:
        text += f"Inactive (90+ days): {len(inactive_users)}\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"Kick Deleted ({len(deleted_users)})", callback_data=f"{CB_PREFIX_SCAN}deleted:{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"Kick All ({len(deleted_users) + len(inactive_users)})", callback_data=f"{CB_PREFIX_SCAN}all:{chat_id}"),
        ],
        [
            InlineKeyboardButton("Cancel", callback_data=f"{CB_PREFIX_SCAN}cancel:{chat_id}"),
        ],
    ])

    context.chat_data["scan_deleted"] = deleted_users
    context.chat_data["scan_inactive"] = [uid for uid, _ in inactive_users]

    await msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


async def scan_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle scan result buttons."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    from utils.helpers import has_permission
    if not await has_permission(chat_id, user_id, Role.ADMIN, context.bot):
        await query.answer("Only admins can use these buttons.", show_alert=True)
        return

    data = query.data[len(CB_PREFIX_SCAN):]
    parts = data.split(":")
    action = parts[0]
    target_chat_id = int(parts[1])

    deleted = context.chat_data.get("scan_deleted", [])
    inactive = context.chat_data.get("scan_inactive", [])

    if action == "cancel":
        await query.edit_message_text("Scan cancelled.")
        return

    users_to_kick = []
    if action == "deleted":
        users_to_kick = deleted
    elif action == "all":
        users_to_kick = deleted + inactive

    if not users_to_kick:
        await query.edit_message_text("No users to kick.")
        return

    await query.edit_message_text(f"Kicking {len(users_to_kick)} users... Please wait.")

    kicked = 0
    failed = 0
    for uid in users_to_kick:
        try:
            await context.bot.ban_chat_member(target_chat_id, uid)
            await context.bot.unban_chat_member(target_chat_id, uid)
            kicked += 1
        except Exception:
            failed += 1

    from services.settings_service import update_chat_setting
    await update_chat_setting(target_chat_id, "last_scan", int(time.time()))

    await query.edit_message_text(
        f"<b>Scan Complete</b>\n\n"
        f"Kicked: {kicked}\n"
        f"Failed: {failed}",
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────
# PIN / UNPIN
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def pin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pin a replied message."""
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Reply to a message to pin it.")
        return
    try:
        await context.bot.pin_chat_message(
            update.effective_chat.id,
            update.message.reply_to_message.message_id,
        )
        await update.message.reply_text("Message pinned.")
    except Exception as e:
        await update.message.reply_text(f"Failed to pin: {e}")


@require_role(Role.ADMIN)
async def unpin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unpin the last pinned message or a specific one."""
    chat_id = update.effective_chat.id
    try:
        if update.message.reply_to_message:
            await context.bot.unpin_chat_message(chat_id, update.message.reply_to_message.message_id)
        else:
            await context.bot.unpin_chat_message(chat_id)
        await update.message.reply_text("Message unpinned.")
    except Exception as e:
        await update.message.reply_text(f"Failed to unpin: {e}")


# ──────────────────────────────────────────────
# TAGALL
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def tagall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ping all group members."""
    if not update.message:
        return

    chat_id = update.effective_chat.id
    message = " ".join(context.args) if context.args else "Attention everyone!"

    db = await get_db()
    rows = await db.fetch(
        "SELECT u.user_id, u.first_name FROM chat_members cm "
        "JOIN users u ON cm.user_id = u.user_id "
        "WHERE cm.chat_id = $1 AND cm.role = 'member'",
        chat_id,
    )

    if not rows:
        await update.message.reply_text("No members found in database.")
        return

    mentions = []
    for row in rows:
        uid, first_name = row["user_id"], row["first_name"] or "User"
        mentions.append(f'<a href="tg://user?id={uid}">{first_name}</a>')

    header = f"<b>{message}</b>\n\n"
    batch = header
    for i, mention in enumerate(mentions):
        batch += mention + " "
        if (i + 1) % 5 == 0:
            try:
                await update.message.reply_text(batch.strip(), parse_mode="HTML")
            except Exception:
                pass
            batch = header

    remaining = batch.strip()
    if remaining and remaining != header.strip():
        try:
            await update.message.reply_text(remaining, parse_mode="HTML")
        except Exception:
            pass


# ──────────────────────────────────────────────
# TIMED WARN EXPIRY
# ──────────────────────────────────────────────

async def check_expired_warnings(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: expire old warnings based on warn_expire_hours setting."""
    db = await get_db()
    now = int(time.time())
    rows = await db.fetch(
        "SELECT chat_id, warn_expire_hours FROM chats WHERE warn_expire_hours > 0",
    )
    for row in rows:
        chat_id, hours = row["chat_id"], row["warn_expire_hours"]
        cutoff = now - (hours * 3600)
        await db.execute(
            "DELETE FROM warnings WHERE chat_id = $1 AND issued_at < $2",
            chat_id, cutoff,
        )


# ──────────────────────────────────────────────
# PURGE / CLEAN MESSAGES
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete N messages. Usage: /purge <count> or reply to a message to delete up to it."""
    if not update.message:
        return

    chat_id = update.effective_chat.id
    count = 0

    if context.args and context.args[0].isdigit():
        count = min(int(context.args[0]), 200)
    elif update.message.reply_to_message:
        count = update.message.message_id - update.message.reply_to_message.message_id
        count = min(max(count, 1), 200)

    if count < 1:
        await update.message.reply_text("Usage: /purge <count> or reply to a message with /purge")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"Yes, delete {count} messages", callback_data=f"pu:confirm:{chat_id}:{count}:{update.message.message_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"pu:cancel:{chat_id}"),
        ]
    ])
    await update.message.reply_text(
        f"Are you sure you want to delete {count} messages?",
        reply_markup=keyboard,
    )


async def purge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle purge confirmation."""
    query = update.callback_query
    await query.answer()

    data = query.data[3:]
    parts = data.split(":")

    if parts[0] == "cancel":
        await query.edit_message_text("Purge cancelled.")
        return

    if parts[0] == "confirm":
        chat_id = int(parts[1])
        count = int(parts[2])
        base_msg_id = int(parts[3])

        from utils.helpers import has_permission
        if not await has_permission(chat_id, query.from_user.id, Role.ADMIN, context.bot):
            await query.answer("Admin only.", show_alert=True)
            return

        await query.edit_message_text(f"Deleting {count} messages...")

        deleted = 0
        failed = 0
        for msg_id in range(base_msg_id, base_msg_id - count, -1):
            try:
                await context.bot.delete_message(chat_id, msg_id)
                deleted += 1
            except Exception:
                failed += 1

        try:
            await query.edit_message_text(f"Purge complete: {deleted} deleted, {failed} failed.")
        except Exception:
            pass

        from services.settings_service import log_action
        await log_action(chat_id, "purge", query.from_user.id, details=f"{deleted} messages deleted")


# ──────────────────────────────────────────────
# USER INFO CARD
# ──────────────────────────────────────────────

async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed info about a user."""
    if not update.message:
        return

    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    db = await get_db()

    user_rows = await db.fetch("SELECT * FROM users WHERE user_id = $1", target_id)
    if not user_rows:
        await update.message.reply_text("User not found in database.")
        return

    user = dict(user_rows[0])

    from utils.helpers import get_user_role
    role = await get_user_role(chat_id, target_id, context.bot)

    warn_count = await db.fetchval(
        "SELECT COUNT(*) FROM warnings WHERE chat_id = $1 AND user_id = $2", chat_id, target_id,
    ) or 0

    member_rows = await db.fetch(
        "SELECT * FROM chat_members WHERE chat_id = $1 AND user_id = $2", chat_id, target_id,
    )
    member = dict(member_rows[0]) if member_rows else {}

    note_count = await db.fetchval(
        "SELECT COUNT(*) FROM user_notes WHERE chat_id = $1 AND user_id = $2", chat_id, target_id,
    ) or 0

    username = f"@{user['username']}" if user['username'] else "None"
    first_seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(user['first_seen'])) if user['first_seen'] else "Unknown"
    last_seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(user['last_seen'])) if user['last_seen'] else "Unknown"
    joined = time.strftime("%Y-%m-%d %H:%M", time.localtime(member['joined_at'])) if member.get('joined_at') else "Unknown"

    text = (
        f"<b>User Info</b>\n\n"
        f"<b>Name:</b> {user['first_name']} {user['last_name']}\n"
        f"<b>Username:</b> {username}\n"
        f"<b>User ID:</b> <code>{target_id}</code>\n"
        f"<b>Role:</b> {role.value}\n"
        f"<b>Warnings:</b> {warn_count}\n"
        f"<b>Notes:</b> {note_count}\n"
        f"<b>First seen:</b> {first_seen}\n"
        f"<b>Last seen:</b> {last_seen}\n"
        f"<b>Joined group:</b> {joined}\n"
    )

    if member.get('is_muted'):
        if member.get('muted_until', 0) > 0:
            text += f"<b>Muted until:</b> {time.strftime('%Y-%m-%d %H:%M', time.localtime(member['muted_until']))}\n"
        else:
            text += "<b>Status:</b> Muted (permanent)\n"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Warn", callback_data=f"wa:warn_quick:{chat_id}:{target_id}"),
            InlineKeyboardButton("Mute", callback_data=f"wa:mute:{chat_id}:{target_id}"),
            InlineKeyboardButton("Ban", callback_data=f"wa:ban:{chat_id}:{target_id}"),
        ],
        [
            InlineKeyboardButton("View Notes", callback_data=f"ui:notes:{chat_id}:{target_id}"),
            InlineKeyboardButton("Add Note", callback_data=f"ui:addnote:{chat_id}:{target_id}"),
        ],
    ])

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def userinfo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user info action buttons."""
    query = update.callback_query
    await query.answer()

    data = query.data[3:]
    parts = data.split(":")
    action = parts[0]
    chat_id = int(parts[1])
    target_id = int(parts[2])

    from utils.helpers import has_permission
    if not await has_permission(chat_id, query.from_user.id, Role.ADMIN, context.bot):
        await query.answer("Admin only.", show_alert=True)
        return

    if action == "notes":
        notes = await _get_user_notes(chat_id, target_id)
        if not notes:
            await query.message.reply_text("No notes for this user.")
            return
        text = "<b>User Notes:</b>\n\n"
        for note in notes[:10]:
            text += f"#{note['id']} - {note['note']}\n"
        await query.message.reply_text(text, parse_mode="HTML")

    elif action == "addnote":
        context.user_data["adding_note_for"] = {"chat_id": chat_id, "user_id": target_id}
        await query.message.reply_text("Send the note text:")


async def handle_note_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input for adding user notes. Returns True if handled."""
    if not update.message or not update.message.text:
        return False

    note_data = context.user_data.get("adding_note_for")
    if not note_data:
        return False

    from services.settings_service import add_user_note
    await add_user_note(
        note_data["chat_id"],
        note_data["user_id"],
        update.message.text,
        update.effective_user.id,
    )
    del context.user_data["adding_note_for"]
    await update.message.reply_text("Note added.")
    return True


async def _get_user_notes(chat_id: int, user_id: int) -> list[dict]:
    from services.settings_service import get_user_notes
    return await get_user_notes(chat_id, user_id)


# ──────────────────────────────────────────────
# UNOTE / NOTES FOR USERS
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def unote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a note about a user. Reply to their message with /unote <text>"""
    if not update.message:
        return

    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        await update.message.reply_text("Reply to a user's message with /unote <note>")
        return

    target_id = update.message.reply_to_message.from_user.id
    if not context.args:
        await update.message.reply_text("Usage: Reply to a user with /unote <note text>")
        return

    note_text = " ".join(context.args)
    chat_id = update.effective_chat.id

    from services.settings_service import add_user_note
    note_id = await add_user_note(chat_id, target_id, note_text, update.effective_user.id)
    await update.message.reply_text(f"Note #{note_id} added for user {target_id}.")


@require_role(Role.ADMIN)
async def unotes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List notes for a user. Reply to their message or specify @username."""
    if not update.message:
        return

    target_id = await _resolve_target_user(update, context)
    if not target_id:
        return

    chat_id = update.effective_chat.id
    notes = await _get_user_notes(chat_id, target_id)

    if not notes:
        await update.message.reply_text("No notes for this user.")
        return

    text = f"<b>Notes for user {target_id}:</b>\n\n"
    for note in notes:
        text += f"#{note['id']} - {note['note']}\n"

    await update.message.reply_text(text, parse_mode="HTML")


@require_role(Role.ADMIN)
async def delunote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a user note. Usage: /delunote <note_id>"""
    if not update.message:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /delunote <note_id>")
        return

    note_id = int(context.args[0])
    chat_id = update.effective_chat.id

    from services.settings_service import delete_user_note
    if await delete_user_note(note_id, chat_id):
        await update.message.reply_text(f"Note #{note_id} deleted.")
    else:
        await update.message.reply_text(f"Note #{note_id} not found.")


# ──────────────────────────────────────────────
# BLACKLIST (persistent ban list)
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def blacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the permanent ban list."""
    if not update.message:
        return

    chat_id = update.effective_chat.id
    db = await get_db()

    rows = await db.fetch(
        "SELECT DISTINCT target_id FROM action_log WHERE chat_id = $1 AND action_type = 'ban' ORDER BY created_at DESC LIMIT 50",
        chat_id,
    )

    if not rows:
        await update.message.reply_text("No bans recorded.")
        return

    text = "<b>Ban History:</b>\n\n"
    for row in rows:
        uid = row["target_id"]
        user_rows = await db.fetch("SELECT first_name, username FROM users WHERE user_id = $1", uid)
        if user_rows:
            name = user_rows[0]["first_name"] or "Unknown"
            username = f"@{user_rows[0]['username']}" if user_rows[0]["username"] else ""
            text += f"• <code>{uid}</code> - {name} {username}\n"
        else:
            text += f"• <code>{uid}</code>\n"

    await update.message.reply_text(text, parse_mode="HTML")
