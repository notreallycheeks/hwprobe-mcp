"""Lightweight per-host snapshot for the fleet agent: identity, CPU, RAM, disks, GPU, uptime.

Deliberately smaller than `aggregate.system_snapshot()`: this runs every few seconds on every
machine and travels over the network, so it carries only what a scheduler needs.
"""

from __future__ import annotations

import getpass
import os
import platform
import plistlib
import socket
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..collectors import nvidia
from ..util import IS_LINUX, IS_MAC, IS_WINDOWS, run
from . import SCHEMA_VERSION

try:
    import psutil
except Exception:  # pragma: no cover - psutil is a hard dep, stay defensive
    psutil = None  # type: ignore[assignment]

# Pseudo / system filesystems that never hold user data.
_SKIP_FSTYPES = {
    "squashfs", "tmpfs", "devtmpfs", "overlay", "iso9660", "autofs", "nsfs", "proc", "sysfs",
    "cgroup", "cgroup2", "devpts", "efivarfs", "fusectl", "configfs", "debugfs", "tracefs",
    "securityfs", "pstore", "bpf", "hugetlbfs", "mqueue", "binfmt_misc", "ramfs", "fuse.portal",
    "fuse.gvfsd-fuse", "udf", "vfat",
}
_SKIP_MOUNT_PREFIXES = (
    "/snap/", "/boot/", "/var/lib/docker", "/var/snap", "/run/",
    "/System/Volumes/VM", "/System/Volumes/Preboot", "/System/Volumes/Update",
    "/System/Volumes/xarts", "/System/Volumes/iSCPreboot", "/System/Volumes/Hardware",
    "/private/var/vm", "/Library/Developer/CoreSimulator",
)

_static_cache: dict[str, Any] = {}


def _try(warns: list[str], label: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        warns.append(f"{label}: {exc}")
        return None


def _gb(n: float | None) -> float | None:
    return None if n is None else round(n / 1024**3, 1)


def _pct(used: float | None, total: float | None) -> float | None:
    if used is None or not total:
        return None
    return round(100.0 * used / total, 1)


# --------------------------------------------------------------------------- identity


def _cpu_model(warns: list[str]) -> str | None:
    if IS_LINUX:
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception as exc:
            warns.append(f"cpuinfo: {exc}")
    elif IS_MAC:
        r = run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5)
        if r["rc"] == 0 and r["out"].strip():
            return r["out"].strip()
    model = platform.processor() or None
    return model


def identity(warns: list[str]) -> dict[str, Any]:
    """Static facts about this host (computed once per process)."""
    if "identity" not in _static_cache:
        boot = _try(warns, "psutil.boot_time", psutil.boot_time) if psutil else None
        _static_cache["identity"] = {
            "hostname": socket.gethostname().split(".")[0],
            "fqdn": socket.gethostname(),
            "user": _try(warns, "getuser", getpass.getuser),
            "os": platform.system(),
            "os_version": platform.platform(),
            "arch": platform.machine(),
            "cpu_model": _cpu_model(warns),
            "logical_cores": _try(warns, "cpu_count", psutil.cpu_count, logical=True) if psutil else None,
            "physical_cores": _try(warns, "cpu_count", psutil.cpu_count, logical=False) if psutil else None,
            "boot_time_epoch": boot,
            "agent_version": __version__,
            "python": platform.python_version(),
        }
    ident = dict(_static_cache["identity"])
    boot = ident.get("boot_time_epoch")
    ident["uptime_s"] = int(time.time() - boot) if boot else None
    return ident


# --------------------------------------------------------------------------- live


def cpu(warns: list[str], sample: float | None = None) -> dict[str, Any]:
    """CPU utilization. `sample` is a pre-measured percent from the agent's sampler thread;
    without one this blocks ~0.5 s to measure."""
    if psutil is None:
        warns.append("psutil unavailable")
        return {"percent": None}
    pct = sample if sample is not None else _try(warns, "cpu_percent", psutil.cpu_percent, interval=0.5)
    d: dict[str, Any] = {"percent": None if pct is None else round(float(pct), 1)}
    if hasattr(psutil, "getloadavg"):
        la = _try(warns, "getloadavg", psutil.getloadavg)
        d["load_avg_1_5_15"] = [round(x, 2) for x in la] if la else None
    freq = _try(warns, "cpu_freq", psutil.cpu_freq)
    d["freq_mhz"] = round(freq.current) if freq and freq.current else None
    return d


