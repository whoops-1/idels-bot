from __future__ import annotations

import json
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database.connection import get_db
from middleware.permissions import require_role
from services.schedule_service import execute_scheduled_message
from utils.constants import Role
from utils.helpers import parse_time_spec, format_duration

logger = logging.getLogger(__name__)


@require_role(Role.ADMIN)
async def schedule_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    raw = " ".join(context.args) if context.args else ""
    if "|" not in raw:
        await update.message.reply_text(
            "Usage: /scheduletext <time> | <message>\n\n"
            "Time formats:\n"
            "  every 30m / every 2h / every 1d\n"
            "  daily 09:00\n"
            "  weekly mon 14:00\n"
            "  2026-06-01 10:00"
        )
        return

    parts = raw.split("|", 1)
    time_spec = parts[0].strip()
    message_text = parts[1].strip()

    if not message_text:
        await update.message.reply_text("Message cannot be empty.")
        return

    parsed = parse_time_spec(time_spec)
    if not parsed:
        await update.message.reply_text(f"Invalid time format: `{time_spec}`", parse_mode="Markdown")
        return

    db = await get_db()
    now = int(time.time())

    if "interval_seconds" in parsed:
        next_run = now + parsed["interval_seconds"]
        cursor = await db.execute(
            """INSERT INTO scheduled_messages
               (chat_id, message_type, text, interval_seconds, next_run, created_by)
               VALUES (?, 'text', ?, ?, ?, ?)""",
            (update.effective_chat.id, message_text, parsed["interval_seconds"], next_run, update.effective_user.id),
        )
    elif "cron_expression" in parsed:
        from croniter import croniter
        next_run = int(croniter(parsed["cron_expression"], time.time()).get_next(float))
        cursor = await db.execute(
            """INSERT INTO scheduled_messages
               (chat_id, message_type, text, cron_expression, next_run, created_by)
               VALUES (?, 'text', ?, ?, ?, ?)""",
            (update.effective_chat.id, message_text, parsed["cron_expression"], next_run, update.effective_user.id),
        )
    elif "once_at" in parsed:
        cursor = await db.execute(
            """INSERT INTO scheduled_messages
               (chat_id, message_type, text, once_at, next_run, created_by)
               VALUES (?, 'text', ?, ?, ?, ?)""",
            (update.effective_chat.id, message_text, parsed["once_at"], parsed["once_at"], update.effective_user.id),
        )
        next_run = parsed["once_at"]

    await db.commit()
    job_id = cursor.lastrowid

    delay = max(next_run - time.time(), 0)
    context.job_queue.run_once(
        execute_scheduled_message,
        when=delay,
        data={"scheduled_id": job_id},
        name=f"scheduled_{job_id}",
    )

    await update.message.reply_text(
        f"Scheduled message #{job_id} created. Next run at <code>{next_run}</code>.",
        parse_mode="HTML",
    )


