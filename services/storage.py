from __future__ import annotations

import json
import logging
import os
from pathlib import Path


logger = logging.getLogger(__name__)

META_KEY = "_meta"


class StateStorage:
    """state.json: seen event ids per "network:address:type" plus a "_meta" section.

    "_meta" holds the RPC block cursors and Telegram alerts that are still
    waiting to be delivered.
    """

    def __init__(self, path: Path, max_hashes: int = 500):
        self.path = path
        self.max_hashes = max_hashes
        self.state = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as error:
            logger.error("Could not load state file: %s", error)
            return {}

    def key(self, network_name: str, wallet_address: str, event_type: str) -> str:
        return f"{network_name}:{wallet_address.lower()}:{event_type}"

    def has_seen(
        self, network_name: str, wallet_address: str, event_type: str, event_id: str, legacy_hash: str = ""
    ) -> bool:
        seen = self.state.get(self.key(network_name, wallet_address, event_type), [])
        if event_id.lower() in seen:
            return True
        # State written by older versions stored the bare transaction hash.
        return bool(legacy_hash) and legacy_hash.lower() in seen

    def add(self, network_name: str, wallet_address: str, event_type: str, event_id: str) -> None:
        key = self.key(network_name, wallet_address, event_type)
        seen = self.state.setdefault(key, [])
        normalized = event_id.lower()
        if normalized not in seen:
            seen.insert(0, normalized)
        self.state[key] = seen[: self.max_hashes]

    def has_key(self, network_name: str, wallet_address: str, event_type: str) -> bool:
        return self.key(network_name, wallet_address, event_type) in self.state

    def ensure_key(self, network_name: str, wallet_address: str, event_type: str) -> None:
        self.state.setdefault(self.key(network_name, wallet_address, event_type), [])

    def meta(self) -> dict:
        meta = self.state.get(META_KEY)
        if not isinstance(meta, dict):
            meta = {}
            self.state[META_KEY] = meta
        return meta

    def rpc_cursor(self, network_name: str) -> dict:
        cursor = self.meta().setdefault("rpc", {}).get(network_name)
        return cursor if isinstance(cursor, dict) else {}

    def set_rpc_cursor(self, network_name: str, cursor: dict) -> None:
        self.meta().setdefault("rpc", {})[network_name] = cursor

    def pending_alerts(self) -> list:
        pending = self.meta().get("pending_alerts")
        if not isinstance(pending, list):
            pending = []
            self.meta()["pending_alerts"] = pending
        return pending

    def save(self) -> None:
        self.path.parent.mkdir(exist_ok=True)
        tmp_path = self.path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(self.state, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        tmp_path.replace(self.path)
