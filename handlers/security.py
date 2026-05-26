from __future__ import annotations

import re
import time
import random
import string
import logging
import json
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import ContextTypes

from database.connection import get_db
from middleware.permissions import require_role
from services.settings_service import (
    get_chat_settings, update_chat_setting, create_captcha_session,
    verify_captcha, get_expired_captcha_sessions, clear_captcha_session,
    log_action, is_fed_banned, get_fed_member_chats, log_action,
)
from utils.constants import Role, CB_PREFIX_CAPTCHA, CB_PREFIX_GLOBAL_LOCK, CB_PREFIX_RAID
from utils.helpers import upsert_user, ensure_chat_member

logger = logging.getLogger(__name__)

# Anti-raid tracker: {chat_id: [join_timestamp, ...]}
_raid_tracker: dict[int, list[float]] = defaultdict(list)

# Non-Latin character ranges
_ARABIC_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]')
_CYRILLIC_RE = re.compile(r'[\u0400-\u04FF\u0500-\u052F]')
_CJK_RE = re.compile(r'[\u4E00-\u9FFF\u3400-\u4DBF]')
_NON_LATIN_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\u0400-\u04FF\u0500-\u052F\u4E00-\u9FFF\u3400-\u4DBF\uFB50-\uFDFF\uFE70-\uFEFF]')


# ──────────────────────────────────────────────
# CAPTCHA SYSTEM
# ──────────────────────────────────────────────

def _generate_math_captcha() -> tuple[str, str]:
    """Generate a math captcha. Returns (question, answer)."""
    a, b = random.randint(1, 20), random.randint(1, 20)
    op = random.choice(["+", "-", "*"])
    if op == "+":
        return f"{a} + {b} = ?", str(a + b)
    elif op == "-":
        if a < b:
            a, b = b, a
        return f"{a} - {b} = ?", str(a - b)
    else:
        return f"{a} * {b} = ?", str(a * b)


def _generate_text_captcha() -> tuple[str, str]:
    """Generate a text captcha. Returns (question, answer)."""
    words = ["telegram", "verify", "human", "robot", "group", "member", "admin", "welcome"]
    word = random.choice(words)
    # Scramble the word
    scrambled = list(word)
    random.shuffle(scrambled)
    return f"Unscramble: {''.join(scrambled)}", word


def _generate_button_captcha() -> tuple[str, str]:
    """Generate a button captcha. Returns (question, correct_button_text)."""
    correct = ''.join(random.choices(string.ascii_lowercase, k=6))
    return "Click the correct button to verify:", correct


async def handle_captcha_join(update: Update, context: ContextTypes.DEFAULT_TYPE, member, chat_id: int) -> bool:
    """Handle captcha for a new member. Returns True if captcha was issued."""
    settings = await get_chat_settings(chat_id)
    if not settings.captcha_enabled:
        return False

    # Mute user immediately
    try:
        await context.bot.restrict_chat_member(
            chat_id, member.id,
            permissions=ChatPermissions(can_send_messages=False),
        )
    except Exception as e:
        logger.warning(f"Failed to mute for captcha: {e}")
        return False

    if settings.captcha_type == "math":
        question, answer = _generate_math_captcha()
        keyboard = None
        text = f"Welcome {member.first_name}! Solve this to verify:\n\n<b>{question}</b>\n\nReply with the answer within {settings.captcha_timeout} seconds."

    elif settings.captcha_type == "text":
        question, answer = _generate_text_captcha()
        keyboard = None
        text = f"Welcome {member.first_name}! Unscramble this word:\n\n<b>{question}</b>\n\nReply with the answer within {settings.captcha_timeout} seconds."

    else:  # button
        question, answer = _generate_button_captcha()
        # Generate 4 fake buttons + 1 correct
        buttons = [answer]
        for _ in range(4):
            fake = ''.join(random.choices(string.ascii_lowercase, k=6))
            buttons.append(fake)
        random.shuffle(buttons)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(b, callback_data=f"{CB_PREFIX_CAPTCHA}verify:{chat_id}:{member.id}:{b}")]
            for b in buttons
        ])
        text = f"Welcome {member.first_name}! Click the button: <b>{answer}</b>\n\nYou have {settings.captcha_timeout} seconds."

    # Send captcha message and store its ID for later deletion
    try:
        sent = await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        captcha_msg_id = sent.message_id if sent else 0
    except Exception:
        captcha_msg_id = 0

    await create_captcha_session(chat_id, member.id, answer, settings.captcha_timeout, captcha_msg_id)

    # Schedule auto-delete of captcha message
    if captcha_msg_id and context.job_queue:
        context.job_queue.run_once(
            _delete_captcha_prompt,
            when=settings.captcha_timeout,
            data={"chat_id": chat_id, "message_id": captcha_msg_id},
            name=f"captcha_del_{chat_id}_{captcha_msg_id}",
        )

    return True


