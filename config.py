# config.py
"""Runtime configuration for JavMaster.

All deploy-specific values are read from environment variables so this file can be
committed safely. Copy `.env.example` to `.env` for Docker Compose deployments.
"""

import os


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int = 0) -> int:
    raw = env(name, str(default))
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# Discord
TOKEN = env("DISCORD_BOT_TOKEN", env("TOKEN", ""))
REPORT_CHANNEL_ID = env_int("DISCORD_REPORT_CHANNEL_ID", 0)

# Gopeed
GOPEED_URL = env("GOPEED_URL", "http://gopeed:9999/api/v1")
GOPEED_TOKEN = env("GOPEED_TOKEN", "")
DEFAULT_GOPEED_DOWNLOAD_PATH = env("GOPEED_DOWNLOAD_PATH", "/downloads")

# Data / filesystem
DATA_DIR = env("DATA_DIR", "/app/data")
WATCHLIST_FILE = env("WATCHLIST_FILE", f"{DATA_DIR}/watchlist.json")
BOT_TASKS_FILE = env("BOT_TASKS_FILE", f"{DATA_DIR}/bot_tasks.json")
ACTRESS_WATCHLIST_FILE = env("ACTRESS_WATCHLIST_FILE", f"{DATA_DIR}/actress_watchlist.json")
JAVMASTER_SETTINGS_FILE = env("JAVMASTER_SETTINGS_FILE", f"{DATA_DIR}/javmaster_settings.json")
JAVMASTER_RESOURCE_STATE_FILE = env("JAVMASTER_RESOURCE_STATE_FILE", f"{DATA_DIR}/resource_state.json")
CLEANUP_PATH = env("CLEANUP_PATH", "/downloads")

# Metadata sources. METADATA_SOURCE_ORDER controls scrape fallback order.
# Default skips official FANZA/DMM because it can return age/region-block pages from AU,
# which are not valid movie metadata for automated scraping.
METADATA_SOURCE_ORDER = env("METADATA_SOURCE_ORDER", "javdb,javbus,javlibrary")
AVWIKIDB_BASE_URL = env("AVWIKIDB_BASE_URL", "https://avwikidb.com")
R18DEV_BASE_URL = env("R18DEV_BASE_URL", "https://r18.dev")
FANZA_BASE_URL = env("FANZA_BASE_URL", "https://www.dmm.co.jp")
JAVDB_BASE_URL = env("JAVDB_BASE_URL", "https://javdb.com")
JAVBUS_BASE_URL = env("JAVBUS_BASE_URL", "https://www.javbus.com")
JAVLIBRARY_BASE_URL = env("JAVLIBRARY_BASE_URL", "https://www.javlibrary.com")
