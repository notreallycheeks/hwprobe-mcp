"""`hwprobe-fleet` — see every host on the tailnet, run the agent, or install it as a service."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .. import __version__
from . import DEFAULT_PORT, client, discovery

HELP = f"""hwprobe-fleet {__version__} — host telemetry (CPU/RAM/disk/GPU + Claude usage) across your tailnet

Usage:
  hwprobe-fleet [status] [--json] [--timeout S] [--mobile]   Table (or JSON) of every host
  hwprobe-fleet self [--json]                                 This host only (no network)
  hwprobe-fleet claude [--json] [--refresh]                   Claude usage for accounts on this host
  hwprobe-fleet peers                                         Who Tailscale says is on the tailnet
  hwprobe-fleet pick [--gpu] [--ram GB] [--disk GB] [--json]  Rank hosts by headroom for new work
  hwprobe-fleet serve [--port N] [--bind ADDR ...] [--allow CIDR ...] [--no-claude]
                      [--hub URL] [--token T] [--push-interval S]
                                                              Run the agent in the foreground
  hwprobe-fleet hub [--port 7790] [--bind 0.0.0.0] [--token T | --insecure] [--state DIR]
                                                              Run the central hub (reports in, MCP + dashboard out)
  hwprobe-fleet install [--port N] [--no-start]               Start the agent at login (systemd/launchd/schtasks)
  hwprobe-fleet uninstall                                     Remove the login service
  hwprobe-fleet service-status                                Is the login service running?

