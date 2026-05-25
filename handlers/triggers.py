from __future__ import annotations

import re
import logging

from telegram import Update
from telegram.ext import ContextTypes

from middleware.permissions import require_role
from services.settings_service import get_chat_settings, get_trigger, get_all_triggers, add_trigger, remove_trigger
from utils.constants import Role

logger = logging.getLogger(__name__)


@require_role(Role.ADMIN)
async def add_trigger_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a custom trigger. Usage: /addtrigger <keyword> <response>"""
    if not update.message or not update.effective_user:
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /addtrigger <keyword> <response>\n\n"
            "Examples:\n"
            "/addtrigger rules Read the rules!\n"
            "/addtrigger #website https://example.com\n"
            "Then typing 'rules' or '#website' triggers the response."
        )
        return

    keyword = context.args[0].lower()
    response = " ".join(context.args[1:])
    chat_id = update.effective_chat.id

    await add_trigger(chat_id, keyword, response, update.effective_user.id)
    await update.message.reply_text(f"Trigger `{keyword}` added.", parse_mode="Markdown")


@require_role(Role.ADMIN)
async def remove_trigger_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a trigger. Usage: /removetrigger <keyword>"""
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /removetrigger <keyword>")
        return

    keyword = context.args[0].lower()
    chat_id = update.effective_chat.id

    if await remove_trigger(chat_id, keyword):
        await update.message.reply_text(f"Trigger `{keyword}` removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Trigger `{keyword}` not found.", parse_mode="Markdown")


async def list_triggers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all triggers."""
    if not update.message:
        return

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)

    if not settings.triggers_enabled:
        await update.message.reply_text("Triggers are disabled for this chat.")
        return

    triggers = await get_all_triggers(chat_id)
    if not triggers:
        await update.message.reply_text("No custom triggers set.")
        return

    text = "**Custom Triggers:**\n\n"
    for t in triggers:
        response_preview = t["response"][:50] + ("..." if len(t["response"]) > 50 else "")
        text += f"`{t['keyword']}` → {response_preview}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def check_triggers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if a message matches any trigger. Returns True if triggered."""
    if not update.message or not update.message.text:
        return False
    if update.effective_chat.type == "private":
        return False

    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    if not settings.triggers_enabled:
        return False

    text = update.message.text.strip().lower()

    # Check exact keyword match first
    trigger = await get_trigger(chat_id, text)
    if not trigger:
        # Check with # prefix removed
        if text.startswith("#"):
            trigger = await get_trigger(chat_id, text[1:])
        # Check with ! prefix removed
        elif text.startswith("!"):
            trigger = await get_trigger(chat_id, text[1:])

    if not trigger:
        return False

    response = trigger["response"]

    # Send the response
    try:
        # Check if response is a URL (might be a link)
        if response.startswith(("http://", "https://")):
            await update.message.reply_text(response)
        else:
            await update.message.reply_text(response, parse_mode="Markdown")
    except Exception:
        try:
            await update.message.reply_text(response)
        except Exception:
            pass

    # Delete trigger message if configured
    if trigger.get("delete_trigger"):
        try:
            await update.message.delete()
        except Exception:
            pass

    return True


@require_role(Role.ADMIN)
async def toggle_triggers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable/disable triggers."""
    if not update.message:
        return
    chat_id = update.effective_chat.id
    settings = await get_chat_settings(chat_id)
    new_val = not settings.triggers_enabled
    from services.settings_service import update_chat_setting
    await update_chat_setting(chat_id, "triggers_enabled", int(new_val))
    await update.message.reply_text(f"Triggers {'enabled' if new_val else 'disabled'}.")
