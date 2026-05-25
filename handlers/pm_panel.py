from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import get_db
from utils.constants import CB_PREFIX_PM_GROUP, CB_PREFIX_PM_SETTINGS, Role
from utils.helpers import get_user_role
from services.settings_service import get_chat_settings, update_chat_setting

logger = logging.getLogger(__name__)


async def mygroups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all groups where the user is admin/owner. DM only."""
    if not update.message or not update.effective_user:
        return
    await show_my_groups(update, context)


async def show_my_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's manageable groups."""
    user_id = update.effective_user.id if update.effective_user else (
        update.callback_query.from_user.id if update.callback_query else 0
    )
    if not user_id:
        return

    db = await get_db()
    # Find groups where user has explicit role, OR is set as owner in chats table
    rows = await db.execute_fetchall(
        "SELECT DISTINCT chat_id, title, role FROM ("
        "  SELECT cm.chat_id, c.title, cm.role FROM chat_members cm "
        "  JOIN chats c ON cm.chat_id = c.chat_id "
        "  WHERE cm.user_id = ? AND cm.role IN ('owner', 'admin')"
        "  UNION "
        "  SELECT c.chat_id, c.title, 'owner' as role FROM chats c "
        "  WHERE c.owner_id = ?"
        ")",
        (user_id, user_id),
    )

    # Also check Telegram API for each known chat if user has no entries yet
    if not rows:
        all_chats = await db.execute_fetchall("SELECT chat_id, title FROM chats")
        for chat_row in all_chats:
            cid = chat_row[0]
            try:
                tg_member = await context.bot.get_chat_member(cid, user_id)
                if tg_member.status in ("creator", "administrator"):
                    role = "owner" if tg_member.status == "creator" else "admin"
                    from utils.helpers import upsert_user, ensure_chat_member
                    await upsert_user(db, update.effective_user or update.callback_query.from_user)
                    await ensure_chat_member(db, cid, user_id, role)
                    rows.append((cid, chat_row[1], role))
            except Exception:
                pass

    # Refresh group titles from Telegram API for any "Unknown Group" entries
    fixed_rows = []
    for row in rows:
        cid, title, role = row[0], row[1] or "", row[2]
        if not title or title == "Unknown Group":
            try:
                chat_info = await context.bot.get_chat(cid)
                title = chat_info.title or f"Group {cid}"
                await db.execute("UPDATE chats SET title = ? WHERE chat_id = ?", (title, cid))
                await db.commit()
            except Exception:
                title = f"Group {cid}"
        fixed_rows.append((cid, title, role))
    rows = fixed_rows

    if not rows:
        text = "You don't have admin/owner access to any groups with this bot.\n\nAdd me to a group first!"
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    buttons = []
    for row in rows:
        cid, title, role = row[0], row[1] or "Unknown Group", row[2]
        role_icon = "\U0001f451" if role == "owner" else "\U0001f6e1\ufe0f"
        buttons.append([
            InlineKeyboardButton(f"{role_icon} {title}", callback_data=f"{CB_PREFIX_PM_GROUP}{cid}")
        ])

    keyboard = InlineKeyboardMarkup(buttons)
    text = "**Your Groups**\n\nTap a group to manage it:"

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def _show_group_panel(query, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Show the group management panel for a specific chat."""
    user_id = query.from_user.id

    role = await get_user_role(chat_id, user_id)
    if role not in (Role.OWNER, Role.ADMIN):
        await query.edit_message_text("You no longer have admin access to this group.")
        return

    settings = await get_chat_settings(chat_id)
    db = await get_db()

    try:
        member_count = await context.bot.get_chat_member_count(chat_id)
    except Exception:
        member_count = "?"

    warn_rows = await db.execute_fetchall(
        "SELECT COUNT(*) FROM warnings WHERE chat_id = ?", (chat_id,)
    )
    warn_count = warn_rows[0][0] if warn_rows else 0

    text = (
        f"**Managing Group**\n\n"
        f"Members: {member_count}\n"
        f"Active Warnings: {warn_count}\n"
        f"Warn Threshold: {settings.warn_threshold}\n"
        f"Flood Limit: {settings.flood_limit} msgs/{settings.flood_window}s\n"
        f"Anti-Spam: {'ON' if settings.antispam_enabled else 'OFF'}\n"
        f"Censor: {'ON' if settings.censor_enabled else 'OFF'}\n"
        f"Link Filter: {'ON' if settings.link_filter_enabled else 'OFF'}\n"
    )

    def _on_off(val: bool) -> str:
        return "ON" if val else "OFF"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"Anti-Spam: {_on_off(settings.antispam_enabled)}", callback_data=f"{CB_PREFIX_PM_SETTINGS}toggle_antispam:{chat_id}"),
            InlineKeyboardButton(f"Censor: {_on_off(settings.censor_enabled)}", callback_data=f"{CB_PREFIX_PM_SETTINGS}toggle_censor:{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"Link Filter: {_on_off(settings.link_filter_enabled)}", callback_data=f"{CB_PREFIX_PM_SETTINGS}toggle_linkfilter:{chat_id}"),
            InlineKeyboardButton(f"Welcome: {_on_off(settings.welcome_enabled)}", callback_data=f"{CB_PREFIX_PM_SETTINGS}toggle_welcome:{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"Warn Threshold: {settings.warn_threshold}", callback_data=f"{CB_PREFIX_PM_SETTINGS}cycle_warn:{chat_id}"),
            InlineKeyboardButton(f"Flood Limit: {settings.flood_limit}", callback_data=f"{CB_PREFIX_PM_SETTINGS}cycle_flood:{chat_id}"),
        ],
        [
            InlineKeyboardButton("Back to Groups", callback_data=f"{CB_PREFIX_PM_SETTINGS}back"),
        ],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def pm_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle group selection from PM panel."""
    query = update.callback_query
    await query.answer()

    chat_id = int(query.data[len(CB_PREFIX_PM_GROUP):])
    await _show_group_panel(query, context, chat_id)


async def pm_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle settings changes from PM panel."""
    query = update.callback_query
    await query.answer()

    data = query.data[len(CB_PREFIX_PM_SETTINGS):]

    if data == "back":
        await show_my_groups(update, context)
        return

    parts = data.split(":")
    action = parts[0]
    chat_id = int(parts[1])

    user_id = query.from_user.id
    role = await get_user_role(chat_id, user_id)
    if role not in (Role.OWNER, Role.ADMIN):
        await query.answer("Access denied.", show_alert=True)
        return

    if action == "toggle_antispam":
        settings = await get_chat_settings(chat_id)
        await update_chat_setting(chat_id, "antispam_enabled", int(not settings.antispam_enabled))
    elif action == "toggle_censor":
        settings = await get_chat_settings(chat_id)
        await update_chat_setting(chat_id, "censor_enabled", int(not settings.censor_enabled))
    elif action == "toggle_linkfilter":
        settings = await get_chat_settings(chat_id)
        await update_chat_setting(chat_id, "link_filter_enabled", int(not settings.link_filter_enabled))
    elif action == "toggle_welcome":
        settings = await get_chat_settings(chat_id)
        await update_chat_setting(chat_id, "welcome_enabled", int(not settings.welcome_enabled))
    elif action == "cycle_warn":
        settings = await get_chat_settings(chat_id)
        new_val = (settings.warn_threshold % 10) + 1
        await update_chat_setting(chat_id, "warn_threshold", new_val)
    elif action == "cycle_flood":
        settings = await get_chat_settings(chat_id)
        options = [3, 5, 8, 10, 15, 20]
        idx = options.index(settings.flood_limit) if settings.flood_limit in options else 1
        new_val = options[(idx + 1) % len(options)]
        await update_chat_setting(chat_id, "flood_limit", new_val)

    # Re-show the group management panel
    await _show_group_panel(query, context, chat_id)