async def _delete_captcha_prompt(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: delete a captcha prompt message after timeout."""
    data = context.job.data
    try:
        await context.bot.delete_message(data["chat_id"], data["message_id"])
    except Exception:
        pass


async def _unmute_after_captcha(context, chat_id: int, user_id: int) -> bool:
    """Unmute a user after captcha verification. Returns True on success."""
    try:
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
            ),
        )
        logger.info(f"Unmuted user {user_id} in chat {chat_id} after captcha")
        return True
    except Exception as e:
        logger.error(f"Failed to unmute user {user_id} in chat {chat_id}: {e}")
        return False


async def _send_welcome_after_captcha(update_or_query, context, chat_id: int, user_id: int) -> None:
    """Send the welcome message after captcha is cleared."""
    try:
        settings = await get_chat_settings(chat_id)
        if not settings.welcome_enabled:
            return

        chat_info = await context.bot.get_chat(chat_id)
        chat_title = chat_info.title or ""
        bot_username = context.bot.username or "bot"

        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            first_name = member.user.first_name or "User"
        except Exception:
            first_name = "User"

        mention = f'<a href="tg://user?id={user_id}">{first_name}</a>'
        try:
            member_count = await context.bot.get_chat_member_count(chat_id)
        except Exception:
            member_count = "?"

        text = settings.welcome_message.format(
            user_mention=mention,
            user_name=first_name,
            user_first_name=first_name,
            chat_name=chat_title,
            member_count=member_count,
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Message", url=f"https://t.me/{bot_username}?start=chat_{chat_id}"),
                InlineKeyboardButton("Rules", callback_data=f"uj:rules:{chat_id}"),
            ],
        ])
        await context.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send welcome after captcha: {e}")


async def captcha_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle captcha button press."""
    query = update.callback_query
    data = query.data[len(CB_PREFIX_CAPTCHA):]
    parts = data.split(":")

    if parts[0] == "verify":
        chat_id = int(parts[1])
        user_id = int(parts[2])
        clicked = parts[3]

        if query.from_user.id != user_id:
            await query.answer("This button is not for you.", show_alert=True)
            return

        if await verify_captcha(chat_id, user_id, clicked):
            await query.answer("Verified! Welcome!", show_alert=True)
            await _unmute_after_captcha(context, chat_id, user_id)
            # Cancel scheduled auto-delete and delete the captcha prompt now
            _cancel_captcha_delete_job(context, chat_id, query.message.message_id)
            await clear_captcha_session(chat_id, user_id)
            try:
                await context.bot.delete_message(chat_id, query.message.message_id)
            except Exception:
                try:
                    await query.edit_message_text(f"{query.from_user.first_name} verified!")
                except Exception:
                    pass
            # Send welcome message now that captcha is cleared
            await _send_welcome_after_captcha(query, context, chat_id, user_id)
        else:
            await query.answer("Wrong! Try again.", show_alert=True)


async def handle_captcha_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if a text message is a captcha answer. Returns True if handled."""
    if not update.message or not update.message.text:
        return False
    if update.effective_chat.type == "private":
        return False

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    settings = await get_chat_settings(chat_id)

    if not settings.captcha_enabled:
        return False

    answer = update.message.text.strip()
    if await verify_captcha(chat_id, user_id, answer):
        # Get captcha message ID from session to delete the prompt
        from database.connection import get_db as _get_db
        _db = await _get_db()
        _rows = await _db.fetch(
            "SELECT message_id FROM captcha_sessions WHERE chat_id = $1 AND user_id = $2 AND solved = 1",
            chat_id, user_id,
        )
        captcha_msg_id = _rows[0]["message_id"] if _rows else 0

        # Cancel scheduled auto-delete
        if captcha_msg_id:
            _cancel_captcha_delete_job(context, chat_id, captcha_msg_id)

        await clear_captcha_session(chat_id, user_id)
        await _unmute_after_captcha(context, chat_id, user_id)

        # Delete captcha prompt and user's answer message
        if captcha_msg_id:
            try:
                await context.bot.delete_message(chat_id, captcha_msg_id)
            except Exception:
                pass
        try:
            await update.message.delete()
        except Exception:
            pass

        # Send welcome message now that captcha is cleared
        await _send_welcome_after_captcha(update, context, chat_id, user_id)
        return True

    return False


async def check_expired_captcha(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: check for expired captcha sessions and take action."""
    expired = await get_expired_captcha_sessions()
    for session in expired:
        chat_id = session["chat_id"]
        user_id = session["user_id"]
        captcha_msg_id = session.get("message_id", 0)
        settings = await get_chat_settings(chat_id)

        action = settings.captcha_action
        try:
            if action == "ban":
                await context.bot.ban_chat_member(chat_id, user_id)
            elif action == "kick":
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.unban_chat_member(chat_id, user_id)
            else:
                # Keep muted
                pass
        except Exception:
            pass

        # Delete the captcha prompt message
        if captcha_msg_id:
            try:
                await context.bot.delete_message(chat_id, captcha_msg_id)
            except Exception:
                pass

        await clear_captcha_session(chat_id, user_id)
        try:
            await context.bot.send_message(
                chat_id,
                f"User {user_id} failed captcha verification ({action}ed)."
            )
        except Exception:
            pass


# ──────────────────────────────────────────────
# HELPER: Cancel scheduled captcha delete job
# ──────────────────────────────────────────────

def _cancel_captcha_delete_job(context, chat_id: int, message_id: int) -> None:
    """Cancel the scheduled auto-delete job for a captcha prompt."""
    if not context.job_queue:
        return
    job_name = f"captcha_del_{chat_id}_{message_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()


# ──────────────────────────────────────────────
# SCRIPT FILTER
# ──────────────────────────────────────────────

async def check_script_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check for non-Latin script. Returns True if message was acted upon."""
    if not update.message or not update.message.text:
        return False

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    if not settings.script_filter_enabled:
        return False

    from utils.helpers import has_permission
    if await has_permission(chat_id, update.effective_user.id, Role.ADMIN, context.bot):
        return False

    text = update.message.text
    non_latin_count = len(_NON_LATIN_RE.findall(text))
    total_chars = len(text.replace(" ", ""))

    if total_chars > 0 and (non_latin_count / total_chars) > 0.3:
        action = settings.script_filter_action
        try:
            await update.message.delete()
            if action == "mute":
                await context.bot.restrict_chat_member(
                    chat_id, update.effective_user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                )
                await update.message.reply_text(
                    f"{update.effective_user.first_name}, non-Latin scripts are not allowed. You have been muted."
                )
            elif action == "warn":
                from services import warning_service
                count = await warning_service.add_warning(chat_id, update.effective_user.id, context.bot.id, "Non-Latin script")
                await update.message.reply_text(
                    f"{update.effective_user.first_name}, non-Latin scripts are not allowed. Warning ({count}/{settings.warn_threshold})."
                )
            else:
                await update.message.reply_text(
                    f"{update.effective_user.first_name}, non-Latin scripts are not allowed."
                )
        except Exception:
            pass
        return True

    return False


# ──────────────────────────────────────────────
# ANTI-RAID
# ──────────────────────────────────────────────

async def check_raid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check for raid conditions on member join. Returns True if raid detected."""
    if not update.message or not update.message.new_chat_members:
        return False

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    if not settings.anti_raid_enabled:
        return False

    now = time.time()
    _raid_tracker[chat_id].append(now)

    # Remove old entries outside the window
    cutoff = now - settings.raid_window
    _raid_tracker[chat_id] = [t for t in _raid_tracker[chat_id] if t > cutoff]

    if len(_raid_tracker[chat_id]) >= settings.raid_threshold:
        action = settings.raid_action
        _raid_tracker[chat_id] = []  # Reset

        if action == "lock":
            await update_chat_setting(chat_id, "global_lock", 1)
            try:
                await context.bot.set_chat_permissions(
                    chat_id,
                    ChatPermissions(can_send_messages=False),
                )
                await update.message.reply_text(
                    f"RAID DETECTED! Group locked automatically. {len(update.message.new_chat_members)} members joined in {settings.raid_window}s.\n"
                    "An admin must /unlock to restore."
                )
            except Exception:
                pass

        elif action == "kick":
            for member in update.message.new_chat_members:
                try:
                    await context.bot.ban_chat_member(chat_id, member.id)
                    await context.bot.unban_chat_member(chat_id, member.id)
                except Exception:
                    pass
            try:
                await update.message.reply_text(
                    f"RAID DETECTED! {len(update.message.new_chat_members)} new members kicked."
                )
            except Exception:
                pass

        elif action == "ban":
            for member in update.message.new_chat_members:
                try:
                    await context.bot.ban_chat_member(chat_id, member.id)
                except Exception:
                    pass
            try:
                await update.message.reply_text(
                    f"RAID DETECTED! {len(update.message.new_chat_members)} new members banned."
                )
            except Exception:
                pass

        return True

    return False


# ──────────────────────────────────────────────
# GLOBAL LOCK
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def global_lock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Emergency global lock — mutes everyone."""
    if not update.message:
        return
    chat_id = update.effective_chat.id

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Lock Everything", callback_data=f"{CB_PREFIX_GLOBAL_LOCK}lock:{chat_id}"),
            InlineKeyboardButton("Lock Text Only", callback_data=f"{CB_PREFIX_GLOBAL_LOCK}text:{chat_id}"),
        ],
        [
            InlineKeyboardButton("Cancel", callback_data=f"{CB_PREFIX_GLOBAL_LOCK}cancel:{chat_id}"),
        ]
    ])
    await update.message.reply_text(
        "Choose lock type:",
        reply_markup=keyboard,
    )


async def global_lock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle global lock buttons."""
    query = update.callback_query
    await query.answer()

    from utils.helpers import has_permission
    if not await has_permission(query.message.chat_id, query.from_user.id, Role.ADMIN, context.bot):
        await query.answer("Admin only.", show_alert=True)
        return

    data = query.data[len(CB_PREFIX_GLOBAL_LOCK):]
    parts = data.split(":")
    action = parts[0]
    chat_id = int(parts[1])

    if action == "cancel":
        await query.edit_message_text("Cancelled.")
        return

    await update_chat_setting(chat_id, "global_lock", 1)

    if action == "lock":
        await context.bot.set_chat_permissions(
            chat_id,
            ChatPermissions(can_send_messages=False,
                            can_send_polls=False, can_send_other_messages=False),
        )
        await query.edit_message_text("GROUP LOCKED. Only admins can speak. Use /unlock to restore.")
    elif action == "text":
        await context.bot.set_chat_permissions(
            chat_id,
            ChatPermissions(can_send_messages=False,
                            can_send_polls=True, can_send_other_messages=True),
        )
        await query.edit_message_text("TEXT LOCKED. Only admins can send messages. Use /unlock to restore.")


@require_role(Role.ADMIN)
async def global_unlock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove global lock."""
    if not update.message:
        return
    chat_id = update.effective_chat.id
    await update_chat_setting(chat_id, "global_lock", 0)
    await context.bot.set_chat_permissions(
        chat_id,
        ChatPermissions(
            can_send_messages=True,
            can_send_polls=True, can_send_other_messages=True,
            can_add_web_page_previews=True, can_change_info=True,
            can_invite_users=True, can_pin_messages=True,
            can_send_audios=True, can_send_documents=True,
            can_send_photos=True, can_send_videos=True,
            can_send_video_notes=True, can_send_voice_notes=True,
        ),
    )
    await update.message.reply_text("Group unlocked. All members can speak again.")
    await log_action(chat_id, "global_unlock", update.effective_user.id)


# ──────────────────────────────────────────────
# FEDERATION SYSTEM
# ──────────────────────────────────────────────

@require_role(Role.OWNER)
async def fed_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a federation."""
    if not update.message or not context.args:
        await update.message.reply_text("Usage: /fedcreate <name>")
        return

    name = " ".join(context.args)
    from services.settings_service import create_federation, join_federation, get_federation_by_name
    existing = await get_federation_by_name(name)
    if existing:
        await update.message.reply_text(f"Federation '{name}' already exists.")
        return

    fed_id = await create_federation(name, update.effective_user.id)
    await join_federation(fed_id, update.effective_chat.id)
    await update.message.reply_text(f"Federation '{name}' created (ID: {fed_id}). This group has joined it.")


@require_role(Role.ADMIN)
async def fed_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Join a federation by ID."""
    if not update.message or not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /fedjoin <federation_id>")
        return

    fed_id = int(context.args[0])
    from services.settings_service import get_federation_by_id, join_federation
    fed = await get_federation_by_id(fed_id)
    if not fed:
        await update.message.reply_text("Federation not found.")
        return

    await join_federation(fed_id, update.effective_chat.id)
    await update.message.reply_text(f"Joined federation '{fed['name']}'.")


@require_role(Role.ADMIN)
async def fed_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Leave the current federation."""
    if not update.message:
        return
    from services.settings_service import leave_federation
    await leave_federation(update.effective_chat.id)
    await update.message.reply_text("Left the federation.")


@require_role(Role.ADMIN)
async def fed_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ban a user from the federation."""
    if not update.message:
        return

    target_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args and context.args[0].lstrip("@").isdigit():
        target_id = int(context.args[0].lstrip("@"))

    if not target_id:
        await update.message.reply_text("Reply to a user or provide a user ID.")
        return

    settings = await get_chat_settings(update.effective_chat.id)
    if not settings.federation_id:
        await update.message.reply_text("This group is not in a federation.")
        return

    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    from services.settings_service import fed_ban_user, get_fed_member_chats, get_federation_by_id
    await fed_ban_user(settings.federation_id, target_id, reason, update.effective_user.id)

    fed = await get_federation_by_id(settings.federation_id)
    # Ban from all member chats
    member_chats = await get_fed_member_chats(settings.federation_id)
    banned_count = 0
    for cid in member_chats:
        try:
            await context.bot.ban_chat_member(cid, target_id)
            banned_count += 1
        except Exception:
            pass

    await update.message.reply_text(
        f"User {target_id} banned from federation '{fed['name'] if fed else '?'}' ({banned_count} groups)."
    )


@require_role(Role.ADMIN)
async def fed_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unban a user from the federation."""
    if not update.message:
        return

    target_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args and context.args[0].lstrip("@").isdigit():
        target_id = int(context.args[0].lstrip("@"))

    if not target_id:
        await update.message.reply_text("Reply to a user or provide a user ID.")
        return

    settings = await get_chat_settings(update.effective_chat.id)
    if not settings.federation_id:
        await update.message.reply_text("This group is not in a federation.")
        return

    from services.settings_service import fed_unban_user, get_fed_member_chats, get_federation_by_id
    success = await fed_unban_user(settings.federation_id, target_id)
    if not success:
        await update.message.reply_text("User is not federation-banned.")
        return

    fed = await get_federation_by_id(settings.federation_id)
    member_chats = await get_fed_member_chats(settings.federation_id)
    unbanned_count = 0
    for cid in member_chats:
        try:
            await context.bot.unban_chat_member(cid, target_id, only_if_banned=True)
            unbanned_count += 1
        except Exception:
            pass

    await update.message.reply_text(
        f"User {target_id} unbanned from federation '{fed['name'] if fed else '?'}' ({unbanned_count} groups)."
    )


@require_role(Role.ADMIN)
async def fed_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show federation info."""
    if not update.message:
        return
    settings = await get_chat_settings(update.effective_chat.id)
    if not settings.federation_id:
        await update.message.reply_text("This group is not in a federation.")
        return

    from services.settings_service import get_federation_by_id, get_fed_member_chats
    from database.connection import get_db
    fed = await get_federation_by_id(settings.federation_id)
    if not fed:
        await update.message.reply_text("Federation not found.")
        return

    member_chats = await get_fed_member_chats(settings.federation_id)
    db = await get_db()
    ban_rows = await db.fetch(
        "SELECT COUNT(*) FROM federation_bans WHERE federation_id = $1",
        settings.federation_id,
    )
    ban_count = ban_rows[0]["count"] if ban_rows else 0

    await update.message.reply_text(
        f"**Federation: {fed['name']}**\n\n"
        f"ID: {fed['id']}\n"
        f"Owner: {fed['owner_id']}\n"
        f"Groups: {len(member_chats)}\n"
        f"Fed Bans: {ban_count}\n",
        parse_mode="Markdown",
    )


async def check_fed_ban_on_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if a new member is federation-banned. Returns True if banned."""
    if not update.message or not update.message.new_chat_members:
        return False

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    if not settings.federation_id:
        return False

    for member in update.message.new_chat_members:
        ban = await is_fed_banned(settings.federation_id, member.id)
        if ban:
            try:
                await context.bot.ban_chat_member(chat_id, member.id)
                await update.message.reply_text(
                    f"{member.first_name} is federation-banned: {ban.get('reason', 'No reason')}"
                )
            except Exception:
                pass
            return True

    return False


# ──────────────────────────────────────────────
# SETTINGS FOR SECURITY FEATURES
# ──────────────────────────────────────────────

@require_role(Role.ADMIN)
async def captcha_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle and configure captcha."""
    if not update.message:
        return
    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)

    if not context.args:
        status = "ON" if settings.captcha_enabled else "OFF"
        await update.message.reply_text(
            f"Captcha: **{status}**\n"
            f"Type: {settings.captcha_type}\n"
            f"Timeout: {settings.captcha_timeout}s\n"
            f"Fail action: {settings.captcha_action}\n\n"
            "Usage:\n"
            "/captcha on|off\n"
            "/captcha type button|math|text\n"
            "/captcha timeout <seconds>\n"
            "/captcha action kick|ban|mute",
            parse_mode="Markdown",
        )
        return

    arg = context.args[0].lower()
    if arg in ("on", "off"):
        await update_chat_setting(chat_id, "captcha_enabled", 1 if arg == "on" else 0)
        await update.message.reply_text(f"Captcha {'enabled' if arg == 'on' else 'disabled'}.")
    elif arg == "type" and len(context.args) > 1:
        t = context.args[1].lower()
        if t in ("button", "math", "text"):
            await update_chat_setting(chat_id, "captcha_type", t)
            await update.message.reply_text(f"Captcha type set to {t}.")
    elif arg == "timeout" and len(context.args) > 1 and context.args[1].isdigit():
        await update_chat_setting(chat_id, "captcha_timeout", int(context.args[1]))
        await update.message.reply_text(f"Captcha timeout set to {context.args[1]}s.")
    elif arg == "action" and len(context.args) > 1:
        a = context.args[1].lower()
        if a in ("kick", "ban", "mute"):
            await update_chat_setting(chat_id, "captcha_action", a)
            await update.message.reply_text(f"Captcha fail action set to {a}.")


@require_role(Role.ADMIN)
async def scriptfilter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle script filter."""
    if not update.message:
        return
    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)

    if not context.args:
        status = "ON" if settings.script_filter_enabled else "OFF"
        await update.message.reply_text(
            f"Script Filter: **{status}**\n"
            f"Action: {settings.script_filter_action}\n\n"
            "Usage:\n"
            "/scriptfilter on|off\n"
            "/scriptfilter action mute|warn|delete",
            parse_mode="Markdown",
        )
        return

    arg = context.args[0].lower()
    if arg in ("on", "off"):
        await update_chat_setting(chat_id, "script_filter_enabled", 1 if arg == "on" else 0)
        await update.message.reply_text(f"Script filter {'enabled' if arg == 'on' else 'disabled'}.")
    elif arg == "action" and len(context.args) > 1:
        a = context.args[1].lower()
        if a in ("mute", "warn", "delete"):
            await update_chat_setting(chat_id, "script_filter_action", a)
            await update.message.reply_text(f"Script filter action set to {a}.")


