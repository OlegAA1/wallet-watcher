from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from services.providers.common import classify_diagnostic, get_json, mask_secret, request_json_diagnostic


logger = logging.getLogger(__name__)


class BlockscoutProvider:
    def __init__(self, api_key: str = "", timeout: int = 20, limit: int = 50):
        self.api_key = api_key
        self.timeout = timeout
        self.limit = limit

    def get_native_transactions(self, network: dict, address: str) -> list[dict]:
        endpoint = network.get("transactions_endpoint", "/addresses/{address}/transactions")
        data = self._request(network, endpoint, address)
        items = self._items(data)[: self.limit]
        return [self._normalize_native(tx, network) for tx in items if tx.get("hash")]

    def get_token_transfers(self, network: dict, address: str) -> list[dict]:
        endpoint = network.get("token_transfers_endpoint", "/addresses/{address}/token-transfers")
        data = self._request(network, endpoint, address)
        items = self._items(data)[: self.limit]
        return [self._normalize_token_transfer(tx, network) for tx in items if tx.get("tx_hash") or tx.get("transaction_hash")]

    def test_network(self, network: dict) -> tuple[bool, str]:
        diagnostic = self.diagnostic_network(network)
        return diagnostic["ok"], diagnostic["message"]

    def diagnostic_network(self, network: dict) -> dict:
        address = "0x0000000000000000000000000000000000000000"
        endpoint = network.get("transactions_endpoint", "/addresses/{address}/transactions")
        try:
            url, params, headers = self._build_request(network, endpoint, address, limit=1)
        except Exception as error:
            return {
                "network": network.get("name", "Unknown"),
                "provider": "blockscout",
                "chain_id": network.get("chain_id"),
                "endpoint": network.get("api_base_url", "not configured"),
                "api_key": mask_secret(self.api_key),
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
            "endpoint": f"{url}?limit={params['limit']}",
            "api_key": mask_secret(self.api_key),
            "response_received": response["response_received"],
            "http_status": response["http_status"],
            **classification,
        }

    def _request(self, network: dict, endpoint: str, address: str, limit: int | None = None) -> dict | list:
        url, params, headers = self._build_request(network, endpoint, address, limit or self.limit)
        return get_json(url, params=params, headers=headers, timeout=self.timeout)

    def _build_request(self, network: dict, endpoint: str, address: str, limit: int) -> tuple[str, dict, dict]:
        base_url = network.get("api_base_url")
        if not base_url:
            raise ValueError("api_base_url is missing in networks.json")

        path = endpoint.format(address=address)
        url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        params = {"limit": limit}
        headers = {}
        if self.api_key:
            header_name = network.get("api_key_header", "Authorization")
            header_prefix = network.get("api_key_prefix", "Bearer ")
            headers[header_name] = f"{header_prefix}{self.api_key}" if header_prefix else self.api_key
        return url, params, headers

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
        return {
            "event_type": "native",
            "tx_hash": tx.get("hash", ""),
            "from_address": _address_value(tx.get("from")),
            "to_address": _address_value(tx.get("to")),
            "asset": network["native_symbol"],
            "amount": _format_units(tx.get("value", "0"), 18),
        }

    def _normalize_token_transfer(self, transfer: dict, network: dict) -> dict:
        token = transfer.get("token") or {}
        total = transfer.get("total") or {}
        raw_value = total.get("value") if isinstance(total, dict) else transfer.get("value", "0")
        decimals = total.get("decimals") if isinstance(total, dict) else token.get("decimals")
        return {
            "event_type": "erc20",
            "tx_hash": transfer.get("tx_hash") or transfer.get("transaction_hash") or transfer.get("transaction_hashes", [""])[0],
            "from_address": _address_value(transfer.get("from")),
            "to_address": _address_value(transfer.get("to")),
            "asset": token.get("symbol") or token.get("address") or "ERC-20",
            "amount": _format_units(raw_value, _safe_int(decimals, 0)),
        }


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
