from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation

from services.providers.common import (
    ProviderError,
    RateLimitError,
    classify_diagnostic,
    endpoint_with_masked_params,
    get_json,
    mask_secret,
    request_json_diagnostic,
    is_rate_limit_message,
)


logger = logging.getLogger(__name__)

ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"


class EtherscanV2Provider:
    def __init__(self, api_key: str, timeout: int = 20, limit: int = 50):
        self.api_key = api_key
        self.timeout = timeout
        self.limit = limit

    def get_native_transactions(self, network: dict, address: str) -> list[dict]:
        result = self._request(network, address, "txlist")
        transactions = result[: self.limit]
        return [self._normalize_native(tx, network) for tx in transactions if tx.get("hash")]

    def get_token_transfers(self, network: dict, address: str) -> list[dict]:
        result = self._request(network, address, "tokentx")
        transfers = result[: self.limit]
        return [self._normalize_token_transfer(tx, network) for tx in transfers if tx.get("hash")]

    def test_network(self, network: dict) -> tuple[bool, str]:
        diagnostic = self.diagnostic_network(network)
        return diagnostic["ok"], diagnostic["message"]

    def diagnostic_network(self, network: dict) -> dict:
        params = self._build_params(
            network=network,
            address="0x0000000000000000000000000000000000000000",
            action="txlist",
            limit=1,
        )
        return self._diagnostic_from_params(
            network=network,
            provider_name="etherscan_v2",
            url=ETHERSCAN_V2_URL,
            params=params,
        )

    def _request(self, network: dict, address: str, action: str, limit: int | None = None) -> list[dict]:
        if not self.api_key:
            raise ValueError("ETHERSCAN_API_KEY is not configured")

        params = self._build_params(network=network, address=address, action=action, limit=limit or self.limit)
        data = self._get_account_response(ETHERSCAN_V2_URL, params)
        status = str(data.get("status", ""))
        message = str(data.get("message", ""))
        result = data.get("result", [])
        result_text = result if isinstance(result, str) else message

        if status == "0" and isinstance(result, str) and "No transactions found" in result:
            return []
        if status == "0" and is_rate_limit_message(result_text):
            raise RateLimitError("Rate limit from Etherscan API")
        if status == "0" and message.upper() != "OK":
            raise ProviderError(_short_error(result_text, "Etherscan API error"))
        if not isinstance(result, list):
            raise ProviderError("Unexpected Etherscan response format")
        return result

    def _build_params(self, network: dict, address: str, action: str, limit: int) -> dict:
        return {
            "module": "account",
            "action": action,
            "address": address,
            "chainid": network["chain_id"],
            "sort": "desc",
            "page": 1,
            "offset": limit,
            "apikey": self.api_key,
        }

    def _diagnostic_from_params(self, network: dict, provider_name: str, url: str, params: dict) -> dict:
        endpoint = endpoint_with_masked_params(url, params, secret_keys=("apikey",))
        if not self.api_key:
            return {
                "network": network.get("name", "Unknown"),
                "provider": provider_name,
                "chain_id": network.get("chain_id"),
                "endpoint": endpoint,
                "api_key": mask_secret(self.api_key),
                "response_received": False,
                "auth_error": True,
                "plan_or_unsupported_error": False,
                "rate_limit_error": False,
                "ok": False,
                "message": "API key is not configured",
            }

        response = request_json_diagnostic(url, params=params, timeout=self.timeout)
        classification = classify_diagnostic(response["http_status"], response["json"], response["error"])
        return {
            "network": network.get("name", "Unknown"),
            "provider": provider_name,
            "chain_id": network.get("chain_id"),
            "endpoint": endpoint,
            "api_key": mask_secret(self.api_key),
            "response_received": response["response_received"],
            "http_status": response["http_status"],
            **classification,
        }

    def _get_account_response(self, url: str, params: dict) -> dict:
        for attempt in range(3):
            data = get_json(url, params=params, timeout=self.timeout)
            result = data.get("result", "")
            message = result if isinstance(result, str) else str(data.get("message", ""))
            if is_rate_limit_message(message) and attempt < 2:
                time.sleep(2 + attempt)
                continue
            return data
        raise RateLimitError("Rate limit from Etherscan API")

    def _normalize_native(self, tx: dict, network: dict) -> dict:
        return {
            "event_type": "native",
            "tx_hash": tx.get("hash", ""),
            "from_address": tx.get("from", ""),
            "to_address": tx.get("to", ""),
            "asset": network["native_symbol"],
            "amount": _format_units(tx.get("value", "0"), 18),
        }

    def _normalize_token_transfer(self, tx: dict, network: dict) -> dict:
        decimals = _safe_int(tx.get("tokenDecimal"), 0)
        return {
            "event_type": "erc20",
            "tx_hash": tx.get("hash", ""),
            "from_address": tx.get("from", ""),
            "to_address": tx.get("to", ""),
            "asset": tx.get("tokenSymbol") or tx.get("contractAddress") or "ERC-20",
            "amount": _format_units(tx.get("value", "0"), decimals),
        }


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


def _short_error(message: str, fallback: str) -> str:
    clean = (message or fallback).replace("\n", " ").strip()
    return clean[:180]
