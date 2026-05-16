from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import requests


logger = logging.getLogger(__name__)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SYMBOL_SELECTOR = "0x95d89b41"
DECIMALS_SELECTOR = "0x313ce567"


class BlockPiRpcProvider:
    def __init__(self, rpc_urls: dict[str, str | Sequence[str]], timeout: int = 20, limit: int = 50):
        self.rpc_urls = rpc_urls
        self.timeout = timeout
        self.limit = limit
        self.token_cache: dict[tuple[int, str], dict] = {}

    def get_native_transactions(self, network: dict, address: str) -> list[dict]:
        url = self._rpc_url(network, address)
        latest_block = self._latest_block(url)
        from_block = max(0, latest_block - int(network.get("rpc_scan_blocks", 300)) + 1)
        normalized_address = address.lower()
        events = []

        for block_number in range(latest_block, from_block - 1, -1):
            block = self._rpc(
                url,
                "eth_getBlockByNumber",
                [hex(block_number), True],
            )
            for tx in block.get("transactions", []):
                if tx.get("from", "").lower() != normalized_address:
                    continue
                events.append(
                    {
                        "event_type": "native",
                        "tx_hash": tx.get("hash", ""),
                        "from_address": tx.get("from", ""),
                        "to_address": tx.get("to", ""),
                        "asset": network["native_symbol"],
                        "amount": _format_units(_hex_to_int(tx.get("value", "0x0")), 18),
                    }
                )
                if len(events) >= self.limit:
                    return events
        return events

    def get_token_transfers(self, network: dict, address: str) -> list[dict]:
        url = self._rpc_url(network, address)
        latest_block = self._latest_block(url)
        from_block = max(0, latest_block - int(network.get("rpc_scan_blocks", 300)) + 1)
        logs = self._rpc(
            url,
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(from_block),
                    "toBlock": hex(latest_block),
                    "topics": [TRANSFER_TOPIC, _address_topic(address)],
                }
            ],
        )
        events = []
        for log in reversed(logs):
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue
            token_address = log.get("address", "")
            token = self._token_metadata(network, url, token_address)
            events.append(
                {
                    "event_type": "erc20",
                    "tx_hash": log.get("transactionHash", ""),
                    "from_address": _topic_to_address(topics[1]),
                    "to_address": _topic_to_address(topics[2]),
                    "asset": token["symbol"],
                    "amount": _format_units(_hex_to_int(log.get("data", "0x0")), token["decimals"]),
                }
            )
            if len(events) >= self.limit:
                break
        return events

    def test_network(self, network: dict) -> tuple[bool, str]:
        diagnostic = self.diagnostic_network(network)
        return diagnostic["ok"], diagnostic["message"]

    def diagnostic_network(self, network: dict) -> dict:
        try:
            url = self._rpc_url(network)
        except ValueError as error:
            return _diagnostic(network, False, None, False, str(error), endpoint="not configured")

        try:
            block_number = self._rpc(url, "eth_blockNumber", [])
            return _diagnostic(network, True, 200, True, f"OK, latest block {int(block_number, 16)}", endpoint=_mask_rpc_url(url))
        except Exception as error:
            message = str(error)
            return _diagnostic(
                network,
                False,
                None,
                True,
                message,
                endpoint=_mask_rpc_url(url),
                auth_error=_is_auth_error(message),
                rate_limit_error=_is_rate_limit_error(message),
            )

    def _rpc_url(self, network: dict, address: str = "") -> str:
        env_name = network.get("rpc_url_env", "")
        urls = self._urls(env_name)
        if not urls:
            raise ValueError(f"{env_name or 'RPC URL'} is not configured")
        if not address:
            return urls[0]
        normalized = address.lower().replace("0x", "")
        try:
            index = int(normalized[-8:] or "0", 16) % len(urls)
        except ValueError:
            index = 0
        return urls[index]

    def _urls(self, env_name: str) -> list[str]:
        value = self.rpc_urls.get(env_name, [])
        if isinstance(value, str):
            return [value] if value else []
        return [url for url in value if url]

    def _latest_block(self, url: str) -> int:
        return int(self._rpc(url, "eth_blockNumber", []), 16)

    def _token_metadata(self, network: dict, url: str, token_address: str) -> dict:
        key = (int(network["chain_id"]), token_address.lower())
        if key in self.token_cache:
            return self.token_cache[key]

        symbol = self._read_token_symbol(url, token_address) or _short_token(token_address)
        decimals = self._read_token_decimals(url, token_address)
        token = {"symbol": symbol, "decimals": decimals}
        self.token_cache[key] = token
        return token

    def _read_token_symbol(self, url: str, token_address: str) -> str:
        try:
            result = self._rpc(url, "eth_call", [{"to": token_address, "data": SYMBOL_SELECTOR}, "latest"])
            return _decode_abi_string(result)
        except Exception:
            return ""

    def _read_token_decimals(self, url: str, token_address: str) -> int:
        try:
            result = self._rpc(url, "eth_call", [{"to": token_address, "data": DECIMALS_SELECTOR}, "latest"])
            return _hex_to_int(result)
        except Exception:
            return 18

    def _rpc(self, url: str, method: str, params: list):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as error:
            raise RuntimeError(f"Request failed: {error.__class__.__name__}") from error

        if response.status_code == 429:
            raise RuntimeError("Rate limit: HTTP 429")
        if response.status_code in (401, 403):
            raise RuntimeError(f"Auth error: HTTP {response.status_code}")
        if not response.ok:
            raise RuntimeError(f"HTTP status {response.status_code}")

        data = response.json()
        if "error" in data:
            error = data["error"]
            message = error.get("message", "RPC error") if isinstance(error, dict) else str(error)
            raise RuntimeError(message[:180])
        return data.get("result")


