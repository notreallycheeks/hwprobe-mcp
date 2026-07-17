"""Shared helpers: never-raise subprocess runners, platform flags, response envelope."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from typing import Any

PLATFORM = platform.system()  # "Linux" | "Darwin" | "Windows"
IS_LINUX = PLATFORM == "Linux"
IS_MAC = PLATFORM == "Darwin"
IS_WINDOWS = PLATFORM == "Windows"


def which(name: str) -> str | None:
    """Absolute path to an executable, or None if not on PATH."""
    return shutil.which(name)


def is_root() -> bool:
    """True if running with root/administrator privileges (best effort)."""
    try:
        if hasattr(os, "geteuid"):
            return os.geteuid() == 0
        # Windows: assume non-admin unless we can prove otherwise.
        import ctypes  # noqa: PLC0415

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def run(cmd: list[str] | str, timeout: float = 15.0, shell: bool = False) -> dict[str, Any]:
    """Run a command without ever raising.

    Returns {"rc", "out", "err", "cmd", "error"?}. `error` is one of
    "not_found" | "timeout" | "exception" when the process could not complete.
    """
    disp = cmd if isinstance(cmd, str) else " ".join(cmd)
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, shell=shell, capture_output=True, text=True, timeout=timeout
        )
        return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr, "cmd": disp}
    except FileNotFoundError:
        return {"rc": 127, "out": "", "err": "binary not found", "cmd": disp, "error": "not_found"}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "out": "", "err": f"timeout after {timeout}s", "cmd": disp, "error": "timeout"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"rc": -1, "out": "", "err": str(exc), "cmd": disp, "error": "exception"}


def run_json(
    cmd: list[str] | str, timeout: float = 15.0, shell: bool = False
) -> tuple[Any | None, str | None]:
    """Run a command and parse stdout as JSON.

    Returns (data, warning). Tools like `smartctl` exit non-zero (bitmask) yet still
    emit valid JSON, so we parse whenever there is output regardless of return code.
    """
    r = run(cmd, timeout=timeout, shell=shell)
    binary = (r["cmd"].split() or ["?"])[0]
    if r.get("error") == "not_found":
        return None, f"{binary}: not installed"
    if r.get("error") == "timeout":
        return None, f"{binary}: {r['err']}"
    if not r["out"].strip():
        return None, f"{binary}: no output (rc={r['rc']}) {r['err'].strip()[:160]}".strip()
    try:
        data = json.loads(r["out"])
    except json.JSONDecodeError as exc:
        return None, f"{binary}: invalid JSON ({exc})"
    warn = None if r["rc"] == 0 else f"{binary}: nonzero rc={r['rc']} (output parsed anyway)"
    return data, warn


def envelope(
    sources: str | list[str],
    data: Any,
    warnings: list[str] | None = None,
    ok: bool = True,
) -> dict[str, Any]:
    """Standard tool response shape shared by every hwprobe-mcp tool."""
    if isinstance(sources, str):
        sources = [sources]
    return {
        "ok": bool(ok),
        "platform": PLATFORM,
        "sources": sources,
        "warnings": warnings or [],
        "data": data,
    }