@require_role(Role.ADMIN)
async def antiraid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle anti-raid."""
    if not update.message:
        return
    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)

    if not context.args:
        status = "ON" if settings.anti_raid_enabled else "OFF"
        await update.message.reply_text(
            f"Anti-Raid: **{status}**\n"
            f"Threshold: {settings.raid_threshold} joins/{settings.raid_window}s\n"
            f"Action: {settings.raid_action}\n\n"
            "Usage:\n"
            "/antiraid on|off\n"
            "/antiraid threshold <count>\n"
            "/antiraid window <seconds>\n"
            "/antiraid action lock|kick|ban",
            parse_mode="Markdown",
        )
        return

    arg = context.args[0].lower()
    if arg in ("on", "off"):
        await update_chat_setting(chat_id, "anti_raid_enabled", 1 if arg == "on" else 0)
        await update.message.reply_text(f"Anti-raid {'enabled' if arg == 'on' else 'disabled'}.")
    elif arg == "threshold" and len(context.args) > 1 and context.args[1].isdigit():
        await update_chat_setting(chat_id, "raid_threshold", int(context.args[1]))
        await update.message.reply_text(f"Raid threshold set to {context.args[1]} joins.")
    elif arg == "window" and len(context.args) > 1 and context.args[1].isdigit():
        await update_chat_setting(chat_id, "raid_window", int(context.args[1]))
        await update.message.reply_text(f"Raid window set to {context.args[1]}s.")
    elif arg == "action" and len(context.args) > 1:
        a = context.args[1].lower()
        if a in ("lock", "kick", "ban"):
            await update_chat_setting(chat_id, "raid_action", a)
            await update.message.reply_text(f"Raid action set to {a}.")


@require_role(Role.ADMIN)
async def nightmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle night mode."""
    if not update.message:
        return
    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)

    if not context.args:
        status = "ON" if settings.night_mode_enabled else "OFF"
        await update.message.reply_text(
            f"Night Mode: **{status}**\n"
            f"Start: {settings.night_start}\n"
            f"End: {settings.night_end}\n"
            f"Action: {settings.night_action}\n\n"
            "Usage:\n"
            "/nightmode on|off\n"
            "/nightmode start HH:MM\n"
            "/nightmode end HH:MM\n"
            "/nightmode action mute|lock",
            parse_mode="Markdown",
        )
        return

    arg = context.args[0].lower()
    if arg in ("on", "off"):
        await update_chat_setting(chat_id, "night_mode_enabled", 1 if arg == "on" else 0)
        await update.message.reply_text(f"Night mode {'enabled' if arg == 'on' else 'disabled'}.")
    elif arg == "start" and len(context.args) > 1:
        await update_chat_setting(chat_id, "night_start", context.args[1])
        await update.message.reply_text(f"Night mode start set to {context.args[1]}.")
    elif arg == "end" and len(context.args) > 1:
        await update_chat_setting(chat_id, "night_end", context.args[1])
        await update.message.reply_text(f"Night mode end set to {context.args[1]}.")
    elif arg == "action" and len(context.args) > 1:
        a = context.args[1].lower()
        if a in ("mute", "lock"):
            await update_chat_setting(chat_id, "night_action", a)
            await update.message.reply_text(f"Night mode action set to {a}.")


