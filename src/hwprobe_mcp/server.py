"""FastMCP server. Each tool is a thin, well-documented wrapper over `aggregate`."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import aggregate

mcp = FastMCP("hwprobe-mcp")


@mcp.tool()
def hardware_inventory() -> dict[str, Any]:
    """Deep STATIC hardware inventory of this machine.

    CPU model/cores/arch/cache, RAM total + DIMM layout, disks (model/serial/size),
    GPUs, motherboard/BIOS, and OS/platform. Aggregates `platform`, `psutil`, and
    native tools (lscpu/lsblk/lspci/dmidecode on Linux; system_profiler on macOS;
    CIM/WMI on Windows). Motherboard/BIOS/DIMM detail needs root; anything unreadable
    is reported in `warnings`.
    """
    return aggregate.hardware_inventory()


@mcp.tool()
def live_sensors() -> dict[str, Any]:
    """LIVE sensor snapshot right now.

    CPU/component temperatures, fan RPM, battery, plus voltages/power via lm-sensors
    (Linux) and NVIDIA GPU temp/power/utilization when a driver is present.
    """
    return aggregate.live_sensors()


@mcp.tool()
def cpu_status() -> dict[str, Any]:
    """CPU identity + LIVE load: per-core utilization %, per-core frequency, load
    average, and core temperatures.
    """
    return aggregate.cpu_status()


@mcp.tool()
def gpu_status() -> dict[str, Any]:
    """GPU inventory + LIVE telemetry. NVIDIA via nvidia-smi (name, driver, temp,
    utilization, power, clocks, memory). Returns a warning if no GPU/driver is present.
    """
    return aggregate.gpu_status()


@mcp.tool()
def disk_health() -> dict[str, Any]:
    """Per-disk SMART health via smartctl: model, serial, firmware, capacity,
    temperature, SMART pass/fail, power-on hours, power cycles. Full data needs root.
    """
    return aggregate.disk_health()


@mcp.tool()
def system_snapshot() -> dict[str, Any]:
    """EVERYTHING in one call: inventory + live sensors + CPU/GPU/memory/disk/network.
    The 'tell me everything about this machine' tool.
    """
    return aggregate.system_snapshot()


@mcp.tool()
def check_dependencies() -> dict[str, Any]:
    """Report which optional backends hwprobe uses are installed vs missing, what each one
    unlocks, and the EXACT command to install any that are missing (auto-detects the
    platform's package manager). Call this when another tool returns empty data or a
    'not installed' warning — it tells the user precisely what to install.
    """
    return aggregate.check_dependencies()


def run() -> None:
    """Run the MCP server over stdio (default transport)."""
    mcp.run()
