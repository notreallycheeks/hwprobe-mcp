"""Register the agent to start at login: systemd --user (Linux), launchd (macOS), Task Scheduler
(Windows). Everything runs as the current user; nothing needs root."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..util import IS_LINUX, IS_MAC, IS_WINDOWS, run

NAME = "hwprobe-fleet"
MAC_LABEL = "dev.hwprobe.fleet"


def _python() -> str:
    exe = Path(sys.executable).resolve()
    if IS_WINDOWS:
        # pythonw keeps the agent off the taskbar/console.
        pw = exe.with_name("pythonw.exe")
        if pw.exists():
            exe = pw
    return str(exe)


def _argv(port: int | None) -> list[str]:
    args = [_python(), "-m", "hwprobe_mcp.fleet", "serve"]
    if port:
        args += ["--port", str(port)]
    return args


def _state_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / NAME
    if IS_MAC:
        return Path.home() / "Library" / "Logs" / NAME
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / NAME


# --------------------------------------------------------------------------- linux


def _systemd_unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / f"{NAME}.service"


def _install_linux(port: int | None, start: bool) -> dict[str, Any]:
    unit = _systemd_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    exec_start = " ".join(f'"{a}"' if " " in a else a for a in _argv(port))
    unit.write_text(
        "[Unit]\n"
        "Description=hwprobe fleet agent (host telemetry for agents on the tailnet)\n"
        "After=network-online.target tailscaled.service\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "Environment=PYTHONUNBUFFERED=1\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    steps = [run(["systemctl", "--user", "daemon-reload"], timeout=30)]
    steps.append(run(["systemctl", "--user", "enable"] + (["--now"] if start else []) + [f"{NAME}.service"], timeout=60))
    ok = all(s["rc"] == 0 for s in steps)
    notes = [
        f"unit: {unit}",
        "Runs while you are logged in. To keep it running after logout/at boot without a login:",
        f"  sudo loginctl enable-linger {os.environ.get('USER', '$USER')}",
        f"logs: journalctl --user -u {NAME} -f",
    ]
    return {"ok": ok, "platform": "linux", "steps": steps, "notes": notes}


def _uninstall_linux() -> dict[str, Any]:
    steps = [run(["systemctl", "--user", "disable", "--now", f"{NAME}.service"], timeout=60)]
    unit = _systemd_unit_path()
    if unit.exists():
        unit.unlink()
    steps.append(run(["systemctl", "--user", "daemon-reload"], timeout=30))
    return {"ok": True, "platform": "linux", "steps": steps, "notes": [f"removed {unit}"]}


def _status_linux() -> dict[str, Any]:
    r = run(["systemctl", "--user", "is-active", f"{NAME}.service"], timeout=15)
    return {"platform": "linux", "active": r["out"].strip() == "active", "detail": (r["out"] or r["err"]).strip()}


# --------------------------------------------------------------------------- macos


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def _install_mac(port: int | None, start: bool) -> dict[str, Any]:
    p = _plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    logs = _state_dir()
    logs.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": MAC_LABEL,
        "ProgramArguments": _argv(port),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(logs / "agent.log"),
        "StandardErrorPath": str(logs / "agent.log"),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    }
    with open(p, "wb") as fh:
        plistlib.dump(plist, fh)
    steps = []
    uid = os.getuid()
    run(["launchctl", "bootout", f"gui/{uid}", str(p)], timeout=30)  # ignore result: may not be loaded
    if start:
        steps.append(run(["launchctl", "bootstrap", f"gui/{uid}", str(p)], timeout=30))
    ok = all(s["rc"] == 0 for s in steps)
    return {"ok": ok, "platform": "macos", "steps": steps, "notes": [f"launch agent: {p}", f"log: {logs / 'agent.log'}"]}


def _uninstall_mac() -> dict[str, Any]:
    p = _plist_path()
    steps = [run(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], timeout=30)]
    if p.exists():
        p.unlink()
    return {"ok": True, "platform": "macos", "steps": steps, "notes": [f"removed {p}"]}


def _status_mac() -> dict[str, Any]:
    r = run(["launchctl", "print", f"gui/{os.getuid()}/{MAC_LABEL}"], timeout=15)
    return {"platform": "macos", "active": r["rc"] == 0 and "state = running" in r["out"], "detail": "loaded" if r["rc"] == 0 else "not loaded"}


# --------------------------------------------------------------------------- windows


def _install_windows(port: int | None, start: bool) -> dict[str, Any]:
    argv = _argv(port)
    tr = " ".join(f'"{a}"' if " " in a or a.endswith(".exe") else a for a in argv)
    steps = [
        run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED", "/TN", NAME, "/TR", tr], timeout=60),
    ]
    # Let the task run indefinitely and on battery.
    ps = (
        f"$t = Get-ScheduledTask -TaskName '{NAME}'; "
        "$t.Settings.ExecutionTimeLimit = 'PT0S'; $t.Settings.DisallowStartIfOnBatteries = $false; "
        "$t.Settings.StopIfGoingOnBatteries = $false; $t.Settings.RestartCount = 3; $t.Settings.RestartInterval = 'PT1M'; "
        f"Set-ScheduledTask -TaskName '{NAME}' -Settings $t.Settings | Out-Null"
    )
    steps.append(run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=60))
    if start:
        steps.append(run(["schtasks", "/Run", "/TN", NAME], timeout=30))
    ok = steps[0]["rc"] == 0 and (not start or steps[-1]["rc"] == 0)
    return {"ok": ok, "platform": "windows", "steps": steps, "notes": [f"scheduled task '{NAME}' (at logon, as {os.environ.get('USERNAME', 'you')})"]}


def _uninstall_windows() -> dict[str, Any]:
    steps = [run(["schtasks", "/End", "/TN", NAME], timeout=30), run(["schtasks", "/Delete", "/F", "/TN", NAME], timeout=30)]
    return {"ok": True, "platform": "windows", "steps": steps, "notes": ["scheduled task removed"]}


def _status_windows() -> dict[str, Any]:
    r = run(["schtasks", "/Query", "/TN", NAME, "/FO", "LIST"], timeout=30)
    return {"platform": "windows", "active": r["rc"] == 0 and "Running" in r["out"], "detail": r["out"].strip()[-200:] if r["rc"] == 0 else "not installed"}


# --------------------------------------------------------------------------- dispatch


def install(port: int | None = None, start: bool = True) -> dict[str, Any]:
    if IS_LINUX:
        return _install_linux(port, start)
    if IS_MAC:
        return _install_mac(port, start)
    if IS_WINDOWS:
        return _install_windows(port, start)
    return {"ok": False, "platform": sys.platform, "steps": [], "notes": ["unsupported platform"]}


def uninstall() -> dict[str, Any]:
    if IS_LINUX:
        return _uninstall_linux()
    if IS_MAC:
        return _uninstall_mac()
    if IS_WINDOWS:
        return _uninstall_windows()
    return {"ok": False, "platform": sys.platform, "steps": [], "notes": ["unsupported platform"]}


def status() -> dict[str, Any]:
    if IS_LINUX:
        return _status_linux()
    if IS_MAC:
        return _status_mac()
    if IS_WINDOWS:
        return _status_windows()
    return {"platform": sys.platform, "active": False, "detail": "unsupported platform"}


def _spawn_detached(argv: list[str]) -> int:
    """Fallback when no service manager is wanted: start the agent in the background."""
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs).pid
