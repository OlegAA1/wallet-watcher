from __future__ import annotations

import argparse
import logging
import signal

from config import BASE_DIR, LOG_FILE, NETWORKS_FILE, STATE_FILE, load_networks, load_settings, load_wallets, setup_logging
from services.notifier import TelegramNotifier
from services.scanner import WalletScanner
from services.server_status import collect_server_status, print_server_status
from services.storage import StateStorage


logger = logging.getLogger(__name__)


def build_scanner() -> WalletScanner:
    settings = load_settings()
    wallets = load_wallets()
    networks = load_networks()
    storage = StateStorage(STATE_FILE, max_hashes=settings["state_max_hashes"])
    notifier = TelegramNotifier(
        bot_token=settings["telegram_bot_token"],
        chat_id=settings["telegram_chat_id"],
        timeout=settings["request_timeout_seconds"],
    )
    return WalletScanner(settings, wallets, networks, storage, notifier)


def _yes_no(value: bool) -> str:
    return "YES" if value else "NO"


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor outgoing EVM wallet activity.")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit.")
    parser.add_argument("--test-api", action="store_true", help="Check provider API access for all configured networks.")
    parser.add_argument("--test-telegram", action="store_true", help="Send a Telegram test message and exit.")
    parser.add_argument("--server-status", action="store_true", help="Print server load and project runtime status.")
    args = parser.parse_args()

    setup_logging()

    if args.server_status:
        print_server_status(collect_server_status(BASE_DIR, STATE_FILE, LOG_FILE))
        return

    scanner = build_scanner()

    def handle_shutdown(signum, frame):
        logger.info("Shutdown signal received; saving state")
        scanner.stop()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    if args.test_telegram:
        ok = scanner.notifier.send_test_message()
        print("Telegram: OK" if ok else "Telegram: ERROR")
        return

    if args.test_api:
        if not NETWORKS_FILE.exists():
            print("networks.json not found")
            return
        for result in scanner.test_apis():
            status = "OK" if result["ok"] else "ERROR"
            print(f"Network: {result['network']}")
            print(f"  Provider: {result['provider']}")
            print(f"  Chain ID: {result.get('chain_id', 'n/a')}")
            print(f"  Endpoint: {result.get('endpoint', 'n/a')}")
            print(f"  API key: {result.get('api_key', 'n/a')}")
            print(f"  Response received: {_yes_no(result.get('response_received', False))}")
            if result.get("http_status") is not None:
                print(f"  HTTP status: {result['http_status']}")
            print(f"  Auth error: {_yes_no(result.get('auth_error', False))}")
            print(f"  Tariff / unsupported chain error: {_yes_no(result.get('plan_or_unsupported_error', False))}")
            print(f"  Rate limit error: {_yes_no(result.get('rate_limit_error', False))}")
            print(f"  Status: {status}")
            print(f"  Message: {result.get('message', '')}")
            print()
        return

    logger.info("Wallet watcher started")
    try:
        if args.once:
            scanner.run_once()
            return
        scanner.run_forever()
    except KeyboardInterrupt:
        logger.info("Interrupted; saving state")
        scanner.stop()
    finally:
        scanner.stop()


if __name__ == "__main__":
    main()
