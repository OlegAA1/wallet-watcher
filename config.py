from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "events.log"
STATE_FILE = BASE_DIR / "state.json"
WALLETS_FILE = BASE_DIR / "wallets.json"
NETWORKS_FILE = BASE_DIR / "networks.json"


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(),
        ],
    )


def load_settings() -> dict:
    load_dotenv(BASE_DIR / ".env")
    return {
        "etherscan_api_key": os.getenv("ETHERSCAN_API_KEY", ""),
        "blockscout_api_key": os.getenv("BLOCKSCOUT_API_KEY", ""),
        "botanixscan_api_key": os.getenv("BOTANIXSCAN_API_KEY", ""),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "check_interval_seconds": _int_env("CHECK_INTERVAL_SECONDS", 300),
        "request_timeout_seconds": 20,
        "fetch_limit": 50,
        "state_max_hashes": 500,
    }


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        logging.getLogger(__name__).error("%s must be an integer; using %s", name, default)
        return default


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_wallets() -> list[dict]:
    return load_json_file(WALLETS_FILE, [])


def load_networks() -> list[dict]:
    return load_json_file(NETWORKS_FILE, [])
