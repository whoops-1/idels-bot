from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from middleware.permissions import require_role
from services.antispam_service import check_flood, check_spam_score, check_links, check_censored_words, get_domain, extract_urls
from services.settings_service import get_chat_settings, update_link_allowlist
from utils.constants import Role

logger = logging.getLogger(__name__)


async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Central message filter pipeline. Runs for every non-command text message."""
    if not update.message or not update.message.text:
        return

    # Ignore DMs
    if update.effective_chat.type == "private":
        return

    # Skip anti-spam checks if user is editing settings
    if context.user_data and context.user_data.get("editing_setting"):
        return

    # Pipeline: flood -> spam score -> links -> censored words
    if await check_flood(update, context):
        return
    if await check_spam_score(update, context):
        return
    if await check_links(update, context):
        return
    await check_censored_words(update, context)


@require_role(Role.ADMIN)
async def add_allowed_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /linkadd <domain>\nExample: /linkadd example.com")
        return

    domain = context.args[0].lower().strip().lstrip("www.")
    settings = await get_chat_settings(update.effective_chat.id)
    allowlist = list(settings.link_allowlist)

    if domain in allowlist:
        await update.message.reply_text(f"`{domain}` is already in the allowlist.", parse_mode="Markdown")
        return

    allowlist.append(domain)
    await update_link_allowlist(update.effective_chat.id, allowlist)
    await update.message.reply_text(f"Added `{domain}` to the link allowlist.", parse_mode="Markdown")


@require_role(Role.ADMIN)
async def remove_allowed_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /linkremove <domain>")
        return

    domain = context.args[0].lower().strip().lstrip("www.")
    settings = await get_chat_settings(update.effective_chat.id)
    allowlist = list(settings.link_allowlist)

    if domain not in allowlist:
        await update.message.reply_text(f"`{domain}` is not in the allowlist.", parse_mode="Markdown")
        return

    allowlist.remove(domain)
    await update_link_allowlist(update.effective_chat.id, allowlist)
    await update.message.reply_text(f"Removed `{domain}` from the link allowlist.", parse_mode="Markdown")


@require_role(Role.ADMIN)
async def list_allowed_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    settings = await get_chat_settings(update.effective_chat.id)
    if not settings.link_allowlist:
        await update.message.reply_text("The link allowlist is empty. All links are blocked when link filter is enabled.")
        return

    text = "**Allowed Domains:**\n"
    for domain in settings.link_allowlist:
        text += f"- `{domain}`\n"
    await update.message.reply_text(text, parse_mode="Markdown")
