from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ChatSettings:
    chat_id: int
    owner_id: int = 0
    # Welcome/Goodbye
    welcome_enabled: bool = True
    welcome_message: str = "Welcome to {chat_name}, {user_mention}!"
    goodbye_enabled: bool = True
    goodbye_message: str = "Goodbye, {user_name}!"
    welcome_media: str = ""
    welcome_media_type: str = ""
    welcome_delete_seconds: int = 0
    goodbye_delete_seconds: int = 0
    # Anti-spam
    antispam_enabled: bool = True
    flood_limit: int = 5
    flood_window: int = 10
    flood_action: str = "mute"
    flood_mute_duration: int = 300
    # Links
    link_filter_enabled: bool = False
    link_allowlist: list[str] = field(default_factory=list)
    # Censor
    censor_enabled: bool = True
    # Warnings
    warn_threshold: int = 3
    warn_action: str = "ban"
    warn_mute_duration: int = 3600
    warn_expire_hours: int = 0
    # Rules
    rules_text: str = ""
    # Lock
    locked_types: list[str] = field(default_factory=list)
    global_lock: bool = False
    # Scan
    auto_scan_enabled: bool = False
    auto_scan_interval: int = 86400
    last_scan: int = 0
    # Captcha
    captcha_enabled: bool = False
    captcha_type: str = "button"
    captcha_timeout: int = 120
    captcha_action: str = "kick"
    # Script filter
    script_filter_enabled: bool = False
    script_filter_action: str = "mute"
    # Anti-raid
    anti_raid_enabled: bool = False
    raid_threshold: int = 10
    raid_window: int = 30
    raid_action: str = "lock"
    # Service purge
    purge_join: bool = False
    purge_leave: bool = False
    purge_pin: bool = False
    purge_photo_change: bool = False
    # Night mode
    night_mode_enabled: bool = False
    night_start: str = "23:00"
    night_end: str = "06:00"
    night_action: str = "mute"
    # Log channel
    log_channel_id: int = 0
    # Slow mode
    slow_mode_seconds: int = 0
    # Triggers
    triggers_enabled: bool = True
    # Federation
    federation_id: int = 0