async def check_night_mode(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job: check if night mode should activate/deactivate."""
    from datetime import datetime
    db = await get_db()
    rows = await db.fetch(
        "SELECT chat_id, night_start, night_end, night_action FROM chats WHERE night_mode_enabled = 1"
    )

    now = datetime.now()
    current_time = now.strftime("%H:%M")

    for row in rows:
        chat_id, start, end, action = row["chat_id"], row["night_start"], row["night_end"], row["night_action"]
        is_night = False
        if start <= end:
            is_night = start <= current_time < end
        else:
            is_night = current_time >= start or current_time < end

        settings = await get_chat_settings(chat_id)
        was_locked = settings.global_lock

        if is_night and not was_locked:
            await update_chat_setting(chat_id, "global_lock", 1)
            try:
                if action == "mute":
                    await context.bot.set_chat_permissions(
                        chat_id, ChatPermissions(can_send_messages=False)
                    )
                    await context.bot.send_message(chat_id, "Night mode activated. Group locked until morning.")
                elif action == "lock":
                    await context.bot.set_chat_permissions(
                        chat_id, ChatPermissions(can_send_messages=False)
                    )
                    await context.bot.send_message(chat_id, "Night mode activated. Full lock until morning.")
            except Exception:
                pass

        elif not is_night and was_locked:
            # Only auto-unlock if it was night mode that locked it
            await update_chat_setting(chat_id, "global_lock", 0)
            try:
                await context.bot.set_chat_permissions(
                    chat_id,
                    ChatPermissions(
                        can_send_messages=True,
                        can_send_polls=True, can_send_other_messages=True,
                        can_add_web_page_previews=True, can_change_info=True,
                        can_invite_users=True, can_pin_messages=True,
                        can_send_audios=True, can_send_documents=True,
                        can_send_photos=True, can_send_videos=True,
                        can_send_video_notes=True, can_send_voice_notes=True,
                    ),
                )
                await context.bot.send_message(chat_id, "Night mode ended. Group unlocked.")
            except Exception:
                pass


@require_role(Role.ADMIN)
async def slowmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set slow mode."""
    if not update.message:
        return
    chat_id = update.effective_chat.id

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /slowmode <seconds>\n\n"
            "Options: 0 (off), 10, 30, 60, 300, 900, 3600"
        )
        return

    seconds = int(context.args[0])
    try:
        await context.bot.set_chat_slow_mode_delay(chat_id, seconds)
        await update_chat_setting(chat_id, "slow_mode_seconds", seconds)
        if seconds:
            await update.message.reply_text(f"Slow mode set to {seconds} seconds.")
        else:
            await update.message.reply_text("Slow mode disabled.")
    except Exception as e:
        await update.message.reply_text(f"Failed to set slow mode: {e}")


