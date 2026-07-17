"""Disk SMART collector via `smartctl -j` (smartmontools)."""

from __future__ import annotations

from typing import Any

from ..util import is_root, run_json, which


def available() -> bool:
    return which("smartctl") is not None


def _json(args: list[str], warns: list[str], timeout: float = 25) -> Any:
    """Run `smartctl -j <args>` and return parsed JSON. SMART reads need privilege, so when
    not root we first try passwordless `sudo -n` (works only if a sudoers rule allows it) and
    fall back to a direct call. Never raises."""
    if not is_root() and which("sudo"):
        data, _ = run_json(["sudo", "-n", "smartctl", "-j", *args], timeout=timeout)
        if data is not None:
            return data
    data, warn = run_json(["smartctl", "-j", *args], timeout=timeout)
    if warn:
        warns.append(warn)
    return data


def scan(warns: list[str]) -> list[str]:
    data = _json(["--scan-open"], warns)
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
    data = _json(["-a", dev], warns, timeout=25)
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
        warns.append(
            "smartctl needs root for SMART data — run the server as root, or grant "
            "passwordless sudo for smartctl (see README). `sudo -n` is attempted automatically."
        )
    devices = [device(d, warns) for d in scan(warns)]
    return {"root": is_root(), "devices": devices}
