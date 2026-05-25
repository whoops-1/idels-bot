from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ScheduledMessage:
    id: int
    chat_id: int
    message_type: str
    text: str
    poll_question: str
    poll_options: list[str] = field(default_factory=list)
    poll_anonymous: bool = True
    poll_type: str = "regular"
    poll_correct_option: int = -1
    cron_expression: str = ""
    interval_seconds: int = 0
    once_at: int = 0
    next_run: int = 0
    is_active: bool = True
    created_by: int = 0
