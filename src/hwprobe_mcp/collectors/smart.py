"""Disk SMART collector via `smartctl -j` (smartmontools)."""

from __future__ import annotations

from typing import Any

from ..util import is_root, run_json, which


def available() -> bool:
    return which("smartctl") is not None


def scan(warns: list[str]) -> list[str]:
    data, warn = run_json(["smartctl", "--scan-open", "-j"])
    if warn:
        warns.append(warn)
    devices: list[str] = []
    if isinstance(data, dict):
        for dev in data.get("devices") or []:
            if not isinstance(dev, dict):
                continue
            name = dev.get("name")
            if name:
                devices.append(name)
    return devices


def device(dev: str, warns: list[str]) -> dict[str, Any]:
    data, warn = run_json(["smartctl", "-j", "-a", dev], timeout=25)
    if warn:
        warns.append(warn)
    if not isinstance(data, dict):
        return {"device": dev, "error": "no SMART data (needs root?)"}
    return {
        "device": dev,
        "model": data.get("model_name"),
        "serial": data.get("serial_number"),
        "firmware": data.get("firmware_version"),
        "protocol": (data.get("device") or {}).get("protocol"),
        "capacity_bytes": (data.get("user_capacity") or {}).get("bytes"),
        "rotation_rate": data.get("rotation_rate"),  # 0/absent => SSD/NVMe
        "smart_passed": (data.get("smart_status") or {}).get("passed"),
        "temperature_c": (data.get("temperature") or {}).get("current"),
        "power_on_hours": (data.get("power_on_time") or {}).get("hours"),
        "power_cycles": data.get("power_cycle_count"),
    }


def health(warns: list[str]) -> dict[str, Any]:
    if not is_root():
        warns.append("smartctl: not running as root — SMART data may be denied or empty")
    devices = [device(d, warns) for d in scan(warns)]
    return {"root": is_root(), "devices": devices}
