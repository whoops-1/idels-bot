from __future__ import annotations
from dataclasses import dataclass


@dataclass
class BotUser:
    user_id: int
    username: str
    first_name: str
    last_name: str
    is_bot: bool


@dataclass
class ChatMemberInfo:
    chat_id: int
    user_id: int
    role: str
    is_muted: bool
    muted_until: int
