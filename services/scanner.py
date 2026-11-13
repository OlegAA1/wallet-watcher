from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import threading
import time

from services.notifier import TelegramNotifier
from services.providers.blockscout import BlockscoutProvider
from services.providers.etherscan_v2 import EtherscanV2Provider
from services.providers.rpc import RpcProvider
from services.storage import StateStorage


logger = logging.getLogger(__name__)

# "blockpi_rpc" is the old name of the "rpc" provider and is still accepted.
RPC_PROVIDER_NAMES = ("rpc", "blockpi_rpc")
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
MAX_PENDING_ALERTS = 500
MAX_ALERT_ATTEMPTS = 50
ETHERSCAN_FREE_DAILY_CALLS = 100_000


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
        self._cycle_stats: dict[str, dict] = {}
        self._health: dict[str, dict] = {}

    def run_once(self, networks: list[dict] | None = None) -> None:
        initial_state_changed = False
        scan_networks = self._enabled_networks(networks if networks is not None else self.networks)
        wallets = self._enabled_wallets()
        rpc_networks = [n for n in scan_networks if n.get("api_provider") in RPC_PROVIDER_NAMES]
        api_networks = [n for n in scan_networks if n.get("api_provider") not in RPC_PROVIDER_NAMES]

        self._flush_pending_alerts()

        for network in rpc_networks:
            if self.stop_event.is_set():
                break
            initial_state_changed = self._scan_rpc_network(network, wallets) or initial_state_changed

        for wallet in wallets:
            if self.stop_event.is_set():
                break
            wallet_address = wallet["address"]
            for network in api_networks:
                if self.stop_event.is_set():
                    break
                provider = self.providers.get(network.get("api_provider"))
                if provider is None:
                    logger.error("Unknown provider for %s: %s", network.get("name"), network.get("api_provider"))
                    self._record(network, False, "Unknown provider")
                    continue

                try:
                    native_events = provider.get_native_transactions(network, wallet_address)
                    initial_state_changed = self._handle_events(wallet, network, "native", native_events) or initial_state_changed
                    self._record(network, True)
                except Exception as error:
                    logger.error("Native scan failed for %s / %s: %s", wallet.get("name"), network.get("name"), error)
                    self._record(network, False, error)

                if self.stop_event.is_set():
                    break

                try:
                    token_events = provider.get_token_transfers(network, wallet_address)
                    initial_state_changed = self._handle_events(wallet, network, "erc20", token_events) or initial_state_changed
                    self._record(network, True)
                except Exception as error:
                    logger.error("ERC-20 scan failed for %s / %s: %s", wallet.get("name"), network.get("name"), error)
                    self._record(network, False, error)

        self._flush_pending_alerts()
        self._save_state_or_log()
        if initial_state_changed:
            logger.info("Initial state saved")
        self._report_health()

    def _scan_rpc_network(self, network: dict, wallets: list[dict]) -> bool:
        """One scan of an RPC network for all wallets. Returns True on the first (baseline) scan."""
        name = network["name"]
        provider = self.providers[network["api_provider"]]
        try:
            result = provider.scan(network, [wallet["address"] for wallet in wallets], self.storage.rpc_cursor(name))
        except Exception as error:
            logger.error("RPC scan failed for %s: %s", name, error)
            self._record(network, False, error)
            return False

        self._record(network, True)
        wallets_by_address = {wallet["address"].lower(): wallet for wallet in wallets}
        for (address, event_type), events in result.events.items():
            wallet = wallets_by_address.get(address)
            if wallet is None:
                continue
            self.storage.ensure_key(name, address, event_type)
            self._process_new_events(wallet, network, event_type, events)

        # The cursor moves only after the events are stored, so a crash in between
        # leads to a rescan (deduplicated by event id), never to a lost event.
        self.storage.set_rpc_cursor(name, result.cursor)
        self._save_state_or_log()
        return result.initialized

    def _handle_events(self, wallet: dict, network: dict, event_type: str, events: list[dict]) -> bool:
        if self.stop_event.is_set():
            return False

        wallet_address = wallet["address"]
        outgoing_events = self._outgoing(events, wallet_address)
        key_exists = self.storage.has_key(network["name"], wallet_address, event_type)

        if not key_exists:
            self.storage.ensure_key(network["name"], wallet_address, event_type)
            for event in outgoing_events:
                event_id = _event_id(event)
                if event_id:
                    self.storage.add(network["name"], wallet_address, event_type, event_id)
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
        api_interval = self.settings["api_check_interval_seconds"]
        rpc_interval = self.settings["rpc_check_interval_seconds"]
        enabled = self._enabled_networks(self.networks)
        api_networks = [n for n in enabled if n.get("api_provider") not in RPC_PROVIDER_NAMES]
        rpc_networks = [n for n in enabled if n.get("api_provider") in RPC_PROVIDER_NAMES]
        next_api_scan = 0.0
        next_rpc_scan = 0.0

        logger.info("API scan interval: %s seconds", api_interval)
        logger.info("RPC scan interval: %s seconds", rpc_interval)
        self._warn_about_etherscan_quota(api_networks, api_interval)

        while not self.stop_event.is_set():
            now = time.monotonic()
            next_wake = None

            if api_networks and now >= next_api_scan:
                logger.info("Running API scan for %s networks", len(api_networks))
                self.run_once(api_networks)
                next_api_scan = time.monotonic() + api_interval

            if rpc_networks and not self.stop_event.is_set() and now >= next_rpc_scan:
                logger.info("Running RPC scan for %s networks", len(rpc_networks))
                self.run_once(rpc_networks)
                next_rpc_scan = time.monotonic() + rpc_interval

            if api_networks:
                next_wake = next_api_scan if next_wake is None else min(next_wake, next_api_scan)
            if rpc_networks:
                next_wake = next_rpc_scan if next_wake is None else min(next_wake, next_rpc_scan)
            if next_wake is None:
                logger.warning("No networks configured; waiting %s seconds", api_interval)
                self.stop_event.wait(api_interval)
                continue

            wait_seconds = max(1.0, next_wake - time.monotonic())
            self.stop_event.wait(wait_seconds)

    def request_stop(self) -> None:
        """Safe to call from a signal handler: only sets a flag."""
        self.stop_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self._save_state_or_log()

    def test_apis(self) -> list[dict]:
        results = []
        for network in self._enabled_networks(self.networks):
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
            results.append(provider.diagnostic_network(network))
        return results

    def _process_new_events(self, wallet: dict, network: dict, event_type: str, events: list[dict]) -> None:
        # Providers return newest first; reverse so Telegram alerts arrive oldest-to-newest.
        for event in reversed(events):
            if self.stop_event.is_set():
                break
            event_id = _event_id(event)
            if not event_id:
                continue
            tx_hash = event.get("tx_hash", "")
            if self.storage.has_seen(network["name"], wallet["address"], event_type, event_id, legacy_hash=tx_hash):
                continue

            self.storage.add(network["name"], wallet["address"], event_type, event_id)
            if self._is_stale_event(event):
                self._save_state_or_log()
                logger.info("Old event skipped for %s / %s / %s", wallet["name"], network["name"], event_id)
                continue

            alert_event = {
                **event,
                "event_type": "native tx" if event_type == "native" else "ERC-20 transfer",
                "wallet_name": wallet["name"],
                "wallet_address": wallet["address"],
                "network_name": network["name"],
                "explorer_url": network["explorer_url"],
            }
            # The alert is persisted together with the "seen" mark and stays in the
            # queue until Telegram confirms delivery.
            pending = self.storage.pending_alerts()
            pending.append(alert_event)
            if len(pending) > MAX_PENDING_ALERTS:
                dropped = pending.pop(0)
                logger.error("Pending alert queue is full; dropped alert for %s", dropped.get("tx_hash"))
            self._save_state_or_log()

    def _flush_pending_alerts(self) -> None:
        pending = self.storage.pending_alerts()
        while pending and not self.stop_event.is_set():
            alert = pending[0]
            if self.notifier.send_alert(alert):
                logger.info(
                    "Alert sent for %s / %s / %s",
                    alert.get("wallet_name"),
                    alert.get("network_name"),
                    alert.get("tx_hash") or alert.get("event_id"),
                )
                pending.pop(0)
                self._save_state_or_log()
                continue

            alert["attempts"] = int(alert.get("attempts", 0)) + 1
            if alert["attempts"] >= MAX_ALERT_ATTEMPTS:
                logger.error("Alert dropped after %s failed attempts: %s", alert["attempts"], alert.get("tx_hash"))
                pending.pop(0)
                self._save_state_or_log()
                continue
            logger.warning("Telegram delivery failed; %s alert(s) will be retried on the next scan", len(pending))
            self._save_state_or_log()
            break

    def _record(self, network: dict, ok: bool, error=None) -> None:
        stats = self._cycle_stats.setdefault(network.get("name", "Unknown"), {"ok": 0, "failed": 0, "error": ""})
        if ok:
            stats["ok"] += 1
        else:
            stats["failed"] += 1
            stats["error"] = str(error)[:180]

    def _report_health(self) -> None:
        """Tell Telegram when a network keeps failing, and when it recovers."""
        threshold = int(self.settings.get("network_failure_alert_cycles", 3))
        for name, stats in self._cycle_stats.items():
            health = self._health.setdefault(name, {"failed_cycles": 0, "alerted": False})
            if stats["failed"] > stats["ok"]:
                health["failed_cycles"] += 1
                if threshold > 0 and health["failed_cycles"] >= threshold and not health["alerted"]:
                    text = (
                        f"⚠️ Wallet watcher: {name} is failing\n\n"
                        f"Failed checks in a row: {health['failed_cycles']}\n"
                        f"Last error: {stats['error']}\n\n"
                        "Outgoing transfers on this network are not being detected."
                    )
                    health["alerted"] = self.notifier.send_message(text)
            else:
                if health["alerted"]:
                    self.notifier.send_message(f"✅ Wallet watcher: {name} is working again")
                health["failed_cycles"] = 0
                health["alerted"] = False
        self._cycle_stats.clear()

    def _warn_about_etherscan_quota(self, api_networks: list[dict], interval: int) -> None:
        etherscan_networks = [n for n in api_networks if n.get("api_provider") == "etherscan_v2"]
        keys = max(1, len(self.settings.get("etherscan_api_keys") or []))
        wallets = len(self._enabled_wallets())
        if not etherscan_networks or not wallets or interval <= 0:
            return
        calls_per_day = wallets * len(etherscan_networks) * 2 * (86400 / interval) / keys
        if calls_per_day > ETHERSCAN_FREE_DAILY_CALLS:
            logger.warning(
                "Etherscan: about %d calls per day per API key, the free plan allows %d. "
                "Add API keys, raise API_CHECK_INTERVAL_SECONDS or disable networks.",
                calls_per_day,
                ETHERSCAN_FREE_DAILY_CALLS,
            )

    def _is_stale_event(self, event: dict) -> bool:
        max_age = int(self.settings.get("max_event_age_seconds", 0))
        if max_age <= 0:
            return False

        timestamp = _event_timestamp(event.get("timestamp"))
        if timestamp is None:
            return False

        return (time.time() - timestamp) > max_age

    def _outgoing(self, events: list[dict], wallet_address: str) -> list[dict]:
        normalized_wallet = wallet_address.lower()
        return [
            event
            for event in events
            if event.get("from_address", "").lower() == normalized_wallet
        ]

    def _enabled_wallets(self) -> list[dict]:
        wallets = []
        for wallet in self.wallets:
            if not wallet.get("enabled", True) or not wallet.get("address"):
                continue
            address = str(wallet["address"]).strip()
            if not ADDRESS_PATTERN.match(address):
                logger.error("Wallet %s skipped: %r is not a valid EVM address", wallet.get("name"), address)
                continue
            wallets.append({**wallet, "address": address, "name": wallet.get("name") or address})
        return wallets

    def _enabled_networks(self, networks: list[dict]) -> list[dict]:
        return [
            network
            for network in networks
            if network.get("enabled", True)
        ]

    def _build_providers(self) -> dict:
        timeout = self.settings["request_timeout_seconds"]
        limit = self.settings["fetch_limit"]
        rpc = RpcProvider(timeout=timeout)
        return {
            "etherscan_v2": EtherscanV2Provider(
                api_key=self.settings["etherscan_api_keys"],
                timeout=timeout,
                limit=limit,
                calls_per_second=self.settings.get("etherscan_calls_per_second", 3),
            ),
            "blockscout": BlockscoutProvider(
                api_key=self.settings["blockscout_api_keys"],
                timeout=timeout,
                limit=limit,
            ),
            **{name: rpc for name in RPC_PROVIDER_NAMES},
        }


def _event_id(event: dict) -> str:
    return event.get("event_id") or event.get("tx_hash") or ""


def _event_timestamp(value) -> float | None:
    if value in (None, "", 0, "0"):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if text.isdigit():
        return float(text)

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
