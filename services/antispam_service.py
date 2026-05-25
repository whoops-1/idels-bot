from __future__ import annotations

import re
import time
import logging
from collections import defaultdict
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import ContextTypes

from services.settings_service import get_chat_settings
from utils.constants import FLOOD_TRACKER_MAX_ENTRIES

logger = logging.getLogger(__name__)

# In-memory flood tracker: {(chat_id, user_id): [timestamp1, timestamp2, ...]}
_flood_tracker: dict[tuple[int, int], list[float]] = defaultdict(list)

_URL_PATTERN = re.compile(
    r'https?://[^\s<>\")\]]+|'
    r'(?:www\.)[^\s<>\")\]]+|'
    r'\b[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?'
)

SPAM_INDICATORS = {
    "excessive_caps": lambda t: len(re.findall(r'[A-Z]', t)) / max(len(t), 1) > 0.7 and len(t) > 10,
    "repeated_chars": lambda t: bool(re.search(r'(.)\1{9,}', t)),
    "mention_spam": lambda t: len(re.findall(r'@\w+', t)) > 5,
}


async def check_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if the user is flooding (should be acted upon)."""
    if not update.message or not update.effective_user:
        return False

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    settings = await get_chat_settings(chat_id)

    if not settings.antispam_enabled:
        return False

    key = (chat_id, user_id)
    now = time.time()

    _flood_tracker[key].append(now)
    cutoff = now - settings.flood_window
    _flood_tracker[key] = [t for t in _flood_tracker[key] if t > cutoff]

    if len(_flood_tracker[key]) > FLOOD_TRACKER_MAX_ENTRIES:
        _flood_tracker[key] = _flood_tracker[key][-FLOOD_TRACKER_MAX_ENTRIES:]

    if len(_flood_tracker[key]) > settings.flood_limit:
        action = settings.flood_action
        try:
            if action == "mute":
                await context.bot.restrict_chat_member(
                    chat_id, user_id,
                    permissions=_no_send_permissions(),
                )
                duration = settings.flood_mute_duration
                context.job_queue.run_once(
                    _unmute_flood_job,
                    when=duration,
                    data={"chat_id": chat_id, "user_id": user_id},
                    name=f"unmute_{chat_id}_{user_id}",
                )
                await update.message.reply_text(f"User muted for flooding ({duration}s).")
            elif action == "kick":
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.unban_chat_member(chat_id, user_id)
                await update.message.reply_text("User kicked for flooding.")
            elif action == "ban":
                await context.bot.ban_chat_member(chat_id, user_id)
                await update.message.reply_text("User banned for flooding.")
        except Exception as e:
            logger.error(f"Flood action failed: {e}")

        try:
            await update.message.delete()
        except Exception:
            pass

        _flood_tracker[key] = []
        return True

    return False


async def _unmute_flood_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    chat_id, user_id = data["chat_id"], data["user_id"]
    try:
        from telegram import ChatPermissions
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=True,
                can_invite_users=True,
                can_pin_messages=True,
            ),
        )
    except Exception as e:
        logger.error(f"Failed to unmute flooded user {user_id} in {chat_id}: {e}")


def spam_score(message_text: str) -> int:
    """Returns a spam score. Score >= 2 is considered spam."""
    return sum(1 for check in SPAM_INDICATORS.values() if check(message_text))


def extract_urls(text: str) -> list[str]:
    """Extract all URLs from text."""
    return _URL_PATTERN.findall(text)


def get_domain(url: str) -> str:
    """Extract domain from a URL."""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    if ":" in domain:
        domain = domain.split(":")[0]
    return domain.lower()


async def check_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if an unapproved link was found and action taken."""
    if not update.message or not update.message.text:
        return False

    settings = await get_chat_settings(update.effective_chat.id)
    if not settings.link_filter_enabled:
        return False

    urls = extract_urls(update.message.text)
    if not urls:
        return False

    allowlist = [d.lower() for d in settings.link_allowlist]
    for url in urls:
        domain = get_domain(url)
        is_allowed = False
        for allowed in allowlist:
            if domain == allowed or domain.endswith("." + allowed):
                is_allowed = True
                break
        if not is_allowed:
            try:
                await update.message.reply_text(
                    f"Links from `{domain}` are not allowed. Contact an admin to add to allowlist.",
                    parse_mode="Markdown",
                )
                await update.message.delete()
            except Exception:
                pass
            return True

    return False


async def check_censored_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if a banned word was found and message acted upon."""
    if not update.message or not update.message.text:
        return False

    settings = await get_chat_settings(update.effective_chat.id)
    if not settings.censor_enabled:
        return False

    from database.connection import get_db
    db = await get_db()
    rows = await db.fetch(
        "SELECT word, action FROM banned_words WHERE chat_id = $1",
        update.effective_chat.id,
    )
    if not rows:
        return False

    text_lower = update.message.text.lower()
    for row in rows:
        word = row["word"]
        pattern = r'\b' + re.escape(word) + r'\b'
        if re.search(pattern, text_lower, re.IGNORECASE):
            action = row["action"]
            try:
                if action in ("delete", "both"):
                    await update.message.delete()
                if action in ("warn", "both"):
                    user_mention = f'<a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>'
                    await update.message.reply_text(
                        f"{user_mention}, that word is not allowed here.",
                        parse_mode="HTML",
                    )
            except Exception:
                pass
            return True

    return False


def _no_send_permissions():
    from telegram import ChatPermissions
    return ChatPermissions(can_send_messages=False)


async def check_spam_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if the message is spam (by scoring) and was acted upon."""
    if not update.message or not update.message.text:
        return False

    settings = await get_chat_settings(update.effective_chat.id)
    if not settings.antispam_enabled:
        return False

    if spam_score(update.message.text) >= 2:
        try:
            user_mention = f'<a href="tg://user?id={update.effective_user.id}">{update.effective_user.first_name}</a>'
            await update.message.delete()
            await update.message.reply_text(
                f"{user_mention}, your message was flagged as spam.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return True

    return False
