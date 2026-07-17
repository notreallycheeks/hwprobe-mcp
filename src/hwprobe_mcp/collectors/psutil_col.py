"""Cross-platform core collector backed by psutil."""

from __future__ import annotations

import datetime
from typing import Any

try:
    import psutil
except Exception:  # pragma: no cover - psutil is a hard dep, but stay defensive
    psutil = None  # type: ignore[assignment]


def available() -> bool:
    return psutil is not None


def _try(warns: list[str], label: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        warns.append(f"psutil.{label}: {exc}")
        return None


def cpu(warns: list[str]) -> dict[str, Any]:
    d: dict[str, Any] = {
        "logical_cores": _try(warns, "cpu_count", psutil.cpu_count, logical=True),
        "physical_cores": _try(warns, "cpu_count", psutil.cpu_count, logical=False),
    }
    per = _try(warns, "cpu_percent", psutil.cpu_percent, interval=0.3, percpu=True) or []
    d["per_core_percent"] = per
    d["total_percent"] = round(sum(per) / len(per), 1) if per else None
    freq = _try(warns, "cpu_freq", psutil.cpu_freq, percpu=True)
    d["per_core_freq_mhz"] = [f._asdict() for f in freq] if freq else None
    if hasattr(psutil, "getloadavg"):
        la = _try(warns, "getloadavg", psutil.getloadavg)
        d["load_avg_1_5_15"] = list(la) if la else None
    return d


def memory(warns: list[str]) -> dict[str, Any]:
    vm = _try(warns, "virtual_memory", psutil.virtual_memory)
    sm = _try(warns, "swap_memory", psutil.swap_memory)
    return {
        "virtual": vm._asdict() if vm else None,
        "swap": sm._asdict() if sm else None,
    }


def temperatures(warns: list[str]) -> dict[str, Any] | None:
    fn = getattr(psutil, "sensors_temperatures", None)
    if fn is None:
        warns.append("psutil.sensors_temperatures: unsupported on this platform")
        return None
    data = _try(warns, "sensors_temperatures", fn) or {}
    return {
        chip: [
            {"label": e.label or None, "current": e.current, "high": e.high, "critical": e.critical}
            for e in entries
        ]
        for chip, entries in data.items()
    }


def fans(warns: list[str]) -> dict[str, Any] | None:
    fn = getattr(psutil, "sensors_fans", None)
    if fn is None:
        return None
    data = _try(warns, "sensors_fans", fn) or {}
    return {
        chip: [{"label": e.label or None, "rpm": e.current} for e in entries]
        for chip, entries in data.items()
    }


def battery(warns: list[str]) -> dict[str, Any] | None:
    fn = getattr(psutil, "sensors_battery", None)
    if fn is None:
        return None
    b = _try(warns, "sensors_battery", fn)
    if b is None:
        return None
    unknown = {getattr(psutil, "POWER_TIME_UNLIMITED", -1), getattr(psutil, "POWER_TIME_UNKNOWN", -2)}
    return {
        "percent": round(b.percent, 1),
        "secs_left": None if b.secsleft in unknown else b.secsleft,
        "plugged_in": b.power_plugged,
    }


def disks(warns: list[str]) -> dict[str, Any]:
    parts = _try(warns, "disk_partitions", psutil.disk_partitions, all=False) or []
    out = []
    for p in parts:
        usage = _try(warns, "disk_usage", psutil.disk_usage, p.mountpoint)
        out.append(
            {
                "device": p.device,
                "mountpoint": p.mountpoint,
                "fstype": p.fstype,
                "usage": usage._asdict() if usage else None,
            }
        )
    io = _try(warns, "disk_io_counters", psutil.disk_io_counters, perdisk=True) or {}
    return {"partitions": out, "io_per_disk": {k: v._asdict() for k, v in io.items()}}


def net(warns: list[str]) -> dict[str, Any]:
    io = _try(warns, "net_io_counters", psutil.net_io_counters, pernic=True) or {}
    return {nic: v._asdict() for nic, v in io.items()}


def boot(warns: list[str]) -> dict[str, Any]:
    bt = _try(warns, "boot_time", psutil.boot_time)
    # fromtimestamp can raise (OverflowError/OSError/ValueError) on a corrupt epoch
    # (broken RTC on VMs/containers), so guard it via the never-raise helper.
    iso = (
        _try(warns, "boot_time_iso", lambda: datetime.datetime.fromtimestamp(bt).isoformat())
        if bt
        else None
    )
    return {
        "boot_time_epoch": bt,
        "boot_time_iso": iso,
    }
