from __future__ import annotations

import html
import logging
import traceback

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest, TimedOut, NetworkError

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler. Logs exceptions and attempts to notify the user."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    if isinstance(context.error, Forbidden):
        logger.warning("Bot lacks permissions or was kicked from a chat.")
        return

    if isinstance(context.error, TimedOut):
        logger.warning("Request timed out (transient).")
        return

    if isinstance(context.error, NetworkError):
        logger.warning(f"Network error: {context.error}")
        return

    if isinstance(update, Update) and update.effective_chat and update.effective_message:
        try:
            error_text = "An error occurred while processing your request."
            if isinstance(context.error, BadRequest):
                error_text = f"Bad request: {context.error}"
            await update.effective_message.reply_text(error_text)
        except Exception:
            logger.error("Failed to send error message to user.", exc_info=True)
