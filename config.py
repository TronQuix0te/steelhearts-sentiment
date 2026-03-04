import os
from dotenv import load_dotenv

load_dotenv()

# Discord
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")

# Anthropic
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Batch analysis settings
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "20"))
BATCH_INTERVAL_SECONDS: int = int(os.getenv("BATCH_INTERVAL", "60"))

# Snapshot interval (hourly rollups)
SNAPSHOT_INTERVAL_SECONDS: int = int(os.getenv("SNAPSHOT_INTERVAL", "3600"))

# Web server
WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT: int = int(os.getenv("WEB_PORT", "8050"))

# Database
DB_PATH: str = os.getenv("DB_PATH", "sentiment.db")

# Channel filter — only monitor these channels (empty = all channels)
# Comma-separated names, e.g. "slug-hq,general"
_channels_env = os.getenv("MONITOR_CHANNELS", "slug-hq")
MONITOR_CHANNELS: set = {c.strip() for c in _channels_env.split(",") if c.strip()} if _channels_env else set()

# Users to ignore for sentiment analysis (still stored, just skipped in analysis)
_ignore_env = os.getenv("IGNORE_USERS", "TheBorg404Error,Cõmmãnder heung-Min")
IGNORE_USERS: set = {u.strip() for u in _ignore_env.split(",") if u.strip()} if _ignore_env else set()
