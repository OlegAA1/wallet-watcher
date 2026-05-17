from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def collect_server_status(base_dir: Path, state_file: Path, log_file: Path) -> dict:
    disk = shutil.disk_usage(base_dir)
    load_avg = os.getloadavg() if hasattr(os, "getloadavg") else None
    memory = _memory_status()

    return {
        "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
        "pid": os.getpid(),
        "cpu_count": os.cpu_count() or 0,
        "load_average": load_avg,
        "memory": memory,
        "disk": {
            "path": str(base_dir),
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "used_percent": _percent(disk.used, disk.total),
        },
        "uptime_seconds": _uptime_seconds(),
        "state": _state_status(state_file),
        "log": _file_status(log_file),
    }


def print_server_status(status: dict) -> None:
    print("Server status")
    print(f"  Hostname: {status['hostname']}")
    print(f"  PID: {status['pid']}")
    print(f"  CPU cores: {status['cpu_count']}")

    load_avg = status["load_average"]
    if load_avg:
        print(f"  Load average: {load_avg[0]:.2f} / {load_avg[1]:.2f} / {load_avg[2]:.2f}")
    else:
        print("  Load average: unavailable")

    uptime = status["uptime_seconds"]
    print(f"  Uptime: {_format_duration(uptime)}" if uptime is not None else "  Uptime: unavailable")

    memory = status["memory"]
    if memory:
        print(
            "  Memory: "
            f"{_format_bytes(memory['used'])} used / {_format_bytes(memory['total'])} total "
            f"({memory['used_percent']:.1f}%)"
        )
    else:
        print("  Memory: unavailable")

    disk = status["disk"]
    print(
        "  Disk: "
        f"{_format_bytes(disk['used'])} used / {_format_bytes(disk['total'])} total "
        f"({disk['used_percent']:.1f}%)"
    )

    state = status["state"]
    print("State file")
    print(f"  Exists: {_yes_no(state['exists'])}")
    if state["exists"]:
        print(f"  Size: {_format_bytes(state['size'])}")
        print(f"  Keys: {state['keys']}")
        print(f"  Stored hashes: {state['hashes']}")

    log = status["log"]
    print("Log file")
    print(f"  Exists: {_yes_no(log['exists'])}")
    if log["exists"]:
        print(f"  Size: {_format_bytes(log['size'])}")


def _memory_status() -> dict | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None

    values = {}
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None

    used = total - available
    return {
        "total": total,
        "available": available,
        "used": used,
        "used_percent": _percent(used, total),
    }


def _uptime_seconds() -> float | None:
    uptime_file = Path("/proc/uptime")
    if not uptime_file.exists():
        return None
    try:
        return float(uptime_file.read_text(encoding="utf-8").split()[0])
    except (IndexError, ValueError):
        return None


def _state_status(path: Path) -> dict:
    status = _file_status(path)
    status.update({"keys": 0, "hashes": 0})
    if not path.exists():
        return status

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        status["error"] = "Could not parse state.json"
        return status

    if isinstance(data, dict):
        status["keys"] = len(data)
        status["hashes"] = sum(len(value) for value in data.values() if isinstance(value, list))
    return status


def _file_status(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "size": 0}
    stat = path.stat()
    return {"exists": True, "size": stat.st_size}


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_duration(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _percent(part: int, total: int) -> float:
    return (part / total * 100) if total else 0.0


def _yes_no(value: bool) -> str:
    return "YES" if value else "NO"
