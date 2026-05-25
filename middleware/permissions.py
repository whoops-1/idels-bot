from __future__ import annotations

import logging
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from utils.constants import Role
from utils.helpers import get_user_role, has_permission

logger = logging.getLogger(__name__)


def require_role(minimum_role: Role):
    """Decorator: checks if the invoking user has the required role before running the handler."""
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.effective_user or not update.effective_chat:
                return

            chat_id = update.effective_chat.id
            user_id = update.effective_user.id
            chat_type = update.effective_chat.type

            # Skip role checks in private chats for DM-compatible commands
            if chat_type == "private" and minimum_role in (Role.MEMBER,):
                return await func(update, context)

            allowed = await has_permission(chat_id, user_id, minimum_role, context.bot)
            if not allowed:
                await update.message.reply_text(
                    f"Permission denied. You need the '{minimum_role.value}' role or higher."
                )
                return

            return await func(update, context)
        return wrapper
    return decorator
