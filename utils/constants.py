from enum import Enum


class Role(Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"

    @classmethod
    def hierarchy(cls) -> dict[str, int]:
        return {cls.OWNER.value: 3, cls.ADMIN.value: 2, cls.MEMBER.value: 1}


class FloodAction(Enum):
    MUTE = "mute"
    KICK = "kick"
    BAN = "ban"


class WarnAction(Enum):
    BAN = "ban"
    KICK = "kick"
    MUTE = "mute"


class CensorAction(Enum):
    WARN = "warn"
    DELETE = "delete"
    BOTH = "both"


# Callback data prefixes for inline keyboards (must be <= 64 bytes total)
CB_PREFIX_SETTINGS = "set:"
CB_PREFIX_WARN_CONFIRM = "wc:"
CB_PREFIX_WARN_CANCEL = "wn:"
CB_PREFIX_UNMUTE = "um:"
CB_PREFIX_UNWARN_LIST = "ul:"
CB_PREFIX_WARN_ACTION = "wa:"       # warn action buttons: reset/mute/ban
CB_PREFIX_BOT_JOIN = "bj:"          # bot join message buttons
CB_PREFIX_USER_JOIN = "uj:"         # user join buttons: message/rules
CB_PREFIX_HELP = "hp:"              # help menu buttons
CB_PREFIX_REPORT = "rp:"            # report action buttons
CB_PREFIX_SCAN = "sc:"              # scan result buttons
CB_PREFIX_PM_GROUP = "pm:"          # PM panel group buttons
CB_PREFIX_PM_SETTINGS = "ps:"       # PM panel settings buttons
CB_PREFIX_CAPTCHA = "cp:"           # captcha verification buttons
CB_PREFIX_FED = "fd:"               # federation buttons
CB_PREFIX_PURGE = "pu:"             # purge confirm buttons
CB_PREFIX_GLOBAL_LOCK = "gl:"       # global lock buttons
CB_PREFIX_RAID = "rd:"              # anti-raid action buttons
CB_PREFIX_USERINFO = "ui:"          # user info action buttons
CB_PREFIX_NIGHT = "nm:"             # night mode buttons

MAX_MESSAGE_LENGTH = 4096
MAX_WARNINGS_DISPLAY = 20
FLOOD_TRACKER_MAX_ENTRIES = 100
