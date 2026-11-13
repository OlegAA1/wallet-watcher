from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services.providers import blockscout as blockscout_module
from services.providers.blockscout import BlockscoutProvider
from services.providers.etherscan_v2 import EtherscanV2Provider
from services.providers.rpc import TRANSFER_TOPIC, RpcError, RpcProvider
from services.scanner import WalletScanner
from services.storage import StateStorage


WALLET = "0x" + "a1" * 20
OTHER = "0x" + "b2" * 20
TOKEN = "0x" + "c3" * 20
NETWORK = {
    "name": "TestNet",
    "chain_id": 999,
    "native_symbol": "ETH",
    "explorer_url": "https://explorer.test",
    "api_provider": "rpc",
    "rpc_url": "https://rpc.test",
}
SETTINGS = {
    "etherscan_api_keys": ["key"],
    "blockscout_api_keys": [],
    "request_timeout_seconds": 5,
    "fetch_limit": 50,
    "max_event_age_seconds": 86400,
    "network_failure_alert_cycles": 3,
    "etherscan_calls_per_second": 0,
}


def topic(address: str) -> str:
    return "0x" + address[2:].rjust(64, "0")


class FakeChain(RpcProvider):
    """RpcProvider with the HTTP layer replaced by an in-memory chain."""

    def __init__(self, max_logs_range: int = 10_000, batch: bool = True):
        super().__init__()
        self.latest = 100
        self.nonces: dict[str, int] = {}
        self.blocks: dict[int, list[dict]] = {}
        self.logs: list[dict] = []
        self.max_logs_range = max_logs_range
        self.batch = batch
        self.calls: list[str] = []

    def add_tx(self, block: int, sender: str, tx_hash: str, value: int = 10**18) -> None:
        nonce = self.nonces.get(sender, 0)
        self.blocks.setdefault(block, []).append(
            {"hash": tx_hash, "from": sender, "to": OTHER, "value": hex(value), "nonce": hex(nonce)}
        )
        self.nonces[sender] = nonce + 1

    def add_log(self, block: int, sender: str, tx_hash: str, index: int, amount: int = 5 * 10**6, nft: bool = False) -> None:
        topics = [TRANSFER_TOPIC, topic(sender), topic(OTHER)]
        self.logs.append(
            {
                "address": TOKEN,
                "topics": topics + ([hex(7)] if nft else []),
                "data": "0x" if nft else hex(amount),
                "blockNumber": hex(block),
                "transactionHash": tx_hash,
                "logIndex": hex(index),
            }
        )

    def _post(self, url, payload):
        if isinstance(payload, list):
            if not self.batch:
                raise RpcError("HTTP status 400: batch is not supported")
            return [self._answer(item) for item in payload]
        return self._answer(payload)

    def _answer(self, item: dict) -> dict:
        method, params = item["method"], item["params"]
        self.calls.append(method)
        result = None
        if method == "eth_blockNumber":
            result = hex(self.latest)
        elif method == "eth_getTransactionCount":
            result = hex(self.nonces.get(params[0], 0))
        elif method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            result = {"timestamp": hex(int(time.time())), "transactions": self.blocks.get(number, [])}
        elif method == "eth_getLogs":
            start, end = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            if end - start + 1 > self.max_logs_range:
                return {"id": item["id"], "error": {"message": "block range too large"}}
            senders = set(params[0]["topics"][1])
            result = [
                log
                for log in self.logs
                if start <= int(log["blockNumber"], 16) <= end and log["topics"][1] in senders
            ]
        elif method == "eth_call":
            if params[0]["data"] == "0x313ce567":
                result = hex(6)
            else:
                result = "0x" + (32).to_bytes(32, "big").hex() + (4).to_bytes(32, "big").hex() + b"USDC".ljust(32, b"\0").hex()
        return {"id": item["id"], "result": result}


class FakeNotifier:
    def __init__(self):
        self.ok = True
        self.alerts: list[dict] = []
        self.messages: list[str] = []

    def send_alert(self, event: dict) -> bool:
        if self.ok:
            self.alerts.append(event)
        return self.ok

    def send_message(self, text: str) -> bool:
        if self.ok:
            self.messages.append(text)
        return self.ok


