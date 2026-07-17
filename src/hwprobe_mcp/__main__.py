"""Entry point: `hwprobe-mcp` runs the MCP server; `--selftest` dumps collector JSON."""

from __future__ import annotations

import json
import sys


def _selftest() -> int:
    # Imports only the pure collectors — no `mcp` dependency needed.
    from . import aggregate

    report = {
        "hardware_inventory": aggregate.hardware_inventory(),
        "live_sensors": aggregate.live_sensors(),
        "cpu_status": aggregate.cpu_status(),
        "gpu_status": aggregate.gpu_status(),
        "disk_health": aggregate.disk_health(),
        "system_snapshot": aggregate.system_snapshot(),
    }
    json.dump(report, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def main() -> None:
    argv = sys.argv[1:]
    if "--selftest" in argv:
        raise SystemExit(_selftest())
    if "--version" in argv or "-V" in argv:
        from . import __version__

        print(__version__)
        raise SystemExit(0)
    if "--help" in argv or "-h" in argv:
        print(
            "hwprobe-mcp — hardware inventory + live sensors for AI agents (MCP)\n\n"
            "Usage:\n"
            "  hwprobe-mcp             Run the MCP server over stdio\n"
            "  hwprobe-mcp --selftest  Dump all collector output as JSON and exit\n"
            "  hwprobe-mcp --version   Print version and exit"
        )
        raise SystemExit(0)

    from .server import run

    run()


if __name__ == "__main__":
    main()