def _diagnostic(
    network: dict,
    ok: bool,
    http_status: int | None,
    response_received: bool,
    message: str,
    endpoint: str,
    auth_error: bool = False,
    rate_limit_error: bool = False,
) -> dict:
    return {
        "network": network.get("name", "Unknown"),
        "provider": "blockpi_rpc",
        "chain_id": network.get("chain_id"),
        "endpoint": endpoint,
        "api_key": "inside RPC URL",
        "response_received": response_received,
        "http_status": http_status,
        "auth_error": auth_error,
        "plan_or_unsupported_error": _is_unsupported_error(message),
        "rate_limit_error": rate_limit_error,
        "ok": ok,
        "message": message,
    }


def _hex_to_int(value: str) -> int:
    return int(value or "0x0", 16)


def _format_units(value: int, decimals: int) -> str:
    amount = Decimal(value) / (Decimal(10) ** decimals)
    formatted = format(amount.normalize(), "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _address_topic(address: str) -> str:
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")


def _topic_to_address(topic: str) -> str:
    return "0x" + topic[-40:]


def _decode_abi_string(value: str) -> str:
    if not value or value == "0x":
        return ""

    raw = bytes.fromhex(value.replace("0x", ""))
    if len(raw) == 32:
        return raw.rstrip(b"\x00").decode("utf-8", errors="ignore").strip()
    if len(raw) >= 96:
        length = int.from_bytes(raw[32:64], "big")
        return raw[64 : 64 + length].decode("utf-8", errors="ignore").strip()
    return ""


def _short_token(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}" if len(address) > 12 else address


def _mask_rpc_url(url: str) -> str:
    parts = urlsplit(url)
    path_parts = parts.path.split("/")
    if path_parts and len(path_parts[-1]) > 8:
        secret = path_parts[-1]
        path_parts[-1] = f"{secret[:4]}...{secret[-4:]}"
    return urlunsplit((parts.scheme, parts.netloc, "/".join(path_parts), parts.query, parts.fragment))


def _is_auth_error(message: str) -> bool:
    normalized = message.lower()
    return "auth" in normalized or "unauthorized" in normalized or "forbidden" in normalized


def _is_rate_limit_error(message: str) -> bool:
    normalized = message.lower()
    return "rate limit" in normalized or "too many requests" in normalized


def _is_unsupported_error(message: str) -> bool:
    normalized = message.lower()
    return "unsupported" in normalized or "not supported" in normalized
