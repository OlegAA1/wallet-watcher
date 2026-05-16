from __future__ import annotations

import json
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


class StateStorage:
    def __init__(self, path: Path, max_hashes: int = 500):
        self.path = path
        self.max_hashes = max_hashes
        self.state = self._load()

    def _load(self) -> dict[str, list[str]]:
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

    def has_seen(self, network_name: str, wallet_address: str, event_type: str, tx_hash: str) -> bool:
        return tx_hash.lower() in self.state.get(self.key(network_name, wallet_address, event_type), [])

    def add(self, network_name: str, wallet_address: str, event_type: str, tx_hash: str) -> None:
        key = self.key(network_name, wallet_address, event_type)
        hashes = self.state.setdefault(key, [])
        normalized_hash = tx_hash.lower()
        if normalized_hash not in hashes:
            hashes.insert(0, normalized_hash)
        self.state[key] = hashes[: self.max_hashes]

    def has_key(self, network_name: str, wallet_address: str, event_type: str) -> bool:
        return self.key(network_name, wallet_address, event_type) in self.state

    def ensure_key(self, network_name: str, wallet_address: str, event_type: str) -> None:
        self.state.setdefault(self.key(network_name, wallet_address, event_type), [])

    def save(self) -> None:
        self.path.parent.mkdir(exist_ok=True)
        tmp_path = self.path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(self.state, file, indent=2, ensure_ascii=False)
            file.write("\n")
        tmp_path.replace(self.path)
