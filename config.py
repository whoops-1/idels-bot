import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
WEBHOOK_URL: str = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_PORT: int = int(os.environ.get("WEBHOOK_PORT", "8443"))
WEBHOOK_LISTEN: str = os.environ.get("WEBHOOK_LISTEN", "0.0.0.0")
WEBHOOK_CERT: str = os.environ.get("WEBHOOK_CERT", "")
WEBHOOK_KEY: str = os.environ.get("WEBHOOK_KEY", "")
DATABASE_PATH: Path = Path(os.environ.get("DATABASE_PATH", "data/bot.db"))
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
OWNER_IDS: list[int] = [int(x) for x in os.environ.get("OWNER_IDS", "").split(",") if x.strip()]
