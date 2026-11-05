"""Read `/v1/stats` from every peer in parallel and render the result."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import SCHEMA_VERSION, discovery, hub_client
from .claude_usage import headline


def fetch_stats(ip: str, port: int, timeout: float = 4.0) -> tuple[dict[str, Any] | None, str | None]:
    url = f"http://{ip}:{port}/v1/stats"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "hwprobe-fleet"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (ConnectionRefusedError, urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        text = str(reason).lower()
        if "refused" in text:
            return None, "no-agent"
        if "timed out" in text or isinstance(reason, (socket.timeout, TimeoutError)):
            return None, "timeout"
        return None, str(reason)[:120]
    except Exception as exc:
        return None, str(exc)[:120]


def _node(peer: dict[str, Any], port: int, timeout: float) -> dict[str, Any]:
    node: dict[str, Any] = {
        "hostname": peer.get("hostname"),
        "ip": peer.get("ip"),
        "os": peer.get("os"),
        "self": bool(peer.get("self")),
        "tailscale_online": bool(peer.get("online")),
        "status": "unknown",
        "error": None,
        "stats": None,
    }
    if not peer.get("online"):
        node["status"] = "offline"
        node["error"] = "not connected to the tailnet"
        return node
    if not peer.get("ip"):
        node["status"] = "error"
        node["error"] = "no Tailscale IPv4"
        return node
    data, err = fetch_stats(peer["ip"], port, timeout)
    if data:
        node["status"] = "ok"
        node["stats"] = data
        node["claude_accounts"] = headline(data.get("claude"))
    else:
        node["status"] = "no-agent" if err == "no-agent" else "unreachable" if err == "timeout" else "error"
        node["error"] = err
    return node


def fleet_status(
    *,
    timeout: float = 4.0,
    port: int | None = None,
    include_self: bool = True,
    self_stats: dict[str, Any] | None = None,
    include_mobile: bool = False,
    hub: str | None = None,
    hub_token: str | None = None,
    prefer_hub: bool = True,
) -> dict[str, Any]:
    """Every host on the tailnet with its stats (or why they are missing).

    With a hub configured (argument, `HWPROBE_FLEET_HUB`, or `hub` in fleet.json) the hub's
    aggregated view is returned; if the hub cannot be reached, falls back to asking each peer
    directly (which needs Tailscale on this machine)."""
    hub_url = hub or (hub_client.hub_url() if prefer_hub else None)
    if hub_url:
        doc, err = hub_client.get_fleet(hub_url, hub_token or hub_client.hub_token(), timeout=max(timeout, 6.0))
        if doc:
            doc["via"] = "hub"
            doc["hub"] = hub_url
            return doc
        hub_error = err
    else:
        hub_error = None
    port = port or discovery.port()
    peers = discovery.peers(include_mobile=include_mobile)
    me = next((p for p in peers if p.get("self")), None)
    todo = [p for p in peers if include_self or not p.get("self")]
    nodes: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(16, len(todo) or 1))) as pool:
        futs = []
        for p in todo:
            if p.get("self") and self_stats is not None:
                futs.append(pool.submit(lambda p=p: {**_node({**p, "online": False}, port, timeout), "status": "ok", "error": None, "stats": self_stats, "claude_accounts": headline(self_stats.get("claude")), "tailscale_online": True}))
            else:
                futs.append(pool.submit(_node, p, port, timeout))
        nodes = [f.result() for f in futs]
    nodes.sort(key=lambda n: (not n["self"], {"ok": 0, "no-agent": 1, "unreachable": 2, "error": 3, "offline": 4}.get(n["status"], 9), n["hostname"] or ""))
    for n in nodes:
        ts = (n.get("stats") or {}).get("ts")
        n["age_s"] = round(time.time() - ts, 1) if ts else None
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": time.time(),
        "from_host": (me or {}).get("hostname") or socket.gethostname(),
        "port": port,
        "tailscale": bool(peers),
        "via": "peers",
        "hub": hub_url,
        "hub_error": hub_error,
        "nodes": nodes,
        "summary": {
            "total": len(nodes),
            "ok": sum(1 for n in nodes if n["status"] == "ok"),
            "no_agent": sum(1 for n in nodes if n["status"] == "no-agent"),
            "offline": sum(1 for n in nodes if n["status"] == "offline"),
        },
    }


# --------------------------------------------------------------------------- rendering


def _fmt_pct(v: Any) -> str:
    return "-" if v is None else f"{v:.0f}%"


def _fmt_mem(block: dict[str, Any] | None) -> str:
    if not block or block.get("total_gb") is None:
        return "-"
    return f"{block.get('used_gb', 0):.0f}/{block['total_gb']:.0f}G ({block.get('percent', 0):.0f}%)"


def _fmt_disk(block: dict[str, Any] | None) -> str:
    prim = (block or {}).get("primary")
    if not prim:
        return "-"
    return f"{prim['free_gb']:.0f}G free/{prim['total_gb']:.0f}G ({prim['percent']:.0f}% used)"


def _fmt_gpu(block: dict[str, Any] | None) -> str:
    gpus = (block or {}).get("gpus") or []
    if not gpus:
        return "-"
    parts = []
    for g in gpus:
        util = _fmt_pct(g.get("utilization_percent"))
        mem = ""
        if g.get("memory_total_mb"):
            mem = f" {g['memory_used_mb'] or 0}/{g['memory_total_mb']}MB"
        elif g.get("memory_used_mb") is not None:
            mem = f" {g['memory_used_mb']}MB"
        name = (g.get("name") or "GPU").replace("NVIDIA ", "").replace("GeForce ", "")
        parts.append(f"{name} {util}{mem}")
    return "; ".join(parts)


def _fmt_claude(rows: list[dict[str, Any]] | None) -> str:
    if not rows:
        return "-"
    parts = []
    for r in rows:
        who = (r.get("email") or "orca").split("@")[0]
        if r.get("error"):
            parts.append(f"{who}: {r['error'][:40]}")
            continue
        scoped = "".join(f" {m[:1].lower()}:{_fmt_pct(p)}" for m, p in (r.get("scoped") or {}).items() if p is not None)
        parts.append(f"{who} 5h:{_fmt_pct(r.get('session_percent'))} 7d:{_fmt_pct(r.get('weekly_percent'))}{scoped}")
    return " | ".join(parts)


def render_table(fleet: dict[str, Any]) -> str:
    cols = ["HOST", "OS", "STATUS", "CPU", "RAM", "DISK", "GPU", "CLAUDE (5h / 7d / per-model)", "AGE"]
    rows: list[list[str]] = []
    now = time.time()
    for n in fleet.get("nodes") or []:
        s = n.get("stats") or {}
        age = "-" if not s.get("ts") else f"{int(now - s['ts'])}s"
        status = n["status"] if n["status"] == "ok" else f"{n['status']}{': ' + n['error'][:30] if n.get('error') and n['status'] == 'error' else ''}"
        rows.append(
            [
                (n.get("hostname") or "?") + ("*" if n.get("self") else ""),
                (n.get("os") or "?")[:7],
                status,
                _fmt_pct((s.get("cpu") or {}).get("percent")),
                _fmt_mem(s.get("memory")),
                _fmt_disk(s.get("disks")),
                _fmt_gpu(s.get("gpu")),
                _fmt_claude(n.get("claude_accounts")),
                age,
            ]
        )
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    lines = ["  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))]
    lines.append("  ".join("-" * w for w in widths))
    for r in rows:
        lines.append("  ".join(v.ljust(widths[i]) for i, v in enumerate(r)))
    sm = fleet.get("summary") or {}
    lines.append("")
    lines.append(
        f"{sm.get('ok', 0)}/{sm.get('total', 0)} hosts reporting"
        + (f", {sm['no_agent']} online without the agent" if sm.get("no_agent") else "")
        + (f", {sm['offline']} offline" if sm.get("offline") else "")
        + f"  (* = this host; via {fleet.get('via') or 'peers'}"
        + (f" {fleet.get('hub') or ''}".rstrip() if fleet.get("via") == "hub" else f", port {fleet.get('port')}")
        + (f"; hub unreachable: {fleet['hub_error']}" if fleet.get("hub_error") else "")
        + ")"
    )
    return "\n".join(lines)
