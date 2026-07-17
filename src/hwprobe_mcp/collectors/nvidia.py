"""NVIDIA GPU collector via `nvidia-smi --query-gpu ... --format=csv`."""

from __future__ import annotations

from typing import Any

from ..util import run, which

# Mixes inventory (name, driver, bus id, vbios) with live telemetry in one invocation.
FIELDS = [
    "index",
    "name",
    "pci.bus_id",
    "driver_version",
    "vbios_version",
    "pstate",
    "temperature.gpu",
    "utilization.gpu",
    "utilization.memory",
    "memory.total",
    "memory.used",
    "memory.free",
    "power.draw",
    "power.limit",
    "clocks.sm",
    "clocks.mem",
    "fan.speed",
]

_MISSING = {"[not supported]", "[n/a]", "n/a", "", "[unknown error]", "[insufficient permissions]"}


def available() -> bool:
    return which("nvidia-smi") is not None


def _num(v: str) -> Any:
    v = v.strip()
    if v.lower() in _MISSING:
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def query(warns: list[str]) -> list[dict[str, Any]]:
    r = run(["nvidia-smi", f"--query-gpu={','.join(FIELDS)}", "--format=csv,noheader,nounits"])
    if r["rc"] != 0:
        msg = (r["err"].strip() or r["out"].strip() or f"rc={r['rc']}").splitlines()[0]
        warns.append(f"nvidia-smi: {msg[:200]}")
        return []
    gpus: list[dict[str, Any]] = []
    for line in r["out"].strip().splitlines():
        vals = [v.strip() for v in line.split(",")]
        if len(vals) != len(FIELDS):
            warns.append(f"nvidia-smi: unexpected column count ({len(vals)} != {len(FIELDS)})")
            continue
        gpus.append({field.replace(".", "_"): _num(v) for field, v in zip(FIELDS, vals)})
    return gpus
