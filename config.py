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
    legacy_interval = _int_env("CHECK_INTERVAL_SECONDS", 300)
    return {
        "etherscan_api_keys": _list_env("ETHERSCAN_API_KEY"),
        "blockscout_api_keys": _list_env("BLOCKSCOUT_API_KEY"),
        "botanixscan_api_keys": _list_env("BOTANIXSCAN_API_KEY"),
        "blockpi_rpc_urls": {
            "BLOCKPI_BASE_RPC_URL": _list_env("BLOCKPI_BASE_RPC_URL"),
            "BLOCKPI_AVALANCHE_RPC_URL": _list_env("BLOCKPI_AVALANCHE_RPC_URL"),
            "BLOCKPI_OPTIMISM_RPC_URL": _list_env("BLOCKPI_OPTIMISM_RPC_URL"),
        },
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "check_interval_seconds": legacy_interval,
        "api_check_interval_seconds": _int_env("API_CHECK_INTERVAL_SECONDS", legacy_interval),
        "blockpi_check_interval_seconds": _int_env("BLOCKPI_CHECK_INTERVAL_SECONDS", 300),
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


def _list_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_wallets() -> list[dict]:
    return load_json_file(WALLETS_FILE, [])


def load_networks() -> list[dict]:
    return load_json_file(NETWORKS_FILE, [])
