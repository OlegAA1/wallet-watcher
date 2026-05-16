from __future__ import annotations

import logging

from services.providers.common import ProviderError, RateLimitError, is_rate_limit_message
from services.providers.etherscan_v2 import EtherscanV2Provider


logger = logging.getLogger(__name__)


class BotanixScanProvider(EtherscanV2Provider):
    def __init__(self, api_key: str, timeout: int = 20, limit: int = 50):
        super().__init__(api_key=api_key, timeout=timeout, limit=limit)

    def diagnostic_network(self, network: dict) -> dict:
        base_url = network.get("api_base_url", "https://api.botanixscan.io/api")
        action_name = network.get("native_action", "txlist")
        params = self._build_params(
            network=network,
            address="0x0000000000000000000000000000000000000000",
            action=action_name,
            limit=1,
        )
        if network.get("chain_id"):
            params["chainid"] = network["chain_id"]
        return self._diagnostic_from_params(
            network=network,
            provider_name="botanixscan",
            url=base_url,
            params=params,
        )

    def _request(self, network: dict, address: str, action: str, limit: int | None = None) -> list[dict]:
        base_url = network.get("api_base_url", "https://api.botanixscan.io/api")
        action_name = network.get("native_action" if action == "txlist" else "token_action", action)
        params = {
            "module": "account",
            "action": action_name,
            "address": address,
            "sort": "desc",
            "page": 1,
            "offset": limit or self.limit,
            "apikey": self.api_key,
        }
        if network.get("chain_id"):
            params["chainid"] = network["chain_id"]

        if not self.api_key:
            raise ValueError("BOTANIXSCAN_API_KEY is not configured")

        data = self._get_account_response(base_url, params)
        result = data.get("result", [])
        status = str(data.get("status", ""))
        message = str(data.get("message", ""))
        result_text = result if isinstance(result, str) else message

        if status == "0" and isinstance(result, str) and "No transactions found" in result:
            return []
        if status == "0" and is_rate_limit_message(result_text):
            raise RateLimitError("Rate limit from BotanixScan API")
        if status == "0" and message.upper() != "OK":
            raise ProviderError((result_text or "BotanixScan API error")[:180])
        if not isinstance(result, list):
            raise ProviderError("Unexpected BotanixScan response format")
        return result
