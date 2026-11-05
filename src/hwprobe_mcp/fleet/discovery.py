"""Find the other machines: Tailscale peers (via `tailscale status --json`) plus a static list."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..util import IS_MAC, IS_WINDOWS, run, which
from . import DEFAULT_PORT

MOBILE_OS = {"ios", "android"}


def config_path() -> Path:
    env = os.environ.get("HWPROBE_FLEET_CONFIG")
    if env:
        return Path(env)
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA") or Path.home())
        return base / "hwprobe" / "fleet.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "hwprobe" / "fleet.json"


def load_config() -> dict[str, Any]:
    """Optional `fleet.json`:
    {"port": 7788, "hub": "http://hub-host:7790", "token": "...",
     "peers": [{"hostname": "x", "ip": "100.1.2.3"}], "exclude": ["phone"]}
    Static peers are for machines Tailscale does not list; `hub`/`token` point agents and
    clients at a central hub (see `hwprobe-fleet hub`)."""
    p = config_path()
    try:
        if p.exists():
            d = json.loads(p.read_text("utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def tailscale_cli() -> str | None:
    env = os.environ.get("HWPROBE_TAILSCALE_CLI")
    if env and Path(env).exists():
        return env
    p = which("tailscale")
    if p:
        return p
    cands = ["/usr/bin/tailscale", "/usr/local/bin/tailscale"]
    if IS_MAC:
        cands += [
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/opt/homebrew/bin/tailscale",
        ]
    if IS_WINDOWS:
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                cands.append(str(Path(base) / "Tailscale" / "tailscale.exe"))
    for c in cands:
        if Path(c).exists():
            return c
    return None


def tailscale_status() -> dict[str, Any] | None:
    cli = tailscale_cli()
    if not cli:
        return None
    r = run([cli, "status", "--json"], timeout=10)
    if r["rc"] != 0 or not r["out"].strip():
        return None
    try:
        return json.loads(r["out"])
    except Exception:
        return None


def _ipv4(ips: list[str] | None) -> str | None:
    for ip in ips or []:
        if "." in ip:
            return ip
    return None


def parse_status(status: dict[str, Any] | None, include_mobile: bool = False) -> list[dict[str, Any]]:
    """Normalize `tailscale status --json` into peer rows, self first."""
    rows: list[dict[str, Any]] = []
    if not status:
        return rows
    me = status.get("Self") or {}
    if me:
        rows.append(
            {
                "hostname": me.get("HostName"),
                "dns_name": (me.get("DNSName") or "").rstrip("."),
                "ip": _ipv4(me.get("TailscaleIPs")),
                "os": me.get("OS"),
                "online": True,
                "self": True,
            }
        )
    for p in (status.get("Peer") or {}).values():
        os_name = (p.get("OS") or "").lower()
        if not include_mobile and os_name in MOBILE_OS:
            continue
        rows.append(
            {
                "hostname": p.get("HostName"),
                "dns_name": (p.get("DNSName") or "").rstrip("."),
                "ip": _ipv4(p.get("TailscaleIPs")),
                "os": p.get("OS"),
                "online": bool(p.get("Online")),
                "self": False,
                "last_seen": p.get("LastSeen"),
            }
        )
    return rows


def self_tailscale_ip() -> str | None:
    st = tailscale_status()
    return _ipv4((st or {}).get("Self", {}).get("TailscaleIPs")) if st else None


def peers(include_mobile: bool = False, include_offline: bool = True) -> list[dict[str, Any]]:
    """Tailscale peers merged with static config peers; excluded names dropped."""
    cfg = load_config()
    rows = parse_status(tailscale_status(), include_mobile=include_mobile)
    known = {r["ip"] for r in rows if r.get("ip")}
    for sp in cfg.get("peers") or []:
        if not isinstance(sp, dict) or not sp.get("ip") or sp["ip"] in known:
            continue
        rows.append(
            {
                "hostname": sp.get("hostname") or sp["ip"],
                "dns_name": sp.get("dns_name", ""),
                "ip": sp["ip"],
                "os": sp.get("os"),
                "online": True,
                "self": False,
                "static": True,
            }
        )
    excl = {str(x).lower() for x in cfg.get("exclude") or []}
    rows = [r for r in rows if (r.get("hostname") or "").lower() not in excl]
    if not include_offline:
        rows = [r for r in rows if r.get("online")]
    return rows


def port() -> int:
    env = os.environ.get("HWPROBE_FLEET_PORT")
    if env and env.isdigit():
        return int(env)
    p = load_config().get("port")
    return int(p) if isinstance(p, int) and 0 < p < 65536 else DEFAULT_PORT
