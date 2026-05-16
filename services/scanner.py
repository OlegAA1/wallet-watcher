from __future__ import annotations

import logging
import threading

from services.notifier import TelegramNotifier
from services.providers.blockscout import BlockscoutProvider
from services.providers.blockpi_rpc import BlockPiRpcProvider
from services.providers.botanixscan import BotanixScanProvider
from services.providers.etherscan_v2 import EtherscanV2Provider
from services.storage import StateStorage


logger = logging.getLogger(__name__)


class WalletScanner:
    def __init__(
        self,
        settings: dict,
        wallets: list[dict],
        networks: list[dict],
        storage: StateStorage,
        notifier: TelegramNotifier,
    ):
        self.settings = settings
        self.wallets = wallets
        self.networks = networks
        self.storage = storage
        self.notifier = notifier
        self.providers = self._build_providers()
        self.stop_event = threading.Event()

    def run_once(self) -> None:
        initial_state_changed = False

        for wallet in self._enabled_wallets():
            if self.stop_event.is_set():
                break
            wallet_address = wallet["address"]
            for network in self.networks:
                if self.stop_event.is_set():
                    break
                provider = self.providers.get(network.get("api_provider"))
                if provider is None:
                    logger.error("Unknown provider for %s: %s", network.get("name"), network.get("api_provider"))
                    continue

                try:
                    native_events = provider.get_native_transactions(network, wallet_address)
                    initial_state_changed = self._handle_events(wallet, network, "native", native_events) or initial_state_changed
                except Exception as error:
                    logger.error("Native scan failed for %s / %s: %s", wallet.get("name"), network.get("name"), error)

                if self.stop_event.is_set():
                    break

                try:
                    token_events = provider.get_token_transfers(network, wallet_address)
                    initial_state_changed = self._handle_events(wallet, network, "erc20", token_events) or initial_state_changed
                except Exception as error:
                    logger.error("ERC-20 scan failed for %s / %s: %s", wallet.get("name"), network.get("name"), error)

        try:
            self.storage.save()
        except OSError as error:
            logger.error("Could not save state file: %s", error)
        if initial_state_changed:
            logger.info("Initial state saved")

    def _handle_events(self, wallet: dict, network: dict, event_type: str, events: list[dict]) -> bool:
        if self.stop_event.is_set():
            return False

        wallet_address = wallet["address"]
        outgoing_events = self._outgoing(events, wallet_address)
        key_exists = self.storage.has_key(network["name"], wallet_address, event_type)

        if not key_exists:
            self.storage.ensure_key(network["name"], wallet_address, event_type)
            for event in outgoing_events:
                tx_hash = event.get("tx_hash")
                if tx_hash:
                    self.storage.add(network["name"], wallet_address, event_type, tx_hash)
            return True

        self._process_new_events(wallet, network, event_type, outgoing_events)
        return False

    def _save_state_or_log(self) -> bool:
        try:
            self.storage.save()
            return True
        except OSError as error:
            logger.error("Could not save state file: %s", error)
            return False

    def run_forever(self) -> None:
        interval = self.settings["check_interval_seconds"]
        while not self.stop_event.is_set():
            self.run_once()
            self.stop_event.wait(interval)

    def stop(self) -> None:
        self.stop_event.set()
        self._save_state_or_log()

    def test_apis(self) -> list[dict]:
        results = []
        for network in self.networks:
            provider = self.providers.get(network.get("api_provider"))
            if provider is None:
                results.append(
                    {
                        "network": network.get("name", "Unknown"),
                        "provider": network.get("api_provider", "unknown"),
                        "chain_id": network.get("chain_id"),
                        "endpoint": "n/a",
                        "api_key": "n/a",
                        "response_received": False,
                        "auth_error": False,
                        "plan_or_unsupported_error": False,
                        "rate_limit_error": False,
                        "ok": False,
                        "message": "Unknown provider",
                    }
                )
                continue
            if hasattr(provider, "diagnostic_network"):
                results.append(provider.diagnostic_network(network))
                continue
            ok, message = provider.test_network(network)
            results.append(
                {
                    "network": network.get("name", "Unknown"),
                    "provider": network.get("api_provider", "unknown"),
                    "chain_id": network.get("chain_id"),
                    "endpoint": "n/a",
                    "api_key": "n/a",
                    "response_received": ok,
                    "auth_error": False,
                    "plan_or_unsupported_error": False,
                    "rate_limit_error": False,
                    "ok": ok,
                    "message": message,
                }
            )
        return results

    def _process_new_events(self, wallet: dict, network: dict, event_type: str, events: list[dict]) -> None:
        # Providers return newest first; reverse so Telegram alerts arrive oldest-to-newest.
        for event in reversed(events):
            if self.stop_event.is_set():
                break
            tx_hash = event.get("tx_hash", "")
            if not tx_hash:
                continue
            if self.storage.has_seen(network["name"], wallet["address"], event_type, tx_hash):
                continue

            alert_event = {
                **event,
                "event_type": "native tx" if event_type == "native" else "ERC-20 transfer",
                "wallet_name": wallet["name"],
                "wallet_address": wallet["address"],
                "network_name": network["name"],
                "explorer_url": network["explorer_url"],
            }
            self.storage.add(network["name"], wallet["address"], event_type, tx_hash)
            if not self._save_state_or_log():
                logger.error("Alert skipped because state could not be saved for %s / %s", network["name"], tx_hash)
                continue

            sent = self.notifier.send_alert(alert_event)
            if sent:
                logger.info("Alert sent for %s / %s / %s", wallet["name"], network["name"], tx_hash)

    def _outgoing(self, events: list[dict], wallet_address: str) -> list[dict]:
        normalized_wallet = wallet_address.lower()
        return [
            event
            for event in events
            if event.get("from_address", "").lower() == normalized_wallet
        ]

    def _enabled_wallets(self) -> list[dict]:
        return [
            wallet
            for wallet in self.wallets
            if wallet.get("enabled", True) and wallet.get("address")
        ]

    def _build_providers(self) -> dict:
        timeout = self.settings["request_timeout_seconds"]
        limit = self.settings["fetch_limit"]
        return {
            "etherscan_v2": EtherscanV2Provider(
                api_key=self.settings["etherscan_api_key"],
                timeout=timeout,
                limit=limit,
            ),
            "blockscout": BlockscoutProvider(
                api_key=self.settings["blockscout_api_key"],
                timeout=timeout,
                limit=limit,
            ),
            "botanixscan": BotanixScanProvider(
                api_key=self.settings["botanixscan_api_key"],
                timeout=timeout,
                limit=limit,
            ),
            "blockpi_rpc": BlockPiRpcProvider(
                rpc_urls=self.settings["blockpi_rpc_urls"],
                timeout=timeout,
                limit=limit,
            ),
        }
