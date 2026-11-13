from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from json import JSONDecodeError
from urllib.parse import urlsplit, urlunsplit

import requests


logger = logging.getLogger(__name__)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SYMBOL_SELECTOR = "0x95d89b41"
DECIMALS_SELECTOR = "0x313ce567"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

DEFAULT_LOGS_CHUNK_BLOCKS = 300
MIN_LOGS_CHUNK_BLOCKS = 10
DEFAULT_BATCH_SIZE = 10
DEFAULT_MAX_CATCHUP_BLOCKS = 50_000
DEFAULT_NATIVE_SCAN_MAX_BLOCKS = 3_000
ADDRESSES_PER_LOGS_REQUEST = 50
MAX_TIMESTAMP_LOOKUPS = 200


class RpcError(RuntimeError):
    pass


@dataclass
class RpcScanResult:
    # (wallet address in lower case, "native" | "erc20") -> events, newest first
    events: dict
    cursor: dict
    initialized: bool = False


class RpcProvider:
    """Scans a network once per cycle for all wallets, starting from the last scanned block.

    Outgoing ERC-20 transfers come from eth_getLogs over the new block range.
    Outgoing transactions are detected by the wallet nonce: blocks are only
    downloaded when a nonce has actually increased.
    """

    def __init__(self, timeout: int = 20, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.token_cache: dict[tuple[int, str], dict] = {}
        self._no_batch: set[str] = set()
        self._logs_chunk: dict[str, int] = {}

    # ------------------------------------------------------------------ public

    def urls(self, network: dict) -> list[str]:
        urls = []
        env_name = network.get("rpc_url_env", "")
        if env_name:
            urls.extend(item.strip() for item in os.getenv(env_name, "").split(",") if item.strip())
        public_url = network.get("rpc_url", "")
        if public_url:
            urls.append(public_url)
        return urls

    def scan(self, network: dict, addresses: list[str], cursor: dict | None) -> RpcScanResult:
        urls = self.urls(network)
        if not urls:
            raise RpcError(f"{network.get('rpc_url_env') or 'rpc_url'} is not configured")

        normalized = sorted({address.lower() for address in addresses})
        last_error: RpcError | None = None
        for url in urls:
            try:
                return self._scan(url, network, normalized, cursor or {})
            except RpcError as error:
                last_error = error
                if len(urls) > 1:
                    logger.warning("RPC endpoint failed for %s (%s); trying next", network.get("name"), error)
        raise last_error or RpcError("RPC scan failed")

    def diagnostic_network(self, network: dict) -> dict:
        urls = self.urls(network)
        if not urls:
            message = f"{network.get('rpc_url_env') or 'rpc_url'} is not configured"
            return _diagnostic(network, False, False, message, endpoint="not configured")

        url = urls[0]
        endpoint = _mask_rpc_url(url)
        try:
            latest = self._latest_block(url)
            chunk = self._chunk_size(url, network)
            self._get_logs(url, network, [ZERO_ADDRESS], max(0, latest - chunk + 1), latest)
            self._nonces(url, [ZERO_ADDRESS, ZERO_ADDRESS])
            batch = "no" if url in self._no_batch else "yes"
            message = (
                f"OK, latest block {latest}, eth_getLogs range {self._chunk_size(url, network)} blocks, "
                f"batch requests: {batch}"
            )
            return _diagnostic(network, True, True, message, endpoint=endpoint)
        except Exception as error:
            message = str(error)[:180]
            return _diagnostic(
                network,
                False,
                True,
                message,
                endpoint=endpoint,
                auth_error=_is_auth_error(message),
                rate_limit_error=_is_rate_limit_error(message),
            )

    # ---------------------------------------------------------------- scanning

    def _scan(self, url: str, network: dict, addresses: list[str], cursor: dict) -> RpcScanResult:
        # Nonces first, block number second: the block range then covers every
        # transaction the nonces already reflect.
        nonces_now = self._nonces(url, addresses)
        latest = self._latest_block(url)

        stored = {a: n for a, n in (cursor.get("nonces") or {}).items() if isinstance(n, int)}
        retried = cursor.get("unlocated") or {}
        last_block = cursor.get("last_block")

        if not isinstance(last_block, int):
            return RpcScanResult({}, {"last_block": latest, "nonces": nonces_now}, initialized=True)

        if latest <= last_block:
            nonces = {a: stored.get(a, nonces_now[a]) for a in addresses}
            unlocated = {a: v for a, v in retried.items() if a in nonces}
            return RpcScanResult({}, _cursor(last_block, nonces, unlocated))

        from_block = last_block + 1
        max_catchup = int(network.get("rpc_max_catchup_blocks", DEFAULT_MAX_CATCHUP_BLOCKS))
        if latest - from_block + 1 > max_catchup:
            logger.warning(
                "%s: %s blocks behind, only the last %s are scanned for token transfers",
                network.get("name"),
                latest - from_block + 1,
                max_catchup,
            )
            from_block = latest - max_catchup + 1

        known = [a for a in addresses if a in stored]
        events: dict = {}
        new_nonces = dict(nonces_now)
        new_unlocated: dict = {}

        if known:
            for address, items in self._erc20_events(url, network, known, from_block, latest).items():
                events[(address, "erc20")] = items

            changed = {a: nonces_now[a] - stored[a] for a in known if nonces_now[a] > stored[a]}
            if changed:
                found, complete = self._native_events(url, network, changed, from_block, latest)
                for address in changed:
                    items = found.get(address, [])
                    located = {item.pop("_nonce") for item in items}
                    top = max(located, default=-1) + 1
                    missing = [n for n in range(stored[address], nonces_now[address]) if n not in located]
                    new_nonces[address] = max(nonces_now[address], top)

                    if missing and complete and address not in retried:
                        # The node may have answered the nonce from a newer block
                        # than eth_blockNumber. Look again in the next cycle.
                        new_nonces[address] = missing[0]
                        new_unlocated[address] = 1
                    elif missing:
                        items.insert(0, _unlocated_event(network, address, len(missing), nonces_now[address]))

                    if items:
                        events[(address, "native")] = items

        return RpcScanResult(events, _cursor(latest, new_nonces, new_unlocated))

    def _erc20_events(self, url: str, network: dict, addresses: list[str], from_block: int, to_block: int) -> dict:
        wallet_set = set(addresses)
        logs = []
        for start in range(0, len(addresses), ADDRESSES_PER_LOGS_REQUEST):
            group = addresses[start : start + ADDRESSES_PER_LOGS_REQUEST]
            logs.extend(self._get_logs(url, network, group, from_block, to_block))

        transfers = []
        for log in logs:
            topics = log.get("topics") or []
            # ERC-20 Transfer has exactly 3 topics; ERC-721 has 4 and an empty data field.
            if len(topics) != 3 or log.get("removed"):
                continue
            sender = _topic_to_address(topics[1])
            if sender in wallet_set:
                transfers.append((sender, log))
        if not transfers:
            return {}

        timestamps = self._log_timestamps(url, [log for _, log in transfers])
        result: dict = {}
        for sender, log in transfers:
            token = self._token_metadata(network, url, log.get("address", ""))
            block_number = _hex_to_int(log.get("blockNumber"))
            tx_hash = log.get("transactionHash", "")
            result.setdefault(sender, []).append(
                {
                    "event_type": "erc20",
                    "tx_hash": tx_hash,
                    "event_id": f"{tx_hash}:{_hex_to_int(log.get('logIndex'))}",
                    "from_address": sender,
                    "to_address": _topic_to_address(log["topics"][2]),
                    "asset": token["symbol"],
                    "amount": _format_units(_hex_to_int((log.get("data") or "0x")[:66]), token["decimals"]),
                    "timestamp": timestamps.get(block_number),
                    "_order": (block_number, _hex_to_int(log.get("logIndex"))),
                }
            )
        for items in result.values():
            items.sort(key=lambda item: item["_order"], reverse=True)
            for item in items:
                del item["_order"]
        return result

    def _native_events(
        self, url: str, network: dict, changed: dict[str, int], from_block: int, to_block: int
    ) -> tuple[dict, bool]:
        max_blocks = int(network.get("rpc_native_scan_max_blocks", DEFAULT_NATIVE_SCAN_MAX_BLOCKS))
        complete = True
        if to_block - from_block + 1 > max_blocks:
            from_block = to_block - max_blocks + 1
            complete = False

        batch_size = int(network.get("rpc_batch_size", DEFAULT_BATCH_SIZE))
        expected = sum(changed.values())
        located = 0
        found: dict = {}
        numbers = list(range(to_block, from_block - 1, -1))
        for start in range(0, len(numbers), batch_size):
            group = numbers[start : start + batch_size]
            blocks = self._batch(url, [("eth_getBlockByNumber", [hex(number), True]) for number in group])
            for block in blocks:
                if not isinstance(block, dict):
                    raise RpcError("Block is not available on the RPC node yet")
                timestamp = _hex_to_int(block.get("timestamp"))
                for tx in reversed(block.get("transactions") or []):
                    sender = (tx.get("from") or "").lower()
                    if sender not in changed:
                        continue
                    located += 1
                    found.setdefault(sender, []).append(
                        {
                            "event_type": "native",
                            "tx_hash": tx.get("hash", ""),
                            "from_address": sender,
                            "to_address": tx.get("to") or "contract creation",
                            "asset": network["native_symbol"],
                            "amount": _format_units(_hex_to_int(tx.get("value")), 18),
                            "timestamp": timestamp,
                            "_nonce": _hex_to_int(tx.get("nonce")),
                        }
                    )
            if located >= expected:
                break
        return found, complete

    def _get_logs(self, url: str, network: dict, addresses: list[str], from_block: int, to_block: int) -> list[dict]:
        topics = [TRANSFER_TOPIC, [_address_topic(address) for address in addresses]]
        logs: list[dict] = []
        start = from_block
        while start <= to_block:
            chunk = self._chunk_size(url, network)
            end = min(start + chunk - 1, to_block)
            try:
                part = self._rpc(url, "eth_getLogs", [{"fromBlock": hex(start), "toBlock": hex(end), "topics": topics}])
            except RpcError as error:
                message = str(error)
                if chunk <= MIN_LOGS_CHUNK_BLOCKS or _is_auth_error(message) or _is_rate_limit_error(message):
                    raise
                # Most RPC plans cap the eth_getLogs block range; shrink and remember.
                self._logs_chunk[url] = max(MIN_LOGS_CHUNK_BLOCKS, chunk // 2)
                continue
            logs.extend(part or [])
            start = end + 1
        return logs

    def _chunk_size(self, url: str, network: dict) -> int:
        return self._logs_chunk.get(url) or int(network.get("rpc_logs_chunk_blocks", DEFAULT_LOGS_CHUNK_BLOCKS))

    def _log_timestamps(self, url: str, logs: list[dict]) -> dict[int, int]:
        timestamps: dict[int, int] = {}
        missing: list[int] = []
        for log in logs:
            number = _hex_to_int(log.get("blockNumber"))
            if log.get("blockTimestamp"):
                timestamps[number] = _hex_to_int(log["blockTimestamp"])
            elif number not in timestamps and number not in missing:
                missing.append(number)
        missing = [number for number in missing if number not in timestamps][:MAX_TIMESTAMP_LOOKUPS]
        for start in range(0, len(missing), 50):
            group = missing[start : start + 50]
            blocks = self._batch(url, [("eth_getBlockByNumber", [hex(number), False]) for number in group])
            for number, block in zip(group, blocks):
                if isinstance(block, dict):
                    timestamps[number] = _hex_to_int(block.get("timestamp"))
        return timestamps

    def _nonces(self, url: str, addresses: list[str]) -> dict[str, int]:
        nonces: dict[str, int] = {}
        for start in range(0, len(addresses), 50):
            group = addresses[start : start + 50]
            results = self._batch(url, [("eth_getTransactionCount", [address, "latest"]) for address in group])
            for address, result in zip(group, results):
                nonces[address] = _hex_to_int(result)
        return nonces

    def _latest_block(self, url: str) -> int:
        return _hex_to_int(self._rpc(url, "eth_blockNumber", []))

    def _token_metadata(self, network: dict, url: str, token_address: str) -> dict:
        key = (int(network["chain_id"]), token_address.lower())
        if key in self.token_cache:
            return self.token_cache[key]

        try:
            symbol = _decode_abi_string(self._rpc(url, "eth_call", [{"to": token_address, "data": SYMBOL_SELECTOR}, "latest"]))
        except (RpcError, ValueError):
            symbol = ""
        try:
            decimals = _hex_to_int(self._rpc(url, "eth_call", [{"to": token_address, "data": DECIMALS_SELECTOR}, "latest"]))
            if decimals > 77:
                decimals = 0
        except (RpcError, ValueError):
            decimals = 18
        token = {"symbol": symbol or _short_token(token_address), "decimals": decimals}
        self.token_cache[key] = token
        return token

    # --------------------------------------------------------------- transport

    def _rpc(self, url: str, method: str, params: list):
        data = self._post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(data, dict):
            raise RpcError("Unexpected RPC response format")
        return _result(data)

    def _batch(self, url: str, calls: list[tuple[str, list]]) -> list:
        if not calls:
            return []
        if len(calls) == 1 or url in self._no_batch:
            return [self._rpc(url, method, params) for method, params in calls]

        payload = [
            {"jsonrpc": "2.0", "id": index, "method": method, "params": params}
            for index, (method, params) in enumerate(calls)
        ]
        try:
            data = self._post(url, payload)
        except RpcError as error:
            if _is_auth_error(str(error)) or _is_rate_limit_error(str(error)):
                raise
            data = None
        if not isinstance(data, list) or len(data) != len(calls):
            # Endpoint does not support batch requests; use single calls from now on.
            self._no_batch.add(url)
            return [self._rpc(url, method, params) for method, params in calls]

        by_id = {item.get("id"): item for item in data if isinstance(item, dict)}
        if len(by_id) != len(calls):
            raise RpcError("Unexpected RPC batch response")
        return [_result(by_id[index]) for index in range(len(calls))]

    def _post(self, url: str, payload):
        for attempt in range(3):
            last = attempt == 2
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as error:
                if not last:
                    time.sleep(1 + attempt)
                    continue
                raise RpcError(f"Request failed: {error.__class__.__name__}") from error

            if (response.status_code == 429 or response.status_code >= 500) and not last:
                time.sleep(_retry_after(response, default=1 + attempt))
                continue

            try:
                data = response.json()
            except (JSONDecodeError, ValueError):
                data = None

            if response.status_code == 429:
                raise RpcError("Rate limit: HTTP 429")
            if response.status_code in (401, 403):
                raise RpcError(f"Auth error: HTTP {response.status_code}")
            if not response.ok:
                detail = _error_message(data)
                raise RpcError(f"HTTP status {response.status_code}" + (f": {detail}" if detail else ""))
            if data is None:
                raise RpcError("Invalid JSON response")
            return data
        raise RpcError("Request failed")


def _cursor(last_block: int, nonces: dict, unlocated: dict) -> dict:
    cursor = {"last_block": last_block, "nonces": nonces}
    if unlocated:
        cursor["unlocated"] = unlocated
    return cursor


def _unlocated_event(network: dict, address: str, count: int, nonce: int) -> dict:
    return {
        "event_type": "native",
        "tx_hash": "",
        "event_id": f"nonce:{address}:{nonce}",
        "from_address": address,
        "to_address": "unknown",
        "asset": network["native_symbol"],
        "amount": "unknown",
        "timestamp": None,
        "note": (
            f"Wallet nonce increased by {count} (now {nonce}), but the transaction was not found "
            "in the scanned blocks. Open the address in the explorer."
        ),
    }


def _result(item: dict):
    if "error" in item and item["error"]:
        raise RpcError(_error_message(item) or "RPC error")
    return item.get("result")


def _error_message(data) -> str:
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message", "RPC error"))[:180]
    return str(error)[:180] if error else ""


def _diagnostic(
    network: dict,
    ok: bool,
    response_received: bool,
    message: str,
    endpoint: str,
    auth_error: bool = False,
    rate_limit_error: bool = False,
) -> dict:
    return {
        "network": network.get("name", "Unknown"),
        "provider": "rpc",
        "chain_id": network.get("chain_id"),
        "endpoint": endpoint,
        "api_key": "inside RPC URL" if network.get("rpc_url_env") else "public RPC",
        "response_received": response_received,
        "http_status": 200 if ok else None,
        "auth_error": auth_error,
        "plan_or_unsupported_error": _is_unsupported_error(message),
        "rate_limit_error": rate_limit_error,
        "ok": ok,
        "message": message,
    }


def _hex_to_int(value) -> int:
    if isinstance(value, int):
        return value
    if not value or value == "0x":
        return 0
    return int(value, 16)


def _format_units(value: int, decimals: int) -> str:
    amount = Decimal(value) / (Decimal(10) ** decimals)
    formatted = format(amount.normalize(), "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _address_topic(address: str) -> str:
    return "0x" + address.lower().replace("0x", "").rjust(64, "0")


def _topic_to_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def _decode_abi_string(value: str) -> str:
    if not value or value == "0x":
        return ""

    raw = bytes.fromhex(value.replace("0x", ""))
    if len(raw) == 32:
        return raw.rstrip(b"\x00").decode("utf-8", errors="ignore").strip()
    if len(raw) >= 96:
        length = int.from_bytes(raw[32:64], "big")
        return raw[64 : 64 + min(length, 64)].decode("utf-8", errors="ignore").strip()
    return ""


def _short_token(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}" if len(address) > 12 else address


def _mask_rpc_url(url: str) -> str:
    parts = urlsplit(url)
    path_parts = parts.path.split("/")
    if path_parts and len(path_parts[-1]) > 8:
        secret = path_parts[-1]
        path_parts[-1] = f"{secret[:4]}...{secret[-4:]}"
    return urlunsplit((parts.scheme, parts.netloc, "/".join(path_parts), "", ""))


def _retry_after(response: requests.Response, default: int) -> int:
    value = response.headers.get("Retry-After")
    if value and value.isdigit():
        return min(int(value), 30)
    return default


def _is_auth_error(message: str) -> bool:
    normalized = message.lower()
    return any(marker in normalized for marker in ("auth", "unauthorized", "forbidden", "apikey", "api key"))


def _is_rate_limit_error(message: str) -> bool:
    normalized = message.lower()
    return "rate limit" in normalized or "too many requests" in normalized


def _is_unsupported_error(message: str) -> bool:
    normalized = message.lower()
    return "unsupported" in normalized or "not supported" in normalized
