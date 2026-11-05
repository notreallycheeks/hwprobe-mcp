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


@mcp.tool()
def fleet_status(timeout_s: float = 4.0) -> dict[str, Any]:
    """CPU %, RAM, disk, GPU and Claude-usage figures for EVERY machine on the tailnet that
    runs the hwprobe-fleet agent — the view to consult before delegating work to another
    machine. Reads the fleet hub when one is configured, otherwise asks each Tailscale peer
    directly. Hosts without the agent show status `no-agent`; offline ones `offline`."""
    from .fleet import client

    return client.fleet_status(timeout=timeout_s)


@mcp.tool()
def pick_host(
    need_gpu: bool = False,
    min_free_ram_gb: float = 0.0,
    min_free_disk_gb: float = 0.0,
    max_cpu_percent: float = 85.0,
    max_claude_session_percent: float = 90.0,
    exclude: list[str] | None = None,
) -> dict[str, Any]:
    """Rank fleet hosts by headroom for new work: `recommended` plus every host with
    `eligible`, a 0-100 `score` (higher = more idle) and human-readable `reasons`. Busy CPUs,
    stale reports and Claude accounts near their 5-hour limit make a host ineligible."""
    from .fleet import client, rank

    ranked = rank.rank_hosts(
        client.fleet_status(),
        need_gpu=need_gpu,
        min_free_ram_gb=min_free_ram_gb,
        min_free_disk_gb=min_free_disk_gb,
        max_cpu_percent=max_cpu_percent,
        max_claude_session_percent=max_claude_session_percent,
        exclude=exclude,
    )
    best = next((r for r in ranked if r["eligible"]), None)
    return {"recommended": best["hostname"] if best else None, "hosts": ranked}


@mcp.tool()
def claude_usage(refresh: bool = False) -> dict[str, Any]:
    """Claude Code usage for every account signed in on THIS machine: email, plan, 5-hour and
    7-day window percentages, per-model weekly windows and reset times, plus the rate limits
    Orca tracks when Orca is installed. `refresh=True` bypasses the 2-minute cache."""
    from .fleet import claude_usage as cu

    return cu.collect(force=refresh)


def run() -> None:
    """Run the MCP server over stdio (default transport)."""
    mcp.run()
