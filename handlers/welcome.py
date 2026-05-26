from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import get_db
from services.settings_service import get_chat_settings
from utils.constants import CB_PREFIX_BOT_JOIN, CB_PREFIX_USER_JOIN
from utils.helpers import get_or_create_chat, upsert_user, ensure_chat_member

logger = logging.getLogger(__name__)

BOT_JOIN_TEXT = (
    "Thank you for adding me to <b>{chat_name}</b>!\n\n"
    "To get started:\n"
    "1. Make me an <b>Admin</b> (I need Delete Messages + Ban Users + Restrict Members permissions)\n"
    "2. <a href=\"https://t.me/{bot_username}?start=group_{chat_id}\">Start me in private chat</a> so I can send you notifications\n\n"
    "Use /help to see what I can do, or tap the buttons below!"
)


async def handle_member_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.new_chat_members:
        return

    db = await get_db()
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or ""

    await get_or_create_chat(db, chat_id, chat_title)
    settings = await get_chat_settings(chat_id)
    bot_username = context.bot.username or "bot"

    from handlers.security import check_raid, handle_captcha_join, check_fed_ban_on_join
    if await check_raid(update, context):
        return

    if await check_fed_ban_on_join(update, context):
        return

    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            adder = update.message.from_user
            if adder and not adder.is_bot:
                await upsert_user(db, adder)
                await ensure_chat_member(db, chat_id, adder.id, "owner")
                await db.execute(
                    "UPDATE chats SET owner_id = $1 WHERE chat_id = $2",
                    adder.id, chat_id,
                )

            try:
                admins = await context.bot.get_chat_administrators(chat_id)
                for admin_member in admins:
                    if admin_member.user.is_bot:
                        continue
                    await upsert_user(db, admin_member.user)
                    role = "owner" if admin_member.status == "creator" else "admin"
                    await ensure_chat_member(db, chat_id, admin_member.user.id, role)
            except Exception as e:
                logger.warning(f"Failed to sync admins on bot join: {e}")

            text = BOT_JOIN_TEXT.format(
                chat_name=chat_title,
                bot_username=bot_username,
                chat_id=chat_id,
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Start Private Chat", url=f"https://t.me/{bot_username}?start=group_{chat_id}")],
                [
                    InlineKeyboardButton("Settings", callback_data=f"{CB_PREFIX_BOT_JOIN}settings:{chat_id}"),
                    InlineKeyboardButton("Help", callback_data=f"{CB_PREFIX_BOT_JOIN}help:{chat_id}"),
                ],
            ])
            try:
                await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to send bot join message: {e}")
            continue

        await upsert_user(db, member)
        await ensure_chat_member(db, chat_id, member.id, "member")

        if await handle_captcha_join(update, context, member, chat_id):
            # Welcome will be sent after captcha is cleared
            continue

        if settings.welcome_enabled:
            mention = f'<a href="tg://user?id={member.id}">{member.first_name or "User"}</a>'
            try:
                member_count = await context.bot.get_chat_member_count(chat_id)
            except Exception:
                member_count = "?"

            text = settings.welcome_message.format(
                user_mention=mention,
                user_name=member.first_name or "User",
                user_first_name=member.first_name or "",
                chat_name=chat_title,
                member_count=member_count,
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Message", url=f"https://t.me/{bot_username}?start=chat_{chat_id}"),
                    InlineKeyboardButton("Rules", callback_data=f"{CB_PREFIX_USER_JOIN}rules:{chat_id}"),
                ],
            ])
            try:
                if settings.welcome_media and settings.welcome_media_type:
                    sent = await _send_welcome_media(update, context, text, settings, keyboard)
                else:
                    sent = await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

                if settings.welcome_delete_seconds > 0 and sent:
                    context.job_queue.run_once(
                        _delete_message_job,
                        when=settings.welcome_delete_seconds,
                        data={"chat_id": chat_id, "message_id": sent.message_id},
                        name=f"delwelcome_{chat_id}_{sent.message_id}",
                    )
            except Exception as e:
                logger.error(f"Failed to send welcome message: {e}")


async def _send_welcome_media(update, context, text, settings, keyboard):
    media_type = settings.welcome_media_type
    media_id = settings.welcome_media
    try:
        if media_type == "photo":
            return await update.message.reply_photo(photo=media_id, caption=text, reply_markup=keyboard, parse_mode="HTML")
        elif media_type == "video":
            return await update.message.reply_video(video=media_id, caption=text, reply_markup=keyboard, parse_mode="HTML")
        elif media_type == "gif":
            return await update.message.reply_animation(animation=media_id, caption=text, reply_markup=keyboard, parse_mode="HTML")
        else:
            return await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        return await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    try:
        await context.bot.delete_message(data["chat_id"], data["message_id"])
    except Exception:
        pass


async def handle_member_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.left_chat_member:
        return

    member = update.message.left_chat_member
    if member.id == context.bot.id:
        return

    settings = await get_chat_settings(update.effective_chat.id)

    if settings.purge_leave:
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    if settings.goodbye_enabled:
        text = settings.goodbye_message.format(
            user_name=member.first_name or "User",
            user_first_name=member.first_name or "",
            chat_name=update.effective_chat.title or "",
        )
        try:
            sent = await update.message.reply_text(text, parse_mode="HTML")
            if settings.goodbye_delete_seconds > 0 and sent:
                context.job_queue.run_once(
                    _delete_message_job,
                    when=settings.goodbye_delete_seconds,
                    data={"chat_id": update.effective_chat.id, "message_id": sent.message_id},
                    name=f"delgoodbye_{update.effective_chat.id}_{sent.message_id}",
                )
        except Exception as e:
            logger.error(f"Failed to send goodbye message: {e}")


async def purge_service_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)

    msg = update.message
    should_delete = False

    if settings.purge_join and msg.new_chat_members:
        should_delete = True
    elif settings.purge_leave and msg.left_chat_member:
        should_delete = True
    elif settings.purge_pin and msg.pinned_message:
        should_delete = True
    elif settings.purge_photo_change and msg.new_chat_photo:
        should_delete = True
    elif settings.purge_photo_change and msg.delete_chat_photo:
        should_delete = True

    if should_delete:
        try:
            await msg.delete()
        except Exception:
            pass


async def bot_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data[len(CB_PREFIX_BOT_JOIN):]
    parts = data.split(":", 1)
    action = parts[0]
    chat_id = int(parts[1]) if len(parts) > 1 else 0

    if action == "settings":
        from handlers.admin import _build_main_settings_keyboard
        settings = await get_chat_settings(chat_id)
        keyboard = _build_main_settings_keyboard(settings)
        await query.edit_message_text("**Chat Settings**\nTap a button to toggle or edit.", reply_markup=keyboard, parse_mode="Markdown")
    elif action == "help":
        await query.edit_message_text("Use /help in the group to see all available commands.\n\nOr send /help here in PM to see DM commands.")


async def user_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data[len(CB_PREFIX_USER_JOIN):]
    parts = data.split(":", 1)
    action = parts[0]
    chat_id = int(parts[1]) if len(parts) > 1 else query.message.chat_id

    if action == "rules":
        settings = await get_chat_settings(chat_id)
        if settings.rules_text:
            try:
                await query.message.reply_text(settings.rules_text, parse_mode="Markdown")
            except Exception:
                await query.message.reply_text(settings.rules_text)
        else:
            await query.message.reply_text("No rules have been set for this group.")
