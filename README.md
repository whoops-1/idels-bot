# TermX Community Management Bot

A full-featured Telegram group management bot built with Python, python-telegram-bot v21, and SQLite. Packed with 60+ commands, interactive buttons, security systems, and admin tools.

## Features

### Moderation
- `/kick`, `/ban`, `/unban`, `/mute`, `/unmute` — Full moderation suite
- `/warn`, `/unwarn`, `/warnings` — Warning system with auto-ban/kick/mute at threshold
- Warn action buttons: [Reset Warns] [Mute] [Ban] inline on every warn message
- `/purge` — Bulk delete messages with confirmation
- `/scan` — Find and kick deleted/inactive accounts
- `/blacklist` — View ban history

### Security
- **Captcha Verification** — Math, text, or button captcha on join. Auto-kick on timeout.
- **Anti-Raid** — Detects mass joins and auto-locks the group
- **Script Filter** — Blocks Arabic/Cyrillic/non-Latin text
- **Global Lock** — Emergency `/lockall` to mute everyone during raids
- **Federation System** — Create federations, share ban lists across groups

### Anti-Spam Pipeline
- Flood detection (sliding window algorithm)
- Spam scoring (caps, repeated chars, mention spam)
- Link filter with per-chat allowlist
- Word censoring with regex support
- All checks run on every message automatically

### Welcome & Goodbye
- Customizable messages with placeholders: `{user_mention}`, `{user_name}`, `{chat_name}`, `{member_count}`
- Welcome media support (photo/video/GIF)
- Auto-delete welcome/goodbye messages after configurable time
- Inline buttons: [Message] [Rules]

### Custom Commands
- `/addtrigger`, `/removetrigger`, `/triggers` — Map keywords to auto-responses
- Supports `#keyword` and `!keyword` syntax

### Notes System
- `/save`, `/get`, `/notes`, `/delnote` — Per-chat saved text snippets
- `/unote`, `/unotes`, `/delunote` — Admin notes about specific users

### Scheduled Messages
- `/scheduletext`, `/schedulepoll` — Schedule recurring or one-time messages
- Time formats: `every 30m`, `daily 09:00`, `weekly mon 14:00`, `2026-06-01 10:00`
- Supports cron expressions via croniter

### Group Maintenance
- **Night Mode** — Auto-lock group at night, unlock in the morning
- **Slow Mode** — Set Telegram slow mode via `/slowmode`
- **Service Message Purge** — Auto-delete join/leave/pin notifications
- **Content Locks** — `/lock media`, `/lock sticker`, `/lock forward`, etc.

### PM Management
- `/mygroups` — Manage all your groups from the bot's DM
- Toggle settings remotely with inline buttons
- View group stats and member counts

### Logging
- `/logchannel` — Forward all admin actions to a private channel
- Full action audit trail in database

### Other
- `/afk` — Set AFK status (auto-clears on next message)
- `/stats` — Group statistics
- `/report` — Members report messages to admins with action buttons
- `/info` — Detailed user info card
- `/tagall` — Ping all members
- `/id` — Get user/chat IDs

## Setup

### Prerequisites
- Python 3.10+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### Installation

```bash
pip install -r requirements.txt
```

### Configuration

Edit `.env`:
```
BOT_TOKEN=your_bot_token_here
OWNER_IDS=your_telegram_user_id
LOG_LEVEL=INFO
DATABASE_PATH=data/bot.db
```

### Run

```bash
# Polling mode (development)
python main.py

# Webhook mode (production)
# Set WEBHOOK_URL in .env, then:
python main.py
```

### Systemd Service (production)

```ini
[Unit]
Description=TermX Community Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/idels
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10
EnvironmentFile=/path/to/idels/.env

[Install]
WantedBy=multi-user.target
```

## Project Structure

```
idels/
├── main.py                    # Entry point, handler wiring
├── config.py                  # Environment config loader
├── database/
│   ├── connection.py          # SQLite async connection (WAL mode)
│   └── schema.py              # 15 tables, migrations
├── handlers/
│   ├── admin.py               # Settings UI, lock/unlock, notes
│   ├── antispam.py            # Message pipeline, link filter
│   ├── censor.py              # Word censoring
│   ├── errors.py              # Global error handler
│   ├── info.py                # Help, stats, AFK, rules
│   ├── moderation.py          # Kick/ban/warn/mute, purge, reports
│   ├── pm_panel.py            # PM-based group management
│   ├── scheduled.py           # Scheduled messages
│   ├── security.py            # Captcha, anti-raid, script filter, federation, night mode
│   ├── triggers.py            # Custom command triggers
│   └── welcome.py             # Welcome/goodbye, service purge
├── middleware/
│   └── permissions.py         # @require_role() decorator
├── models/                    # Dataclasses
├── services/
│   ├── antispam_service.py    # Flood detection, spam scoring
│   ├── schedule_service.py    # JobQueue management
│   ├── settings_service.py    # Settings CRUD, triggers, federations, captcha
│   └── warning_service.py     # Warning CRUD
└── utils/
    ├── constants.py           # Enums, callback prefixes
    └── helpers.py             # Duration parsing, user tracking
```

## Tech Stack

- **python-telegram-bot** v21.6 (async)
- **aiosqlite** — Async SQLite
- **croniter** — Cron expression parsing
- **python-dotenv** — Environment config

## License

MIT