@require_role(Role.ADMIN)
async def logchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set log channel."""
    if not update.message:
        return
    chat_id = update.effective_chat.id

    if not context.args:
        settings = await get_chat_settings(chat_id)
        if settings.log_channel_id:
            await update.message.reply_text(f"Log channel: `{settings.log_channel_id}`\n\nTo remove: /logchannel off")
        else:
            await update.message.reply_text("No log channel set.\n\nUsage: /logchannel <channel_id>\nOr forward a message from the channel.")
        return

    if context.args[0].lower() == "off":
        await update_chat_setting(chat_id, "log_channel_id", 0)
        await update.message.reply_text("Log channel removed.")
        return

    if context.args[0].lstrip("-").isdigit():
        channel_id = int(context.args[0])
        await update_chat_setting(chat_id, "log_channel_id", channel_id)
        await update.message.reply_text(f"Log channel set to `{channel_id}`.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Invalid channel ID. Use: /logchannel <channel_id>")


async def send_log(chat_id: int, context, text: str) -> None:
    """Send a log message to the configured log channel."""
    from services.settings_service import get_chat_settings
    settings = await get_chat_settings(chat_id)
    if settings.log_channel_id:
        try:
            await context.bot.send_message(settings.log_channel_id, text, parse_mode="HTML")
        except Exception:
            pass
