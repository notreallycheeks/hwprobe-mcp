"""Entry point: `hwprobe-mcp` runs the MCP server; `--selftest` dumps collector JSON."""

from __future__ import annotations

import json
import sys


def _selftest() -> int:
    # Imports only the pure collectors — no `mcp` dependency needed.
    from . import aggregate

    report = {
        "check_dependencies": aggregate.check_dependencies(),
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


def _doctor() -> int:
    # Human-readable dependency report: what's present, what's missing, how to install it.
    from . import aggregate

    rep = aggregate.check_dependencies()["data"]
    mgr = rep.get("package_manager") or "unknown"
    print(f"hwprobe-mcp dependency check  (package manager: {mgr})\n")
    for dep in rep["dependencies"]:
        mark = "✓" if dep["present"] else "✗"
        flags = ""
        if dep["required"]:
            flags += " (required)"
        if dep.get("needs_root_to_run"):
            flags += " [needs root]"
        print(f"  {mark} {dep['name']:<14} {dep['purpose']}{flags}")
        if not dep["present"] and dep.get("install"):
            print(f"      → install: {dep['install']}")
    missing = rep["missing"]
    print()
    if missing:
        unlocks = sorted({e for m in missing for e in m["enables"]})
        print(f"{len(missing)} missing. Installing them unlocks: {', '.join(unlocks)}")
    else:
        print("All optional backends present.")
    # Only a MISSING REQUIRED dependency is a real failure; optional gaps exit 0.
    return 1 if any(m["required"] for m in missing) else 0


def main() -> None:
    argv = sys.argv[1:]
    if "--selftest" in argv:
        raise SystemExit(_selftest())
    if "--doctor" in argv:
        raise SystemExit(_doctor())
    if "--version" in argv or "-V" in argv:
        from . import __version__

        print(__version__)
        raise SystemExit(0)
    if "--help" in argv or "-h" in argv:
        print(
            "hwprobe-mcp — hardware inventory + live sensors for AI agents (MCP)\n\n"
            "Usage:\n"
            "  hwprobe-mcp             Run the MCP server over stdio\n"
            "  hwprobe-mcp --doctor    List optional dependencies + how to install any missing\n"
            "  hwprobe-mcp --selftest  Dump all collector output as JSON and exit\n"
            "  hwprobe-mcp --version   Print version and exit"
        )
        raise SystemExit(0)

    from .server import run

    run()


if __name__ == "__main__":
    main()
