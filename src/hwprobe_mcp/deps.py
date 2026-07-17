"""Optional backend registry + dependency checker.

Powers the `check_dependencies` tool and the `--doctor` CLI, and lets collectors
enrich their "not installed" warnings with an actual install command. hwprobe works
with whatever is present and degrades gracefully; this module tells the user exactly
what to install to unlock the rest.
"""

from __future__ import annotations

import importlib.util
from typing import Any

from .util import IS_MAC, IS_WINDOWS, PLATFORM, which

_PLATFORM_KEY = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}.get(PLATFORM, "")

# Each entry: what it is, what it unlocks, and how to install it per package manager.
DEPENDENCIES: list[dict[str, Any]] = [
    {
        "key": "psutil",
        "kind": "python",
        "purpose": "core CPU / memory / disk / network metrics + component temps, fans, battery",
        "enables": ["cpu_status", "live_sensors (temps/fans/battery)", "memory", "disk_usage"],
        "platforms": ["linux", "darwin", "windows"],
        "required": True,
        "install": {"pip": "pip install psutil"},
    },
    {
        "key": "lm-sensors",
        "kind": "binary",
        "bin": "sensors",
        "purpose": "rich sensor readings: temperatures, fan RPM, voltages, power",
        "enables": ["live_sensors (voltages/power)"],
        "platforms": ["linux"],
        "install": {
            "apt": "sudo apt install lm-sensors && sudo sensors-detect --auto",
            "dnf": "sudo dnf install lm_sensors && sudo sensors-detect --auto",
            "pacman": "sudo pacman -S lm_sensors && sudo sensors-detect --auto",
            "zypper": "sudo zypper install sensors && sudo sensors-detect --auto",
        },
    },
    {
        "key": "smartmontools",
        "kind": "binary",
        "bin": "smartctl",
        "purpose": "disk SMART health: model, temperature, wear, power-on hours",
        "enables": ["disk_health"],
        "needs_root_to_run": True,
        "platforms": ["linux", "darwin", "windows"],
        "install": {
            "apt": "sudo apt install smartmontools",
            "dnf": "sudo dnf install smartmontools",
            "pacman": "sudo pacman -S smartmontools",
            "zypper": "sudo zypper install smartmontools",
            "brew": "brew install smartmontools",
            "winget": "winget install smartmontools.smartmontools",
        },
    },
    {
        "key": "nvidia-smi",
        "kind": "binary",
        "bin": "nvidia-smi",
        "purpose": "NVIDIA GPU inventory + telemetry (temp, utilization, power, clocks, memory)",
        "enables": ["gpu_status (NVIDIA)"],
        "platforms": ["linux", "windows"],
        "install": {
            "apt": "sudo ubuntu-drivers autoinstall   # installs the NVIDIA driver (bundles nvidia-smi)",
            "note": "ships with the NVIDIA driver; ensure the driver is installed and its kernel module is loaded",
        },
    },
    {
        "key": "pciutils",
        "kind": "binary",
        "bin": "lspci",
        "purpose": "PCI enumeration (GPU/device listing)",
        "enables": ["hardware_inventory (pci_display)"],
        "platforms": ["linux"],
        "install": {
            "apt": "sudo apt install pciutils",
            "dnf": "sudo dnf install pciutils",
            "pacman": "sudo pacman -S pciutils",
        },
    },
    {
        "key": "dmidecode",
        "kind": "binary",
        "bin": "dmidecode",
        "purpose": "motherboard / BIOS / RAM-DIMM inventory (SMBIOS)",
        "enables": ["hardware_inventory (dmi)"],
        "needs_root_to_run": True,
        "platforms": ["linux"],
        "install": {
            "apt": "sudo apt install dmidecode",
            "dnf": "sudo dnf install dmidecode",
            "pacman": "sudo pacman -S dmidecode",
        },
    },
    {
        "key": "util-linux",
        "kind": "binary",
        "bin": "lscpu",  # also provides lsblk
        "purpose": "CPU details (lscpu) and block-device inventory (lsblk)",
        "enables": ["hardware_inventory (cpu, block_devices)"],
        "platforms": ["linux"],
        "install": {
            "apt": "sudo apt install util-linux",
            "dnf": "sudo dnf install util-linux",
        },
    },
]


def _pkg_manager() -> str | None:
    """Best-guess package manager for the current platform (for install hints)."""
    for binary, name in (
        ("apt-get", "apt"),
        ("dnf", "dnf"),
        ("pacman", "pacman"),
        ("zypper", "zypper"),
        ("brew", "brew"),
        ("winget", "winget"),
    ):
        if which(binary):
            return name
    if IS_MAC:
        return "brew"
    if IS_WINDOWS:
        return "winget"
    return None


def _applies(dep: dict[str, Any]) -> bool:
    return _PLATFORM_KEY in dep["platforms"]


def _present(dep: dict[str, Any]) -> bool:
    if dep["kind"] == "python":
        return importlib.util.find_spec(dep["key"]) is not None
    return which(dep["bin"]) is not None


def _install_hint(dep: dict[str, Any], mgr: str | None) -> str | None:
    install = dep.get("install", {})
    if mgr and mgr in install:
        return install[mgr]
    for fallback in ("pip", "apt", "brew", "winget", "note"):
        if fallback in install:
            return install[fallback]
    return None


def install_hint(key: str) -> str | None:
    """Public helper so collectors/aggregate can enrich a 'not installed' warning."""
    mgr = _pkg_manager()
    for dep in DEPENDENCIES:
        if dep["key"] == key or dep.get("bin") == key:
            return _install_hint(dep, mgr)
    return None


def check() -> dict[str, Any]:
    """Return the presence/absence of every optional backend, with install commands."""
    mgr = _pkg_manager()
    dependencies: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for dep in DEPENDENCIES:
        if not _applies(dep):
            continue
        present = _present(dep)
        entry = {
            "name": dep["key"],
            "provides": dep.get("bin", dep["key"]),
            "purpose": dep["purpose"],
            "enables": dep["enables"],
            "present": present,
            "required": dep.get("required", False),
            "needs_root_to_run": dep.get("needs_root_to_run", False),
            "install": None if present else _install_hint(dep, mgr),
        }
        dependencies.append(entry)
        if not present:
            missing.append(
                {
                    "name": dep["key"],
                    "required": entry["required"],
                    "enables": dep["enables"],
                    "install": entry["install"],
                }
            )
    return {
        "package_manager": mgr,
        "all_present": not missing,
        "missing_count": len(missing),
        "missing": missing,
        "dependencies": dependencies,
    }
