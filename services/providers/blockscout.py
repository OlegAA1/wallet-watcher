from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from services.providers.common import classify_diagnostic, get_json, mask_secret, request_json_diagnostic


logger = logging.getLogger(__name__)


class BlockscoutProvider:
    def __init__(self, api_key: str | Sequence[str] = "", timeout: int = 20, limit: int = 50):
        if isinstance(api_key, str):
            self.api_keys = [api_key] if api_key else []
        else:
            self.api_keys = [key for key in api_key if key]
        self.timeout = timeout
        self.limit = limit

    def get_native_transactions(self, network: dict, address: str) -> list[dict]:
        endpoint = network.get("transactions_endpoint", "/addresses/{address}/transactions")
        data = self._request(network, endpoint, address)
        items = self._items(data)[: self.limit]
        return [self._normalize_native(tx, network) for tx in items if tx.get("hash")]

    def get_token_transfers(self, network: dict, address: str) -> list[dict]:
        endpoint = network.get("token_transfers_endpoint", "/addresses/{address}/token-transfers")
        data = self._request(network, endpoint, address, params={"type": "ERC-20"})
        items = self._items(data)[: self.limit]
        return [
            self._normalize_token_transfer(tx, network)
            for tx in items
            if (tx.get("tx_hash") or tx.get("transaction_hash")) and _is_erc20(tx)
        ]

    def test_network(self, network: dict) -> tuple[bool, str]:
        diagnostic = self.diagnostic_network(network)
        return diagnostic["ok"], diagnostic["message"]

    def diagnostic_network(self, network: dict) -> dict:
        address = "0x0000000000000000000000000000000000000000"
        # Check both endpoints with exactly the requests the scanner sends.
        diagnostic: dict = {}
        for endpoint, params in (
            (network.get("transactions_endpoint", "/addresses/{address}/transactions"), None),
            (network.get("token_transfers_endpoint", "/addresses/{address}/token-transfers"), {"type": "ERC-20"}),
        ):
            diagnostic = self._diagnose(network, endpoint, address, params)
            if not diagnostic["ok"]:
                break
        return diagnostic

    def _diagnose(self, network: dict, endpoint: str, address: str, params: dict | None) -> dict:
        try:
            url, params, headers = self._build_request(network, endpoint, address, params)
        except Exception as error:
            return {
                "network": network.get("name", "Unknown"),
                "provider": "blockscout",
                "chain_id": network.get("chain_id"),
                "endpoint": network.get("api_base_url", "not configured"),
                "api_key": mask_secret(self._api_key(address)),
                "response_received": False,
                "auth_error": False,
                "plan_or_unsupported_error": False,
                "rate_limit_error": False,
                "ok": False,
                "message": str(error)[:180],
            }

        response = request_json_diagnostic(url, params=params, headers=headers, timeout=self.timeout)
        classification = classify_diagnostic(response["http_status"], response["json"], response["error"])
        return {
            "network": network.get("name", "Unknown"),
            "provider": "blockscout",
            "chain_id": network.get("chain_id"),
            "endpoint": url,
            "api_key": mask_secret(self._api_key(address)),
            "response_received": response["response_received"],
            "http_status": response["http_status"],
            **classification,
        }

    def _request(self, network: dict, endpoint: str, address: str, params: dict | None = None) -> dict | list:
        url, params, headers = self._build_request(network, endpoint, address, params)
        return get_json(url, params=params, headers=headers, timeout=self.timeout)

    def _build_request(
        self, network: dict, endpoint: str, address: str, params: dict | None = None
    ) -> tuple[str, dict, dict]:
        base_url = network.get("api_base_url")
        if not base_url:
            raise ValueError("api_base_url is missing in networks.json")

        path = endpoint.format(address=address)
        url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        # Blockscout returns a fixed page of 50 items; an extra "limit" parameter
        # is rejected with HTTP 422 by current versions.
        params = dict(params or {})
        headers = {}
        api_key = self._api_key(address)
        if api_key:
            header_name = network.get("api_key_header", "Authorization")
            header_prefix = network.get("api_key_prefix", "Bearer ")
            headers[header_name] = f"{header_prefix}{api_key}" if header_prefix else api_key
        return url, params, headers

    def _api_key(self, address: str) -> str:
        if not self.api_keys:
            return ""
        normalized = address.lower().replace("0x", "")
        try:
            index = int(normalized[-8:] or "0", 16) % len(self.api_keys)
        except ValueError:
            index = 0
        return self.api_keys[index]

    def _items(self, data: dict | list) -> list[dict]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "results", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _normalize_native(self, tx: dict, network: dict) -> dict:
        event = {
            "event_type": "native",
            "tx_hash": tx.get("hash", ""),
            "from_address": _address_value(tx.get("from")),
            "to_address": _address_value(tx.get("to")) or "contract creation",
            "asset": network["native_symbol"],
            "amount": _format_units(tx.get("value", "0"), 18),
            "timestamp": tx.get("timestamp"),
        }
        if tx.get("status") == "error":
            event["status"] = "FAILED (reverted)"
        return event

    def _normalize_token_transfer(self, transfer: dict, network: dict) -> dict:
        token = transfer.get("token") or {}
        total = transfer.get("total") or {}
        raw_value = total.get("value") if isinstance(total, dict) else transfer.get("value", "0")
        decimals = total.get("decimals") if isinstance(total, dict) else token.get("decimals")
        tx_hash = transfer.get("tx_hash") or transfer.get("transaction_hash") or ""
        log_index = transfer.get("log_index")
        return {
            "event_type": "erc20",
            "tx_hash": tx_hash,
            "event_id": f"{tx_hash}:{log_index}" if log_index is not None else tx_hash,
            "from_address": _address_value(transfer.get("from")),
            "to_address": _address_value(transfer.get("to")),
            "asset": token.get("symbol") or token.get("address_hash") or token.get("address") or "ERC-20",
            "amount": _format_units(raw_value, _safe_int(decimals, 0)),
            "timestamp": transfer.get("timestamp"),
        }


def _is_erc20(transfer: dict) -> bool:
    token_type = (transfer.get("token") or {}).get("type")
    return token_type in (None, "", "ERC-20")


def _address_value(value) -> str:
    if isinstance(value, dict):
        return value.get("hash") or value.get("address") or ""
    return value or ""


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_units(value, decimals: int) -> str:
    try:
        amount = Decimal(str(value)) / (Decimal(10) ** decimals)
    except (InvalidOperation, ValueError):
        return str(value)
    formatted = format(amount.normalize(), "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
