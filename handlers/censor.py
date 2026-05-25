from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from database.connection import get_db
from middleware.permissions import require_role
from utils.constants import Role

logger = logging.getLogger(__name__)


@require_role(Role.ADMIN)
async def add_banned_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not context.args:
        await update.message.reply_text("Usage: /addword <word>")
        return

    word = " ".join(context.args).lower().strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO banned_words (chat_id, word, added_by) VALUES (?, ?, ?)",
            (chat_id, word, user_id),
        )
        await db.commit()
        await update.message.reply_text(f"Added `{word}` to the banned words list.", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(f"`{word}` is already in the banned words list.", parse_mode="Markdown")


@require_role(Role.ADMIN)
async def remove_banned_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeword <word>")
        return

    word = " ".join(context.args).lower().strip()
    chat_id = update.effective_chat.id

    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM banned_words WHERE chat_id = ? AND word = ?",
        (chat_id, word),
    )
    await db.commit()
    if cursor.rowcount > 0:
        await update.message.reply_text(f"Removed `{word}` from the banned words list.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"`{word}` was not found in the banned words list.", parse_mode="Markdown")


@require_role(Role.ADMIN)
async def list_banned_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    chat_id = update.effective_chat.id

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT word, action FROM banned_words WHERE chat_id = ? ORDER BY word",
        (chat_id,),
    )
    if not rows:
        await update.message.reply_text("No banned words set for this chat.")
        return

    text = "**Banned Words:**\n"
    for row in rows:
        text += f"- `{row[0]}` (action: {row[1]})\n"
    await update.message.reply_text(text, parse_mode="Markdown")