def memory(warns: list[str]) -> dict[str, Any]:
    if psutil is None:
        return {"total_gb": None}
    vm = _try(warns, "virtual_memory", psutil.virtual_memory)
    sm = _try(warns, "swap_memory", psutil.swap_memory)
    d: dict[str, Any] = {"total_gb": None, "used_gb": None, "available_gb": None, "percent": None}
    if vm:
        d.update(
            total_gb=_gb(vm.total),
            used_gb=_gb(vm.total - vm.available),
            available_gb=_gb(vm.available),
            percent=round(float(vm.percent), 1),
        )
    if sm and sm.total:
        d["swap"] = {"total_gb": _gb(sm.total), "used_gb": _gb(sm.used), "percent": round(float(sm.percent), 1)}
    return d


def _keep_partition(p) -> bool:
    if p.fstype.lower() in _SKIP_FSTYPES:
        return False
    mp = p.mountpoint
    if any(mp.startswith(pre) for pre in _SKIP_MOUNT_PREFIXES):
        return False
    if IS_WINDOWS and "cdrom" in (p.opts or "").lower():
        return False
    return True


def disks(warns: list[str]) -> dict[str, Any]:
    """Real filesystems only, one entry per device, plus `primary` (the OS volume)."""
    if psutil is None:
        return {"volumes": [], "primary": None}
    parts = _try(warns, "disk_partitions", psutil.disk_partitions, all=False) or []
    seen: set[str] = set()
    vols: list[dict[str, Any]] = []
    for p in parts:
        if not _keep_partition(p):
            continue
        key = p.device or p.mountpoint
        if key in seen:
            continue
        usage = _try(warns, f"disk_usage({p.mountpoint})", psutil.disk_usage, p.mountpoint)
        if not usage or not usage.total:
            continue
        seen.add(key)
        vols.append(
            {
                "mount": p.mountpoint,
                "device": p.device,
                "fstype": p.fstype,
                "total_gb": _gb(usage.total),
                "used_gb": _gb(usage.used),
                "free_gb": _gb(usage.free),
                "percent": round(float(usage.percent), 1),
            }
        )
    primary_mount = "C:\\" if IS_WINDOWS else "/"
    primary = next((v for v in vols if v["mount"].rstrip("\\/").lower() == primary_mount.rstrip("\\/").lower()), None)
    if primary is None and IS_MAC:
        primary = next((v for v in vols if v["mount"] == "/System/Volumes/Data"), None)
    if primary is None and vols:
        primary = max(vols, key=lambda v: v["total_gb"] or 0)
    return {"volumes": vols, "primary": primary}


# --------------------------------------------------------------------------- gpu


def _nvidia(warns: list[str]) -> list[dict[str, Any]]:
    local: list[str] = []
    rows = nvidia.query(local)
    out = []
    for g in rows:
        used, total = g.get("memory_used"), g.get("memory_total")
        out.append(
            {
                "vendor": "nvidia",
                "index": g.get("index"),
                "name": g.get("name"),
                "utilization_percent": g.get("utilization_gpu"),
                "memory_used_mb": used,
                "memory_total_mb": total,
                "memory_percent": _pct(used, total),
                "temperature_c": g.get("temperature_gpu"),
                "power_w": g.get("power_draw"),
            }
        )
    if not out:
        warns.extend(local)
    return out


def _amd_sysfs(warns: list[str]) -> list[dict[str, Any]]:
    out = []
    base = "/sys/class/drm"
    try:
        cards = sorted(c for c in os.listdir(base) if c.startswith("card") and "-" not in c)
    except Exception:
        return out
    for i, card in enumerate(cards):
        dev = os.path.join(base, card, "device")
        busy = os.path.join(dev, "gpu_busy_percent")
        if not os.path.exists(busy):
            continue
        try:
            util = int(Path(busy).read_text().strip())
            used = total = None
            if os.path.exists(os.path.join(dev, "mem_info_vram_used")):
                used = int(Path(dev, "mem_info_vram_used").read_text()) // (1024 * 1024)
                total = int(Path(dev, "mem_info_vram_total").read_text()) // (1024 * 1024)
            out.append(
                {
                    "vendor": "amd",
                    "index": i,
                    "name": f"AMD GPU ({card})",
                    "utilization_percent": util,
                    "memory_used_mb": used,
                    "memory_total_mb": total,
                    "memory_percent": _pct(used, total),
                    "temperature_c": None,
                    "power_w": None,
                }
            )
        except Exception as exc:
            warns.append(f"amd sysfs {card}: {exc}")
    return out


