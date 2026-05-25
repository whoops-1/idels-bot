from __future__ import annotations

import json
import time
import logging

from database.connection import get_db
from utils.helpers import parse_json_list

logger = logging.getLogger(__name__)


async def load_scheduled_jobs(application) -> None:
    """Load all active scheduled messages from DB and schedule them in the JobQueue."""
    if not application.job_queue:
        logger.warning("JobQueue not available, skipping scheduled job loading.")
        return

    db = await get_db()
    rows = await db.fetch("SELECT * FROM scheduled_messages WHERE is_active = 1")

    for row in rows:
        r = dict(row)
        job_data = {"scheduled_id": r["id"]}

        when = _compute_when(r)
        if when is None:
            continue

        delay = max(when - time.time(), 0)
        try:
            application.job_queue.run_once(
                execute_scheduled_message,
                when=delay,
                data=job_data,
                name=f"scheduled_{r['id']}",
            )
            logger.info(f"Loaded scheduled job {r['id']}, fires in {delay:.0f}s")
        except Exception as e:
            logger.error(f"Failed to load scheduled job {r['id']}: {e}")


def _compute_when(row: dict) -> float | None:
    """Compute the next execution time for a scheduled message."""
    now = time.time()

    if row["once_at"] > 0:
        return float(row["once_at"])

    if row["interval_seconds"] > 0:
        if row["next_run"] > 0:
            return float(row["next_run"])
        return now + row["interval_seconds"]

    if row["cron_expression"]:
        try:
            from croniter import croniter
            cron = croniter(row["cron_expression"], time.time())
            return cron.get_next(float)
        except Exception as e:
            logger.error(f"Invalid cron expression '{row['cron_expression']}' for job {row['id']}: {e}")
            return None

    return None


async def execute_scheduled_message(context) -> None:
    """Job callback: send the scheduled message, then reschedule if needed."""
    from telegram.constants import PollType as TgPollType

    scheduled_id = context.job.data["scheduled_id"]
    db = await get_db()
    rows = await db.fetch(
        "SELECT * FROM scheduled_messages WHERE id = $1 AND is_active = 1",
        scheduled_id,
    )
    if not rows:
        return

    r = dict(rows[0])
    chat_id = r["chat_id"]

    try:
        if r["message_type"] == "poll":
            options = parse_json_list(r["poll_options"])
            kwargs = {
                "chat_id": chat_id,
                "question": r["poll_question"],
                "options": options,
                "is_anonymous": bool(r["poll_anonymous"]),
            }
            if r["poll_type"] == "quiz" and r["poll_correct_option"] >= 0:
                kwargs["type"] = TgPollType.QUIZ
                kwargs["correct_option_id"] = r["poll_correct_option"]
            else:
                kwargs["type"] = TgPollType.REGULAR
            await context.bot.send_poll(**kwargs)
        else:
            await context.bot.send_message(chat_id, r["text"])
    except Exception as e:
        logger.error(f"Failed to send scheduled message {scheduled_id}: {e}")

    # Reschedule if recurring
    if r["once_at"] > 0:
        await db.execute("UPDATE scheduled_messages SET is_active = 0 WHERE id = $1", scheduled_id)
        return

    next_when = _compute_when(r)
    if next_when is None:
        await db.execute("UPDATE scheduled_messages SET is_active = 0 WHERE id = $1", scheduled_id)
        return

    if r["interval_seconds"] > 0:
        next_run = time.time() + r["interval_seconds"]
    else:
        next_run = next_when

    await db.execute("UPDATE scheduled_messages SET next_run = $1 WHERE id = $2", int(next_run), scheduled_id)

    delay = max(next_run - time.time(), 0)
    context.job_queue.run_once(
        execute_scheduled_message,
        when=delay,
        data={"scheduled_id": scheduled_id},
        name=f"scheduled_{scheduled_id}",
    )
