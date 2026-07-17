"""Linux lm-sensors collector (`sensors -j`) — temps, fans, voltages, power."""

from __future__ import annotations

from typing import Any

from ..util import IS_LINUX, run_json, which

_UNITS = {
    "temperature": "°C",
    "fan": "RPM",
    "voltage": "V",
    "power": "W",
    "energy": "J",
    "current": "A",
    "frequency": "Hz",
    "humidity": "%RH",
}
# order matters: check "temp"/"power"/"curr" before the short "in" voltage prefix
_PREFIXES = (
    ("temp", "temperature"),
    ("fan", "fan"),
    ("power", "power"),
    ("energy", "energy"),
    ("curr", "current"),
    ("freq", "frequency"),
    ("humidity", "humidity"),
    ("in", "voltage"),
)


def available() -> bool:
    return IS_LINUX and which("sensors") is not None


def raw(warns: list[str]) -> dict[str, Any] | None:
    data, warn = run_json(["sensors", "-j"])
    if warn:
        warns.append(warn)
    return data if isinstance(data, dict) else None


def _kind(subfeature: str) -> str:
    for prefix, kind in _PREFIXES:
        if subfeature.startswith(prefix):
            return kind
    return "other"


def normalized(warns: list[str]) -> list[dict[str, Any]]:
    """Flatten `sensors -j` into a flat list of readings for easy agent consumption."""
    data = raw(warns)
    if not data:
        return []
    readings: list[dict[str, Any]] = []
    for chip, body in data.items():
        if not isinstance(body, dict):
            continue
        adapter = body.get("Adapter")
        for feature, sub in body.items():
            if feature == "Adapter" or not isinstance(sub, dict):
                continue
            # A reading's live value is normally the *_input subfeature, but some
            # drivers (ACPI power_meter, intel-rapl) only expose *_average. Pick one
            # value per base subfeature, preferring _input, else falling back to _average.
            inputs: dict = {}
            averages: dict = {}
            for key, val in sub.items():
                if not isinstance(val, (int, float)):
                    continue
                if key.endswith("_input"):
                    inputs[key[: -len("_input")]] = (key, val)
                elif key.endswith("_average"):
                    averages[key[: -len("_average")]] = (key, val)
            for base in inputs.keys() | averages.keys():
                key, val = inputs.get(base) or averages[base]
                kind = _kind(key)
                readings.append(
                    {
                        "chip": chip,
                        "adapter": adapter,
                        "label": feature,
                        "type": kind,
                        "value": val,
                        "unit": _UNITS.get(kind, ""),
                    }
                )
    return readings