class RpcProviderTests(unittest.TestCase):
    def test_first_scan_only_saves_a_baseline(self):
        chain = FakeChain()
        chain.add_tx(90, WALLET, "0xold")
        result = chain.scan(NETWORK, [WALLET], {})
        self.assertTrue(result.initialized)
        self.assertEqual(result.events, {})
        self.assertEqual(result.cursor, {"last_block": 100, "nonces": {WALLET: 1}})

    def test_finds_native_tx_and_erc20_transfers_since_last_block(self):
        chain = FakeChain()
        cursor = chain.scan(NETWORK, [WALLET], {}).cursor
        chain.add_tx(150, WALLET, "0xnative")
        chain.add_log(160, WALLET, "0xmulti", 1)
        chain.add_log(160, WALLET, "0xmulti", 2, amount=2 * 10**6)
        chain.add_log(170, WALLET, "0xnft", 3, nft=True)
        chain.add_log(170, OTHER, "0xforeign", 4)
        chain.latest = 2000  # far more than the old fixed window of 300 blocks

        result = chain.scan(NETWORK, [WALLET], cursor)

        native = result.events[(WALLET, "native")]
        self.assertEqual([event["tx_hash"] for event in native], ["0xnative"])
        self.assertEqual(native[0]["amount"], "1")
        erc20 = result.events[(WALLET, "erc20")]
        self.assertEqual([event["event_id"] for event in erc20], ["0xmulti:2", "0xmulti:1"])
        self.assertEqual([event["amount"] for event in erc20], ["2", "5"])
        self.assertEqual(erc20[0]["asset"], "USDC")
        self.assertEqual(result.cursor["last_block"], 2000)
        self.assertEqual(result.cursor["nonces"][WALLET], 1)

    def test_blocks_are_not_downloaded_when_nonce_is_unchanged(self):
        chain = FakeChain()
        cursor = chain.scan(NETWORK, [WALLET], {}).cursor
        chain.latest = 5000
        chain.calls.clear()
        chain.scan(NETWORK, [WALLET], cursor)
        self.assertNotIn("eth_getBlockByNumber", chain.calls)

    def test_nonce_increase_without_located_tx_alerts_after_one_retry(self):
        chain = FakeChain()
        cursor = chain.scan(NETWORK, [WALLET], {}).cursor
        chain.nonces[WALLET] = 1  # transaction exists, but is in none of the scanned blocks
        chain.latest = 110
        first = chain.scan(NETWORK, [WALLET], cursor)
        self.assertEqual(first.events, {})
        chain.latest = 120
        second = chain.scan(NETWORK, [WALLET], first.cursor)
        event = second.events[(WALLET, "native")][0]
        self.assertEqual(event["tx_hash"], "")
        self.assertIn("nonce increased by 1", event["note"])
        chain.latest = 130
        self.assertEqual(chain.scan(NETWORK, [WALLET], second.cursor).events, {})

    def test_logs_range_is_reduced_when_the_node_rejects_it(self):
        chain = FakeChain(max_logs_range=80)
        cursor = chain.scan(NETWORK, [WALLET], {}).cursor
        chain.add_log(450, WALLET, "0xlate", 0)
        chain.latest = 700
        result = chain.scan(NETWORK, [WALLET], cursor)
        self.assertEqual(len(result.events[(WALLET, "erc20")]), 1)

    def test_falls_back_to_single_calls_without_batch_support(self):
        chain = FakeChain(batch=False)
        cursor = chain.scan(NETWORK, [WALLET, OTHER], {}).cursor
        chain.add_tx(105, WALLET, "0xnative")
        chain.latest = 110
        result = chain.scan(NETWORK, [WALLET, OTHER], cursor)
        self.assertEqual(len(result.events[(WALLET, "native")]), 1)

    def test_failed_scan_raises_and_keeps_the_cursor(self):
        chain = FakeChain(max_logs_range=1)
        cursor = chain.scan(NETWORK, [WALLET], {}).cursor
        chain.latest = 500
        with self.assertRaises(RpcError):
            chain.scan(NETWORK, [WALLET], cursor)


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "state.json"
        self.notifier = FakeNotifier()
        self.wallets = [{"name": "Main", "address": WALLET}]

    def scanner(self, networks, state: dict | None = None) -> WalletScanner:
        storage = StateStorage(self.state_path)
        if state:
            storage.state.update(state)
        return WalletScanner(SETTINGS, self.wallets, networks, storage, self.notifier)

    def test_rpc_alert_survives_a_telegram_outage_and_is_sent_once(self):
        scanner = self.scanner([NETWORK])
        chain = FakeChain()
        scanner.providers["rpc"] = chain
        scanner.run_once()  # baseline
        chain.add_tx(105, WALLET, "0xnative")
        chain.latest = 110

        self.notifier.ok = False
        scanner.run_once()
        self.assertEqual(self.notifier.alerts, [])
        self.assertEqual(len(StateStorage(self.state_path).pending_alerts()), 1)

        self.notifier.ok = True
        chain.latest = 120
        scanner.run_once()
        scanner.run_once()
        self.assertEqual([alert["tx_hash"] for alert in self.notifier.alerts], ["0xnative"])
        self.assertEqual(StateStorage(self.state_path).pending_alerts(), [])

    def test_rpc_rescan_of_the_same_range_does_not_duplicate_alerts(self):
        scanner = self.scanner([NETWORK])
        chain = FakeChain()
        scanner.providers["rpc"] = chain
        scanner.run_once()
        cursor = dict(scanner.storage.rpc_cursor("TestNet"))
        chain.add_log(105, WALLET, "0xtoken", 0)
        chain.latest = 110
        scanner.run_once()
        scanner.storage.set_rpc_cursor("TestNet", cursor)  # as if the process died before saving the cursor
        scanner.run_once()
        self.assertEqual(len(self.notifier.alerts), 1)

    def test_two_token_transfers_in_one_transaction_give_two_alerts(self):
        network = {**NETWORK, "api_provider": "etherscan_v2"}
        now = str(int(time.time()))
        rows = [
            {"hash": "0xmulti", "from": WALLET, "to": OTHER, "value": "1000000", "tokenDecimal": "6",
             "tokenSymbol": "USDC", "contractAddress": TOKEN, "timeStamp": now},
            {"hash": "0xmulti", "from": WALLET, "to": OTHER, "value": "2000000", "tokenDecimal": "6",
             "tokenSymbol": "USDC", "contractAddress": TOKEN, "timeStamp": now},
        ]
        scanner = self.scanner([network], {f"TestNet:{WALLET}:native": [], f"TestNet:{WALLET}:erc20": ["0xlegacy"]})
        provider = scanner.providers["etherscan_v2"]
        legacy = {**rows[0], "hash": "0xlegacy"}
        with mock.patch.object(provider, "_request", side_effect=lambda net, addr, action: rows + [legacy] if action == "tokentx" else []):
            scanner.run_once()
            scanner.run_once()
        self.assertEqual([alert["amount"] for alert in self.notifier.alerts], ["2", "1"])

    def test_network_failure_is_reported_once_and_recovery_too(self):
        scanner = self.scanner([NETWORK])
        chain = FakeChain()
        scanner.providers["rpc"] = chain
        with mock.patch.object(chain, "scan", side_effect=RpcError("HTTP status 503")):
            for _ in range(5):
                scanner.run_once()
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertIn("TestNet is failing", self.notifier.messages[0])
        scanner.run_once()
        self.assertIn("working again", self.notifier.messages[-1])
        self.assertEqual(len(self.notifier.messages), 2)

    def test_invalid_wallet_address_is_skipped(self):
        self.wallets.append({"name": "Typo", "address": "0x123"})
        scanner = self.scanner([NETWORK])
        self.assertEqual([wallet["name"] for wallet in scanner._enabled_wallets()], ["Main"])