@require_role(Role.ADMIN)
async def schedule_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    raw = " ".join(context.args) if context.args else ""
    if "|" not in raw:
        await update.message.reply_text(
            "Usage: /schedulepoll <time> | <question> | <option1> | <option2> | ...\n\n"
            "Time formats:\n"
            "  every 30m / every 2h / every 1d\n"
            "  daily 09:00\n"
            "  weekly mon 14:00"
        )
        return

    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 4:
        await update.message.reply_text("Need at least: time | question | option1 | option2")
        return

    time_spec = parts[0]
    question = parts[1]
    options = parts[2:]

    if len(options) > 10:
        await update.message.reply_text("Maximum 10 poll options allowed.")
        return

    parsed = parse_time_spec(time_spec)
    if not parsed:
        await update.message.reply_text(f"Invalid time format: `{time_spec}`", parse_mode="Markdown")
        return

    db = await get_db()
    now = int(time.time())
    options_json = json.dumps(options)

    if "interval_seconds" in parsed:
        next_run = now + parsed["interval_seconds"]
        cursor = await db.execute(
            """INSERT INTO scheduled_messages
               (chat_id, message_type, poll_question, poll_options, interval_seconds, next_run, created_by)
               VALUES (?, 'poll', ?, ?, ?, ?, ?)""",
            (update.effective_chat.id, question, options_json, parsed["interval_seconds"], next_run, update.effective_user.id),
        )
    elif "cron_expression" in parsed:
        from croniter import croniter
        next_run = int(croniter(parsed["cron_expression"], time.time()).get_next(float))
        cursor = await db.execute(
            """INSERT INTO scheduled_messages
               (chat_id, message_type, poll_question, poll_options, cron_expression, next_run, created_by)
               VALUES (?, 'poll', ?, ?, ?, ?, ?)""",
            (update.effective_chat.id, question, options_json, parsed["cron_expression"], next_run, update.effective_user.id),
        )
    elif "once_at" in parsed:
        cursor = await db.execute(
            """INSERT INTO scheduled_messages
               (chat_id, message_type, poll_question, poll_options, once_at, next_run, created_by)
               VALUES (?, 'poll', ?, ?, ?, ?, ?)""",
            (update.effective_chat.id, question, options_json, parsed["once_at"], parsed["once_at"], update.effective_user.id),
        )
        next_run = parsed["once_at"]

    await db.commit()
    job_id = cursor.lastrowid

    delay = max(next_run - time.time(), 0)
    context.job_queue.run_once(
        execute_scheduled_message,
        when=delay,
        data={"scheduled_id": job_id},
        name=f"scheduled_{job_id}",
    )

    await update.message.reply_text(
        f"Scheduled poll #{job_id} created. Next run at <code>{next_run}</code>.",
        parse_mode="HTML",
    )


@require_role(Role.ADMIN)
async def list_scheduled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM scheduled_messages WHERE chat_id = ? AND is_active = 1 ORDER BY next_run",
        (update.effective_chat.id,),
    )

    if not rows:
        await update.message.reply_text("No scheduled messages for this chat.")
        return

    for row in rows:
        r = dict(row)
        if r["message_type"] == "poll":
            desc = f"Poll: {r['poll_question']}"
        else:
            desc = f"Text: {r['text'][:50]}{'...' if len(r['text']) > 50 else ''}"

        schedule_desc = ""
        if r["interval_seconds"]:
            schedule_desc = f"every {format_duration(r['interval_seconds'])}"
        elif r["cron_expression"]:
            schedule_desc = f"cron: {r['cron_expression']}"
        elif r["once_at"]:
            schedule_desc = f"at {r['once_at']}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Cancel", callback_data=f"canceljob:{r['id']}")]
        ])

        await update.message.reply_text(
            f"<b>Job #{r['id']}</b>\n"
            f"{desc}\n"
            f"Schedule: {schedule_desc}\n"
            f"Next run: <code>{r['next_run']}</code>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )


@require_role(Role.ADMIN)
async def cancel_scheduled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /canceljob <id>")
        return

    job_id = int(context.args[0])
    db = await get_db()
    await db.execute(
        "UPDATE scheduled_messages SET is_active = 0 WHERE id = ? AND chat_id = ?",
        (job_id, update.effective_chat.id),
    )
    await db.commit()

    # Remove from job queue
    current_jobs = context.job_queue.get_jobs_by_name(f"scheduled_{job_id}")
    for job in current_jobs:
        job.schedule_removal()

    await update.message.reply_text(f"Scheduled job #{job_id} has been cancelled.")


async def cancel_job_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline cancel button for scheduled jobs."""
    query = update.callback_query
    await query.answer()

    job_id = int(query.data.split(":")[1])
    db = await get_db()
    await db.execute(
        "UPDATE scheduled_messages SET is_active = 0 WHERE id = ? AND chat_id = ?",
        (job_id, query.message.chat_id),
    )
    await db.commit()

    current_jobs = context.job_queue.get_jobs_by_name(f"scheduled_{job_id}")
    for job in current_jobs:
        job.schedule_removal()

    await query.edit_message_text(f"Job #{job_id} cancelled.")