Default agent port: {DEFAULT_PORT}; hub port: 7790.
Config: {discovery.config_path()} (optional; port, hub, token, static peers, excludes).
Env: HWPROBE_FLEET_HUB, HWPROBE_FLEET_TOKEN, HWPROBE_FLEET_PORT, HWPROBE_CLAUDE_CONFIG_DIRS.
Endpoints on each host: /v1/stats  /v1/fleet  /v1/claude  /healthz  and a dashboard at /
With a hub configured, `status`/`pick` read the hub; otherwise they poll every tailnet peer.
"""


def _print(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hwprobe-fleet", add_help=False)
    ap.add_argument("command", nargs="?", default="status")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=float, default=4.0)
    ap.add_argument("--mobile", action="store_true", help="include phones/tablets in discovery")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--bind", action="append", default=None, help="address to listen on (default: 127.0.0.1 and the Tailscale IP)")
    ap.add_argument("--allow", action="append", default=None, help="extra source CIDR to accept")
    ap.add_argument("--no-claude", action="store_true", help="do not collect Claude usage")
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--hub", default=None, help="hub URL (default: $HWPROBE_FLEET_HUB or fleet.json)")
    ap.add_argument("--token", default=None, help="hub bearer token (default: $HWPROBE_FLEET_TOKEN or fleet.json)")
    ap.add_argument("--local", action="store_true", help="ignore the hub; poll peers directly")
    ap.add_argument("--push-interval", type=float, default=15.0)
    ap.add_argument("--insecure", action="store_true", help="hub: no token (not recommended)")
    ap.add_argument("--state", default=None, help="hub: state directory")
    ap.add_argument("--stale-after", type=float, default=90.0, help="hub: seconds before a host is 'stale'")
    ap.add_argument("--gpu", action="store_true", help="pick: require GPU telemetry")
    ap.add_argument("--ram", type=float, default=0.0, help="pick: minimum free RAM in GB")
    ap.add_argument("--disk", type=float, default=0.0, help="pick: minimum free disk in GB")
    ap.add_argument("--max-cpu", type=float, default=85.0, help="pick: maximum CPU %% busy")
    ap.add_argument("--max-claude", type=float, default=90.0, help="pick: maximum Claude 5h %% used")
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("-V", "--version", action="store_true")
    a = ap.parse_args(argv)

    if a.help or a.command == "help":
        print(HELP)
        return 0
    if a.version:
        print(__version__)
        return 0

    cmd = a.command

    def _fleet():
        return client.fleet_status(
            timeout=a.timeout, port=a.port, include_mobile=a.mobile, hub=a.hub, hub_token=a.token, prefer_hub=not a.local
        )

    if cmd == "status":
        fleet = _fleet()
        if a.json:
            _print(fleet)
        else:
            print(client.render_table(fleet))
        return 0 if fleet["summary"]["ok"] else 1
    if cmd == "pick":
        from . import rank

        ranked = rank.rank_hosts(_fleet(), need_gpu=a.gpu, min_free_ram_gb=a.ram, min_free_disk_gb=a.disk, max_cpu_percent=a.max_cpu, max_claude_session_percent=a.max_claude)
        if a.json:
            _print({"recommended": next((r["hostname"] for r in ranked if r["eligible"]), None), "hosts": ranked})
        else:
            for r in ranked:
                mark = "✓" if r["eligible"] else "✗"
                print(f"{mark} {r['hostname']:<22} score {r['score']:>5}  {'; '.join(r['reasons'])}")
        return 0 if any(r["eligible"] for r in ranked) else 1
    if cmd == "self":
        from . import claude_usage, stats

        snap = stats.snapshot(claude=None if a.no_claude else claude_usage.collect())
        if a.json:
            _print(snap)
        else:
            fake = {"nodes": [{"hostname": snap["host"]["hostname"], "os": snap["host"]["os"], "self": True, "status": "ok", "stats": snap, "claude_accounts": claude_usage.headline(snap.get("claude"))}], "summary": {"ok": 1, "total": 1}, "port": a.port or discovery.port()}
            print(client.render_table(fake))
        return 0
    if cmd == "claude":
        from . import claude_usage

        d = claude_usage.collect(force=a.refresh)
        if a.json:
            _print(d)
        else:
            for row in claude_usage.headline(d):
                who = row.get("email") or "(orca)"
                if row.get("error"):
                    print(f"{who}: {row['error']}")
                    continue
                scoped = "".join(f"  {m}: {p:.0f}%" for m, p in (row.get("scoped") or {}).items() if p is not None)
                print(f"{who} [{row.get('subscription') or '?'}]  5h: {row['session_percent']:.0f}%  7d: {row['weekly_percent']:.0f}%{scoped}")
                if row.get("session_resets_at"):
                    print(f"    session resets {row['session_resets_at']}   week resets {row.get('weekly_resets_at')}")
            orca = d.get("orca") or {}
            if orca.get("providers"):
                for prov, e in orca["providers"].items():
                    if prov == "claude" or e.get("status") != "ok":
                        continue
                    print(f"orca/{prov}: 5h {e.get('session', {}).get('percent')}%  7d {e.get('weekly', {}).get('percent')}%")
            for w in d.get("warnings") or []:
                print(f"note: {w}")
        return 0
    if cmd == "peers":
        rows = discovery.peers(include_mobile=a.mobile)
        if a.json:
            _print(rows)
        else:
            for r in rows:
                print(f"{r['hostname']:<22} {r.get('ip') or '-':<16} {r.get('os') or '?':<8} {'online' if r.get('online') else 'offline'}{'  (self)' if r.get('self') else ''}")
        return 0
    if cmd == "serve":
        from . import server

        server.serve(port=a.port, binds=a.bind, allow=a.allow, claude=not a.no_claude, hub=a.hub, hub_token=a.token, push_interval=a.push_interval)
        return 0
    if cmd == "hub":
        from . import hub

        hub.serve_hub(port=a.port or hub.DEFAULT_HUB_PORT, bind=(a.bind or ["0.0.0.0"])[0], token=a.token, insecure=a.insecure, stale_after=a.stale_after, state=a.state)
        return 0
    if cmd in ("install", "uninstall", "service-status"):
        from . import service

        res = service.install(port=a.port, start=not a.no_start) if cmd == "install" else service.uninstall() if cmd == "uninstall" else service.status()
        if a.json:
            _print(res)
        else:
            if cmd == "service-status":
                print(f"{res['platform']}: {'running' if res.get('active') else 'not running'} ({res.get('detail')})")
            else:
                print(f"{cmd}: {'ok' if res.get('ok') else 'FAILED'} ({res['platform']})")
                for s in res.get("steps") or []:
                    if s.get("rc") not in (0, None):
                        print(f"  ! {s['cmd']}: rc={s['rc']} {(s.get('err') or s.get('out') or '').strip()[:200]}")
                for n in res.get("notes") or []:
                    print(f"  {n}")
                if cmd == "install" and res.get("ok"):
                    port = a.port or discovery.port()
                    print(f"  dashboard: http://127.0.0.1:{port}/   fleet JSON: http://127.0.0.1:{port}/v1/fleet")
        return 0 if res.get("ok", True) else 1
    print(f"unknown command: {cmd}\n", file=sys.stderr)
    print(HELP, file=sys.stderr)
    return 2
