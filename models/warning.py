from __future__ import annotations
from dataclasses import dataclass


@dataclass
class WarningRecord:
    id: int
    chat_id: int
    user_id: int
    reason: str
    issued_by: int
    issued_at: int