def _mac_ioreg(warns: list[str]) -> list[dict[str, Any]]:
    """Apple Silicon / AMD on macOS: `ioreg` exposes 'Device Utilization %' without root."""
    r = run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator", "-a"], timeout=8)
    if r["rc"] != 0 or not r["out"].strip():
        warns.append(f"ioreg: {(r['err'] or 'no output').strip()[:120]}")
        return []
    try:
        entries = plistlib.loads(r["out"].encode("utf-8"))
    except Exception as exc:
        warns.append(f"ioreg: plist parse failed ({exc})")
        return []
    chip = _static_cache.get("identity", {}).get("cpu_model") or "Apple"
    out = []
    for i, e in enumerate(entries if isinstance(entries, list) else [entries]):
        stats = e.get("PerformanceStatistics") or {}
        util = stats.get("Device Utilization %")
        if util is None:
            util = stats.get("GPU Activity(%)")
        in_use = stats.get("In use system memory")
        alloc = stats.get("Alloc system memory")
        out.append(
            {
                "vendor": "apple",
                "index": i,
                "name": e.get("model") or f"{chip} GPU",
                "utilization_percent": util,
                "memory_used_mb": None if in_use is None else int(in_use) // (1024 * 1024),
                "memory_total_mb": None if alloc is None else int(alloc) // (1024 * 1024),
                "memory_percent": _pct(in_use, alloc),
                "temperature_c": None,
                "power_w": None,
                "unified_memory": True,
            }
        )
    return out


_WIN_GPU_PS = r"""
$ErrorActionPreference='SilentlyContinue'
$names = @(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name)
$util = (Get-Counter '\GPU Engine(*engtype_3D)\Utilization Percentage').CounterSamples | Measure-Object -Property CookedValue -Sum | Select-Object -ExpandProperty Sum
$mem = (Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage').CounterSamples | Measure-Object -Property CookedValue -Sum | Select-Object -ExpandProperty Sum
@{ names = $names; util = $util; dedicated_bytes = $mem } | ConvertTo-Json -Compress
"""


def _windows_counters(warns: list[str]) -> list[dict[str, Any]]:
    """Any WDDM GPU (Intel/AMD/NVIDIA) via Windows performance counters. Slow (~2 s), so the
    agent caches it."""
    import json

    r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WIN_GPU_PS], timeout=20)
    if r["rc"] != 0 or not r["out"].strip():
        warns.append(f"gpu perf counters: {(r['err'] or 'no output').strip()[:120]}")
        return []
    try:
        d = json.loads(r["out"].strip().splitlines()[-1])
    except Exception as exc:
        warns.append(f"gpu perf counters: bad JSON ({exc})")
        return []
    names = d.get("names") or []
    if isinstance(names, str):
        names = [names]
    util = d.get("util")
    ded = d.get("dedicated_bytes")
    return [
        {
            "vendor": "wddm",
            "index": 0,
            "name": ", ".join(names) or "GPU",
            "utilization_percent": None if util is None else round(min(float(util), 100.0), 1),
            "memory_used_mb": None if ded is None else int(ded) // (1024 * 1024),
            "memory_total_mb": None,
            "memory_percent": None,
            "temperature_c": None,
            "power_w": None,
        }
    ]


def gpu(warns: list[str]) -> dict[str, Any]:
    """GPU utilization + memory. NVIDIA first (any OS), then the platform fallback."""
    gpus: list[dict[str, Any]] = []
    source = None
    if nvidia.available():
        gpus = _nvidia(warns)
        source = "nvidia-smi" if gpus else None
    if not gpus and IS_MAC:
        gpus, source = _mac_ioreg(warns), "ioreg"
    if not gpus and IS_LINUX:
        gpus, source = _amd_sysfs(warns), "sysfs"
    if not gpus and IS_WINDOWS:
        gpus, source = _windows_counters(warns), "perf-counters"
    if not gpus:
        source = None
        if not any("nvidia-smi" in w or "ioreg" in w or "perf counters" in w for w in warns):
            warns.append("no GPU telemetry source available on this host")
    return {"source": source, "gpus": gpus}


# --------------------------------------------------------------------------- snapshot


def snapshot(
    *,
    cpu_sample: float | None = None,
    gpu_data: dict[str, Any] | None = None,
    claude: dict[str, Any] | None = None,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """The `/v1/stats` document. Pass pre-sampled pieces from the agent to keep it instant."""
    warns: list[str] = list(extra_warnings or [])
    return {
        "schema": SCHEMA_VERSION,
        "ts": time.time(),
        "host": identity(warns),
        "cpu": cpu(warns, sample=cpu_sample),
        "memory": memory(warns),
        "disks": disks(warns),
        "gpu": gpu_data if gpu_data is not None else gpu(warns),
        "claude": claude,
        "warnings": warns,
    }
