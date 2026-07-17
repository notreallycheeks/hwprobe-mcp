"""Compose collectors into the six tool responses. Pure functions, no MCP dependency."""

from __future__ import annotations

from typing import Any

from . import deps
from .collectors import inventory as inv
from .collectors import lmsensors, nvidia, psutil_col, smart
from .util import IS_LINUX, envelope


def hardware_inventory() -> dict[str, Any]:
    warns: list[str] = []
    data = inv.inventory(warns)
    native = "lscpu/lsblk/lspci/dmidecode" if IS_LINUX else "system_profiler/cim"
    sources = ["platform", "psutil", native]
    return envelope(sources, data, warns)


def live_sensors() -> dict[str, Any]:
    warns: list[str] = []
    data: dict[str, Any] = {"backends": []}
    if psutil_col.available():
        data["component_temps"] = psutil_col.temperatures(warns)
        data["fans"] = psutil_col.fans(warns)
        data["battery"] = psutil_col.battery(warns)
        data["backends"].append("psutil")
    if lmsensors.available():
        data["lm_sensors"] = lmsensors.normalized(warns)
        data["backends"].append("lm-sensors")
    elif IS_LINUX:
        warns.append(
            f"lm-sensors not installed (limits voltages/power/fan data) — install: {deps.install_hint('lm-sensors')}"
        )
    if nvidia.available():
        data["nvidia_gpu"] = nvidia.query(warns)
        data["backends"].append("nvidia-smi")
    return envelope(data["backends"] or ["none"], data, warns, ok=bool(data["backends"]))


def cpu_status() -> dict[str, Any]:
    warns: list[str] = []
    if not psutil_col.available():
        return envelope("psutil", None, ["psutil unavailable"], ok=False)
    data = psutil_col.cpu(warns)
    data["temperatures"] = psutil_col.temperatures(warns)
    return envelope("psutil", data, warns)


def gpu_status() -> dict[str, Any]:
    warns: list[str] = []
    gpus = nvidia.query(warns) if nvidia.available() else None
    if gpus is None:
        warns.append(
            f"nvidia-smi not found — install: {deps.install_hint('nvidia-smi')}. "
            "AMD/Intel telemetry not yet implemented."
        )
    return envelope("nvidia-smi", {"nvidia": gpus}, warns, ok=bool(gpus))


def disk_health() -> dict[str, Any]:
    warns: list[str] = []
    if not smart.available():
        return envelope(
            "smartctl",
            None,
            [f"smartmontools not installed — install: {deps.install_hint('smartmontools')}"],
            ok=False,
        )
    return envelope("smartctl", smart.health(warns), warns)


def check_dependencies() -> dict[str, Any]:
    """Report which optional backends are present/missing, what each unlocks, and how to
    install any that are missing (auto-detects the platform package manager)."""
    report = deps.check()
    warns = []
    for m in report["missing"]:
        tag = "REQUIRED" if m["required"] else "optional"
        warns.append(f"[{tag}] {m['name']} missing — {m['install']}")
    return envelope("hwprobe.deps", report, warns, ok=report["all_present"])


def _psutil_env(fn) -> dict[str, Any]:
    """Wrap a raw psutil collector in the standard envelope so snapshot children are uniform."""
    if not psutil_col.available():
        return envelope("psutil", None, ["psutil unavailable"], ok=False)
    warns: list[str] = []
    data = fn(warns)
    return envelope("psutil", data, warns, ok=data is not None)


def system_snapshot() -> dict[str, Any]:
    # Every child is a full envelope so an agent can uniformly read
    # child["ok"] / child["warnings"] / child["data"], and the top-level ok
    # reflects the children rather than defaulting to True.
    snapshot = {
        "inventory": hardware_inventory(),
        "sensors": live_sensors(),
        "cpu": cpu_status(),
        "gpu": gpu_status(),
        "memory": _psutil_env(psutil_col.memory),
        "disk_usage": _psutil_env(psutil_col.disks),
        "disk_health": disk_health(),
        "network": _psutil_env(psutil_col.net),
        "boot": _psutil_env(psutil_col.boot),
    }
    ok = all(child.get("ok", True) for child in snapshot.values())
    return envelope("aggregate", snapshot, [], ok=ok)
