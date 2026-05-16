from __future__ import annotations

import time
from json import JSONDecodeError
from typing import Any
from urllib.parse import urlencode

import requests


RATE_LIMIT_MESSAGES = (
    "rate limit",
    "max rate",
    "too many requests",
    "temporarily unavailable",
)

AUTH_MESSAGES = (
    "invalid api key",
    "missing api key",
    "api key is missing",
    "not authorized",
    "unauthorized",
    "forbidden",
    "access denied",
)

PLAN_OR_CHAIN_MESSAGES = (
    "unsupported chain",
    "unsupported chainid",
    "invalid chainid",
    "chainid is not supported",
    "not supported",
    "upgrade",
    "paid plan",
    "pro plan",
    "not available for your plan",
)


class ProviderError(RuntimeError):
    pass


class RateLimitError(ProviderError):
    pass


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: int = 20, retries: int = 2) -> Any:
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as error:
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            raise ProviderError(f"Request failed: {error.__class__.__name__}") from error

        if response.status_code == 429:
            if attempt < retries:
                time.sleep(_retry_after(response, default=2 + attempt))
                continue
            raise RateLimitError("Rate limit: HTTP 429")

        if not response.ok:
            raise ProviderError(f"HTTP status {response.status_code}")

        try:
            return response.json()
        except (JSONDecodeError, ValueError) as error:
            raise ProviderError("Invalid JSON response") from error

    raise ProviderError("Request failed")


def is_rate_limit_message(message: str) -> bool:
    normalized = message.lower()
    return any(marker in normalized for marker in RATE_LIMIT_MESSAGES)


def is_auth_error_message(message: str) -> bool:
    normalized = message.lower()
    return any(marker in normalized for marker in AUTH_MESSAGES)


def is_plan_or_unsupported_message(message: str) -> bool:
    normalized = message.lower()
    return any(marker in normalized for marker in PLAN_OR_CHAIN_MESSAGES)


def mask_secret(value: str) -> str:
    if not value:
        return "not configured"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def endpoint_with_masked_params(url: str, params: dict | None = None, secret_keys: tuple[str, ...] = ("apikey",)) -> str:
    if not params:
        return url

    masked_params = {}
    secret_names = {key.lower() for key in secret_keys}
    for key, value in params.items():
        if key.lower() in secret_names:
            masked_params[key] = mask_secret(str(value))
        else:
            masked_params[key] = value
    return f"{url}?{urlencode(masked_params)}"


def request_json_diagnostic(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 20,
) -> dict:
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as error:
        return {
            "response_received": False,
            "http_status": None,
            "json": None,
            "error": f"Request failed: {error.__class__.__name__}",
        }

    try:
        data = response.json()
        json_error = None
    except (JSONDecodeError, ValueError):
        data = None
        json_error = "Invalid JSON response"

    return {
        "response_received": True,
        "http_status": response.status_code,
        "json": data,
        "error": json_error,
    }


def classify_diagnostic(http_status: int | None, data: Any, error: str | None = None) -> dict:
    message = _diagnostic_message(data, error)
    rate_limit = http_status == 429 or is_rate_limit_message(message)
    auth_error = http_status in (401, 403) or is_auth_error_message(message)
    plan_or_unsupported = is_plan_or_unsupported_message(message)
    ok = http_status is not None and 200 <= http_status < 300 and not rate_limit and not auth_error and not plan_or_unsupported

    if isinstance(data, dict) and str(data.get("status", "")) == "0":
        result = data.get("result", "")
        result_text = result if isinstance(result, str) else str(data.get("message", ""))
        if "No transactions found" not in result_text:
            ok = False

    if error:
        ok = False

    return {
        "ok": ok,
        "auth_error": auth_error,
        "plan_or_unsupported_error": plan_or_unsupported,
        "rate_limit_error": rate_limit,
        "message": _short_message(message or ("OK" if ok else "Unknown error")),
    }


def _diagnostic_message(data: Any, error: str | None) -> str:
    if error:
        return error
    if isinstance(data, dict):
        result = data.get("result", "")
        if isinstance(result, str) and result:
            return result
        message = data.get("message", "")
        if message:
            return str(message)
        error_value = data.get("error", "")
        if error_value:
            return str(error_value)
    return ""


def _short_message(message: str) -> str:
    return message.replace("\n", " ").strip()[:180]


def _retry_after(response: requests.Response, default: int) -> int:
    value = response.headers.get("Retry-After")
    if value and value.isdigit():
        return min(int(value), 30)
    return default