class ProviderTests(unittest.TestCase):
    def test_blockscout_does_not_send_limit_parameter(self):
        seen = {}

        def fake_get_json(url, *, params=None, headers=None, timeout=20, retries=2):
            seen[url] = params
            return {"items": []}

        network = {"name": "Scroll", "native_symbol": "ETH", "api_base_url": "https://scrollscan.com/api/v2"}
        with mock.patch.object(blockscout_module, "get_json", fake_get_json):
            provider = BlockscoutProvider()
            provider.get_native_transactions(network, WALLET)
            provider.get_token_transfers(network, WALLET)
        self.assertEqual(len(seen), 2)
        for params in seen.values():
            self.assertNotIn("limit", params)

    def test_blockscout_token_transfer_id_includes_log_index(self):
        transfer = {
            "transaction_hash": "0xabc", "log_index": 7, "from": {"hash": WALLET}, "to": {"hash": OTHER},
            "token": {"symbol": "USDC", "type": "ERC-20"}, "total": {"value": "1500000", "decimals": "6"},
        }
        event = BlockscoutProvider()._normalize_token_transfer(transfer, {"native_symbol": "ETH"})
        self.assertEqual(event["event_id"], "0xabc:7")
        self.assertEqual(event["amount"], "1.5")

    def test_etherscan_marks_reverted_transactions(self):
        provider = EtherscanV2Provider(api_key="key", calls_per_second=0)
        event = provider._normalize_native({"hash": "0x1", "from": WALLET, "to": OTHER, "value": "0", "isError": "1"}, NETWORK)
        self.assertIn("FAILED", event["status"])


class StorageTests(unittest.TestCase):
    def test_state_round_trip_keeps_meta_and_legacy_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            storage = StateStorage(path)
            storage.add("Net", WALLET, "erc20", "0xLEGACY")
            storage.set_rpc_cursor("Net", {"last_block": 5, "nonces": {WALLET: 2}})
            storage.pending_alerts().append({"tx_hash": "0x1"})
            storage.save()

            loaded = StateStorage(path)
            self.assertTrue(loaded.has_seen("Net", WALLET, "erc20", "0xlegacy:abc", legacy_hash="0xlegacy"))
            self.assertFalse(loaded.has_seen("Net", WALLET, "erc20", "0xnew:abc", legacy_hash="0xnew"))
            self.assertEqual(loaded.rpc_cursor("Net")["last_block"], 5)
            self.assertEqual(loaded.pending_alerts(), [{"tx_hash": "0x1"}])


if __name__ == "__main__":
    unittest.main()
