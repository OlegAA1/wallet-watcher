from __future__ import annotations

import logging
import time
from json import JSONDecodeError

import requests


logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout: int = 20):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> bool:
        if not self.enabled():
            logger.error("Telegram credentials are not configured")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
                if response.status_code == 429 and attempt < 2:
                    time.sleep(_retry_after(response, default=2 + attempt))
                    continue
                if not response.ok:
                    logger.error("Telegram request failed with HTTP status %s", response.status_code)
                    return False
                data = response.json()
                if not data.get("ok"):
                    logger.error("Telegram API returned an error: %s", data.get("description", "unknown error"))
                    return False
                return True
            except (requests.RequestException, JSONDecodeError, ValueError) as error:
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                logger.error("Telegram request failed: %s", error.__class__.__name__)
                return False
        return False

    def send_test_message(self) -> bool:
        return self.send_message("Wallet watcher test message")

    def send_alert(self, event: dict) -> bool:
        tx_url = f"{event['explorer_url'].rstrip('/')}/tx/{event['tx_hash']}"
        text = "\n".join(
            [
                "🚨 Outgoing transfer detected",
                "",
                f"Type: {event['event_type']}",
                f"Wallet: {event['wallet_name']}",
                f"Address: {event['wallet_address']}",
                f"Network: {event['network_name']}",
                f"Asset: {event['asset']}",
                f"Amount: {event['amount']}",
                f"To: {event['to_address']}",
                f"Hash: {event['tx_hash']}",
                f"Tx: {tx_url}",
            ]
        )
        return self.send_message(text)


def _retry_after(response: requests.Response, default: int) -> int:
    value = response.headers.get("Retry-After")
    if value and value.isdigit():
        return min(int(value), 30)
    return default
