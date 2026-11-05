"""Fleet hub: one central place every agent reports to, and one MCP endpoint every AI agent
on the tailnet can call to see which machines are busy and which are idle.

Agents (`hwprobe-fleet serve --hub http://host:7790`) POST their snapshot every few seconds.
The hub never connects out, so it runs fine inside Docker behind NAT. It serves:

  POST /v1/report          an agent's snapshot (bearer token)
  GET  /v1/fleet           the aggregated view, same shape as the peer-to-peer client
  GET  /v1/hosts/<name>    one host's last full report
  GET  /healthz            liveness (no token)
  GET  /                   dashboard (no token for the page; the page sends the token)
  *    /mcp                MCP over streamable HTTP: fleet_status, fleet_table, pick_host,
                           host_stats, claude_usage
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .. import __version__
from . import SCHEMA_VERSION, client, rank, service
from .claude_usage import headline

log = logging.getLogger("hwprobe.hub")

DEFAULT_HUB_PORT = 7790


def state_dir() -> Path:
    env = os.environ.get("HWPROBE_FLEET_STATE")
    return Path(env) if env else service._state_dir()


class Store:
    """Last report per host, persisted so a restart does not forget the fleet."""

    def __init__(self, path: Path | None, stale_after: float = 90.0, lost_after: float = 900.0):
        self.path = path
        self.stale_after = stale_after
        self.lost_after = lost_after
        self._hosts: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.started = time.time()
        self.reports = 0
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text("utf-8"))
            if isinstance(d, dict):
                self._hosts = {k: v for k, v in d.items() if isinstance(v, dict)}
        except Exception as exc:
            log.warning("could not load %s: %s", self.path, exc)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._hosts, default=str), "utf-8")
            tmp.replace(self.path)
        except Exception as exc:
            log.warning("could not save %s: %s", self.path, exc)

    @staticmethod
    def key_for(report: dict[str, Any]) -> str | None:
        host = report.get("host") or {}
        ts = report.get("tailscale") or {}
        name = ts.get("hostname") or host.get("hostname")
        return str(name).lower() if name else None

    def put(self, report: dict[str, Any], source_ip: str | None) -> str:
        key = self.key_for(report)
        if not key:
            raise ValueError("report has no hostname")
        with self._lock:
            self._hosts[key] = {"report": report, "received_at": time.time(), "source_ip": source_ip}
            self.reports += 1
            if self.reports % 20 == 1:  # persist occasionally, not on every heartbeat
                self._save()
        return key

    def forget(self, key: str) -> bool:
        with self._lock:
            gone = self._hosts.pop(key.lower(), None) is not None
            self._save()
        return gone

    def host(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._hosts.get(key.lower())

    def fleet(self) -> dict[str, Any]:
        now = time.time()
        nodes: list[dict[str, Any]] = []
        with self._lock:
            items = list(self._hosts.items())
        for key, entry in items:
            rep = entry.get("report") or {}
            age = now - float(entry.get("received_at") or 0)
            if age <= self.stale_after:
                status, err = "ok", None
            elif age <= self.lost_after:
                status, err = "stale", f"last report {int(age)}s ago"
            else:
                status, err = "lost", f"no report for {int(age // 60)} min"
            host = rep.get("host") or {}
            ts = rep.get("tailscale") or {}
            nodes.append(
                {
                    "hostname": ts.get("hostname") or host.get("hostname") or key,
                    "ip": ts.get("ip") or entry.get("source_ip"),
                    "os": host.get("os"),
                    "self": False,
                    "tailscale_online": status == "ok",
                    "status": status,
                    "error": err,
                    "stats": rep,
                    "claude_accounts": headline(rep.get("claude")),
                    "age_s": round(age, 1),
                    "received_at": entry.get("received_at"),
                }
            )
        order = {"ok": 0, "stale": 1, "lost": 2}
        nodes.sort(key=lambda n: (order.get(n["status"], 9), n["hostname"]))
        return {
            "schema": SCHEMA_VERSION,
            "generated_at": now,
            "from_host": "hub",
            "via": "hub",
            "hub_version": __version__,
            "hub_uptime_s": int(now - self.started),
            "nodes": nodes,
            "summary": {
                "total": len(nodes),
                "ok": sum(1 for n in nodes if n["status"] == "ok"),
                "stale": sum(1 for n in nodes if n["status"] == "stale"),
                "lost": sum(1 for n in nodes if n["status"] == "lost"),
                "no_agent": 0,
                "offline": 0,
            },
        }


def resolve_token(explicit: str | None, insecure: bool, sdir: Path) -> str | None:
    """Explicit > env > persisted file > freshly generated (and persisted). None only if insecure."""
    if insecure:
        return None
    tok = explicit or os.environ.get("HWPROBE_FLEET_TOKEN")
    if tok:
        return tok
    f = sdir / "hub-token"
    try:
        if f.exists():
            tok = f.read_text("utf-8").strip()
            if tok:
                return tok
        sdir.mkdir(parents=True, exist_ok=True)
        tok = secrets.token_urlsafe(24)
        f.write_text(tok + "\n", "utf-8")
        try:
            f.chmod(0o600)
        except Exception:
            pass
        log.warning("generated a hub token and saved it to %s", f)
        return tok
    except Exception as exc:
        log.error("could not persist a token (%s); using a one-off token for this run", exc)
        return secrets.token_urlsafe(24)


def build_app(store: Store, token: str | None, host: str = "0.0.0.0", port: int = DEFAULT_HUB_PORT):
    """The Starlette app: MCP at /mcp plus the JSON/HTML routes, all behind one bearer token."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse

    from .server import DASHBOARD_HTML

    mcp = FastMCP(
        "hwprobe-fleet-hub",
        instructions=(
            "Live CPU, RAM, disk, GPU and Claude-usage figures for every machine on this tailnet, "
            "reported by the hwprobe-fleet agent on each host. Call pick_host before delegating "
            "work to another machine; call fleet_status for the raw numbers."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    def fleet_status() -> dict[str, Any]:
        """Every host that reports to the hub, with its latest CPU %, RAM, disk, GPU and
        Claude account usage (5-hour and 7-day windows). `status` is ok / stale / lost by
        report age; `age_s` is how old the numbers are."""
        return store.fleet()

    @mcp.tool()
    def fleet_table() -> str:
        """The same fleet view rendered as a compact text table (one line per host)."""
        return client.render_table(store.fleet())

    @mcp.tool()
    def pick_host(
        need_gpu: bool = False,
        min_free_ram_gb: float = 0.0,
        min_free_disk_gb: float = 0.0,
        max_cpu_percent: float = 85.0,
        max_claude_session_percent: float = 90.0,
        exclude: list[str] | None = None,
    ) -> dict[str, Any]:
        """Rank hosts by headroom for delegating work. Returns every host with `eligible`,
        a 0-100 `score` (higher = more idle) and `reasons`. Hosts whose report is stale or whose
        Claude 5-hour window is nearly used up are marked ineligible, never hidden."""
        ranked = rank.rank_hosts(
            store.fleet(),
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
    def host_stats(hostname: str) -> dict[str, Any]:
        """One host's full last report (all volumes, every GPU, every Claude account)."""
        entry = store.host(hostname)
        if not entry:
            return {"error": f"no report from '{hostname}'", "known_hosts": [n["hostname"] for n in store.fleet()["nodes"]]}
        return {"age_s": round(time.time() - entry["received_at"], 1), **entry["report"]}

    @mcp.tool()
    def claude_usage() -> dict[str, Any]:
        """Claude Code account usage on every host: email, plan, 5-hour and 7-day percentages,
        per-model weekly windows, and reset times. Use it to spread work across accounts that
        still have headroom."""
        out = []
        for n in store.fleet()["nodes"]:
            out.append({"hostname": n["hostname"], "status": n["status"], "age_s": n["age_s"], "accounts": n.get("claude_accounts") or [], "orca": ((n.get("stats") or {}).get("claude") or {}).get("orca")})
        return {"hosts": out}

    def _authorized(request: Request) -> bool:
        # Header only: a token in a URL ends up in browser history, proxy and access logs.
        if not token:
            return True
        auth = request.headers.get("authorization", "")
        return auth.lower().startswith("bearer ") and secrets.compare_digest(auth[7:].strip(), token)

    class Auth(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path in ("/healthz", "/", "/index.html") or _authorized(request):
                return await call_next(request)
            return JSONResponse({"error": "unauthorized: send Authorization: Bearer <hub token>"}, status_code=401)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request):
        f = store.fleet()
        return JSONResponse({"ok": True, "hosts": f["summary"], "uptime_s": f["hub_uptime_s"], "version": __version__, "auth": bool(token)})

    @mcp.custom_route("/v1/report", methods=["POST"])
    async def report(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body must be JSON"}, status_code=400)
        if not isinstance(body, dict) or body.get("schema") != SCHEMA_VERSION:
            return JSONResponse({"error": f"expected a schema {SCHEMA_VERSION} report"}, status_code=400)
        try:
            key = store.put(body, request.client.host if request.client else None)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "host": key})

    @mcp.custom_route("/v1/fleet", methods=["GET"])
    async def fleet(_: Request):
        return JSONResponse(store.fleet())

    @mcp.custom_route("/v1/hosts/{name}", methods=["GET", "DELETE"])
    async def one_host(request: Request):
        name = request.path_params["name"]
        if request.method == "DELETE":
            return JSONResponse({"ok": store.forget(name)})
        entry = store.host(name)
        if not entry:
            return JSONResponse({"error": "unknown host"}, status_code=404)
        return JSONResponse(entry)

    @mcp.custom_route("/", methods=["GET"])
    async def index(_: Request):
        return HTMLResponse(DASHBOARD_HTML)

    app = mcp.streamable_http_app()
    app.add_middleware(Auth)
    return app


def serve_hub(
    port: int = DEFAULT_HUB_PORT,
    bind: str = "0.0.0.0",
    token: str | None = None,
    insecure: bool = False,
    stale_after: float = 90.0,
    state: str | None = None,
    log_level: str = "info",
) -> None:
    import logging as _logging
    import sys

    import uvicorn

    _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    sdir = Path(state) if state else state_dir()
    tok = resolve_token(token, insecure, sdir)
    store = Store(sdir / "hub-hosts.json", stale_after=stale_after)
    app = build_app(store, tok, host=bind, port=port)
    if tok:
        log.info("hub listening on http://%s:%s (token required; MCP at /mcp, dashboard at /)", bind, port)
    else:
        log.warning("hub listening on http://%s:%s with NO token — anyone who can reach it can read the fleet", bind, port)
    uvicorn.run(app, host=bind, port=port, log_level=log_level)
