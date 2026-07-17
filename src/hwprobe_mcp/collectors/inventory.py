"""Deep static hardware inventory, dispatched per OS.

Linux : lscpu -J, lsblk -O -J, lspci, dmidecode (root)
macOS : system_profiler -json
Windows: Get-CimInstance ... | ConvertTo-Json
All    : platform + psutil basics
"""

from __future__ import annotations

import platform
from typing import Any

from ..util import IS_LINUX, IS_MAC, IS_WINDOWS, is_root, run, run_json, which

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

_LSBLK_KEEP = ("name", "model", "serial", "size", "type", "tran", "rota", "fstype", "vendor", "mountpoint")


def base(warns: list[str]) -> dict[str, Any]:
    u = platform.uname()
    d: dict[str, Any] = {
        "hostname": u.node,
        "os": u.system,
        "os_release": u.release,
        "os_version": u.version,
        "arch": u.machine,
        "processor": u.processor or None,
        "python": platform.python_version(),
    }
    if psutil is not None:
        try:
            d["total_ram_bytes"] = psutil.virtual_memory().total
        except Exception as exc:  # pragma: no cover
            warns.append(f"psutil.virtual_memory: {exc}")
    return d


def _slim_lsblk(devs: Any) -> list[dict[str, Any]]:
    if not isinstance(devs, list):
        return []
    out = []
    for dev in devs:
        if not isinstance(dev, dict):
            continue
        row = {k: dev.get(k) for k in _LSBLK_KEEP if k in dev}
        children = dev.get("children")
        if isinstance(children, list):
            row["children"] = _slim_lsblk(children)
        out.append(row)
    return out


def _parse_dmidecode(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    last_key: str | None = None  # a "Key:" with an empty value gathers indented bullets
    for line in text.splitlines():
        if not line.strip():
            cur = None
            last_key = None
            continue
        stripped = line.strip()
        indented = line[0].isspace()
        # Skip dmidecode's banner, SMBIOS status, and per-record "Handle" markers.
        # These are unindented and colon-free but are NOT section titles.
        if not indented and (
            line.startswith("#")
            or line.startswith("Handle ")
            or stripped.startswith("SMBIOS ")
            or stripped.startswith("Getting SMBIOS")
        ):
            cur = None
            last_key = None
            continue
        # Real section title: unindented, non-empty, no "key: value" colon pair.
        if not indented and ":" not in line:
            cur = {"_section": stripped}
            entries.append(cur)
            last_key = None
        elif cur is not None and ":" in line:
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip()
            cur[key] = val
            # An empty value introduces an indented bullet sub-list (Flags/Characteristics).
            last_key = key if val == "" else None
        elif cur is not None and indented and last_key is not None:
            existing = cur.get(last_key)
            if isinstance(existing, list):
                existing.append(stripped)
            else:
                cur[last_key] = [stripped]
    return entries


def _linux(warns: list[str]) -> dict[str, Any]:
    d: dict[str, Any] = {}

    data, warn = run_json(["lscpu", "-J"])
    if warn:
        warns.append(warn)
    if isinstance(data, dict):
        d["cpu"] = {
            item["field"].rstrip(":"): item.get("data")
            for item in data.get("lscpu", [])
            if isinstance(item, dict) and isinstance(item.get("field"), str)
        }

    if which("dmidecode"):
        if is_root():
            dmi: dict[str, Any] = {}
            for kind in ("system", "baseboard", "bios", "memory"):
                r = run(["dmidecode", "-t", kind])
                if r["rc"] == 0 and r["out"].strip():
                    dmi[kind] = _parse_dmidecode(r["out"])
            if dmi:
                d["dmi"] = dmi
        else:
            warns.append("dmidecode: needs root for motherboard/BIOS/RAM-DIMM detail")

    data, warn = run_json(["lsblk", "-J", "-O"])
    if warn:
        warns.append(warn)
    if isinstance(data, dict):
        d["block_devices"] = _slim_lsblk(data.get("blockdevices", []))

    r = run(["lspci"])
    if r["rc"] == 0:
        d["pci_display"] = [
            ln for ln in r["out"].splitlines() if any(k in ln for k in ("VGA", "3D", "Display"))
        ]
    elif which("lspci") is None:
        warns.append("lspci: not installed (pciutils) — GPU/PCI enumeration skipped")

    return d


def _mac(warns: list[str]) -> dict[str, Any]:
    data, warn = run_json(
        [
            "system_profiler", "-json",
            "SPHardwareDataType", "SPMemoryDataType",
            "SPStorageDataType", "SPDisplaysDataType",
        ],
        timeout=40,
    )
    if warn:
        warns.append(warn)
    return {"system_profiler": data} if data else {}


def _windows(warns: list[str]) -> dict[str, Any]:
    script = (
        "$o=[ordered]@{};"
        "$o.system=Get-CimInstance Win32_ComputerSystem;"
        "$o.cpu=Get-CimInstance Win32_Processor;"
        "$o.bios=Get-CimInstance Win32_BIOS;"
        "$o.baseboard=Get-CimInstance Win32_BaseBoard;"
        "$o.memory=Get-CimInstance Win32_PhysicalMemory;"
        "$o.gpu=Get-CimInstance Win32_VideoController;"
        "$o.disks=Get-CimInstance Win32_DiskDrive;"
        "[pscustomobject]$o | ConvertTo-Json -Depth 4"
    )
    exe = "powershell" if which("powershell") else ("pwsh" if which("pwsh") else None)
    if exe is None:
        warns.append("powershell/pwsh: not installed — Windows CIM inventory skipped")
        return {}
    data, warn = run_json([exe, "-NoProfile", "-NonInteractive", "-Command", script], timeout=40)
    if warn:
        warns.append(warn)
    if not isinstance(data, dict):
        return {}
    # Get-CimInstance + ConvertTo-Json yields a JSON object for a single-instance class
    # and an array for multiple; normalize every class key to a list for a stable shape.
    for key, val in list(data.items()):
        if val is None:
            data[key] = []
        elif not isinstance(val, list):
            data[key] = [val]
    return {"cim": data}


def inventory(warns: list[str]) -> dict[str, Any]:
    d = base(warns)
    try:  # native probes must never raise out of a collector
        if IS_LINUX:
            d.update(_linux(warns))
        elif IS_MAC:
            d.update(_mac(warns))
        elif IS_WINDOWS:
            d.update(_windows(warns))
    except Exception as exc:  # pragma: no cover - defensive
        warns.append(f"inventory: native probe failed: {exc}")
    return d
