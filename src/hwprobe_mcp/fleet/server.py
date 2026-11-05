"""The per-host agent: a small threaded HTTP server bound to loopback + the Tailscale address.

Routes (all GET, JSON unless noted):
  /healthz       liveness
  /v1/stats      this host (identity, CPU, RAM, disks, GPU, Claude usage)
  /v1/fleet      every host on the tailnet, by asking each peer for /v1/stats
  /              HTML dashboard of /v1/fleet (auto-refreshing)

Requests are accepted only from loopback and Tailscale addresses (100.64.0.0/10 and the
fd7a:115c:a1e0::/48 ULA) unless `--allow` adds more networks, and only with a `Host` header
naming this machine (loopback, its Tailscale IP or MagicDNS name) to defeat DNS rebinding.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import SCHEMA_VERSION, claude_usage, client, discovery, hub_client, stats

log = logging.getLogger("hwprobe.fleet")

TAILNET_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
]

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


class Sampler(threading.Thread):
    """Keeps CPU %, GPU and Claude usage warm so a request never blocks on measurement."""

    def __init__(self, cpu_interval: float = 2.0, gpu_interval: float = 5.0, claude_interval: float = 180.0):
        super().__init__(name="hwprobe-fleet-sampler", daemon=True)
        self.cpu_interval = cpu_interval
        self.gpu_interval = gpu_interval
        self.claude_interval = claude_interval
        self.cpu_percent: float | None = None
        self.gpu: dict[str, Any] | None = None
        self.gpu_warnings: list[str] = []
        self.claude: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._last_gpu = 0.0
        self._last_claude = 0.0
        self.claude_enabled = True

    def refresh_claude(self, force: bool = False) -> None:
        try:
            self.claude = claude_usage.collect(force=force)
        except Exception as exc:  # never let a collector kill the sampler
            self.claude = {"accounts": [], "orca": None, "warnings": [f"claude usage failed: {exc}"]}
        self._last_claude = time.time()

    def refresh_gpu(self) -> None:
        warns: list[str] = []
        try:
            self.gpu = stats.gpu(warns)
        except Exception as exc:
            self.gpu, warns = {"source": None, "gpus": []}, [f"gpu: {exc}"]
        self.gpu_warnings = warns
        self._last_gpu = time.time()

    def run(self) -> None:
        if psutil:
            psutil.cpu_percent(interval=None)  # prime
        while not self._stop.is_set():
            try:
                if psutil:
                    self.cpu_percent = psutil.cpu_percent(interval=None)
                now = time.time()
                if now - self._last_gpu >= self.gpu_interval:
                    self.refresh_gpu()
                if self.claude_enabled and now - self._last_claude >= self.claude_interval:
                    self.refresh_claude()
            except Exception as exc:  # pragma: no cover
                log.warning("sampler: %s", exc)
            self._stop.wait(self.cpu_interval)

    def stop(self) -> None:
        self._stop.set()


class Agent:
    def __init__(self, port: int, allow: list[str] | None = None, claude: bool = True):
        self.port = port
        self.allowed = list(TAILNET_NETS) + [ipaddress.ip_network(a, strict=False) for a in allow or []]
        self.sampler = Sampler()
        self.sampler.claude_enabled = claude
        self.started = time.time()

    def allowed_source(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return False
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return any(addr in net for net in self.allowed)

    tailscale_ip: str | None = None
    tailscale_hostname: str | None = None
    tailscale_dns: str | None = None

    def allowed_host_header(self, host: str | None) -> bool:
        """Only names that resolve to this agent may appear in `Host`, so a page on some
        other site cannot reach us through DNS rebinding."""
        if not host:
            return False
        h = host.strip().lower()
        if h.startswith("["):  # [::1]:7788
            h = h[1:].split("]", 1)[0]
        else:
            h = h.rsplit(":", 1)[0] if h.count(":") == 1 else h
        names = {"127.0.0.1", "localhost", "::1", socket.gethostname().lower()}
        for n in (self.tailscale_ip, self.tailscale_hostname, self.tailscale_dns):
            if n:
                names.add(n.lower().rstrip("."))
        return h in names

    def local_stats(self) -> dict[str, Any]:
        s = self.sampler
        snap = stats.snapshot(cpu_sample=s.cpu_percent, gpu_data=s.gpu, claude=s.claude, extra_warnings=s.gpu_warnings)
        snap["tailscale"] = {"ip": self.tailscale_ip, "hostname": self.tailscale_hostname}
        return snap

    def fleet(self, timeout: float) -> dict[str, Any]:
        return client.fleet_status(timeout=timeout, port=self.port, self_stats=self.local_stats())


_agent: Agent | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "hwprobe-fleet"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter than the default
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj, default=str).encode("utf-8"))

    def do_GET(self) -> None:
        assert _agent is not None
        if not _agent.allowed_source(self.client_address[0]):
            self._json(403, {"error": "forbidden: only loopback and tailnet sources are served"})
            return
        if not _agent.allowed_host_header(self.headers.get("Host")):
            self._json(421, {"error": "misdirected: Host header is not this agent's address"})
            return
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        try:
            if url.path == "/healthz":
                self._json(200, {"ok": True, "uptime_s": int(time.time() - _agent.started), "schema": SCHEMA_VERSION})
            elif url.path == "/v1/stats":
                self._json(200, _agent.local_stats())
            elif url.path == "/v1/claude":
                if qs.get("refresh"):
                    _agent.sampler.refresh_claude(force=True)
                self._json(200, _agent.sampler.claude or {"accounts": [], "orca": None, "warnings": ["not collected yet"]})
            elif url.path == "/v1/fleet":
                timeout = min(15.0, max(0.5, float(qs.get("timeout", ["4"])[0])))
                self._json(200, _agent.fleet(timeout))
            elif url.path in ("/", "/index.html"):
                self._send(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._json(404, {"error": "not found", "routes": ["/healthz", "/v1/stats", "/v1/claude", "/v1/fleet", "/"]})
        except Exception as exc:
            log.exception("request failed")
            self._json(500, {"error": str(exc)})


def _bind(host: str, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def serve(
    port: int | None = None,
    binds: list[str] | None = None,
    allow: list[str] | None = None,
    claude: bool = True,
    once: bool = False,
    hub: str | None = None,
    hub_token: str | None = None,
    push_interval: float = 15.0,
) -> None:
    """Run the agent until interrupted. Binds loopback immediately and the Tailscale IPv4 as soon
    as it exists (re-checked every 15 s, so starting before tailscaled is fine). With a hub
    configured (`--hub`, `HWPROBE_FLEET_HUB` or `hub` in fleet.json) it also pushes a report
    there every `push_interval` seconds."""
    global _agent
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    port = port or discovery.port()
    _agent = Agent(port, allow=allow, claude=claude)
    _agent.sampler.start()
    servers: dict[str, ThreadingHTTPServer] = {}
    wanted = binds or ["127.0.0.1", "tailscale"]

    def start(host: str) -> None:
        try:
            srv = _bind(host, port)
        except OSError as exc:
            log.error("bind %s:%s failed: %s", host, port, exc)
            return
        servers[host] = srv
        threading.Thread(target=srv.serve_forever, name=f"http-{host}", daemon=True).start()
        log.info("listening on http://%s:%s", host, port)

    for host in wanted:
        if host == "tailscale":
            continue
        start(host)

    ts_checked = threading.Event()

    def watch_tailscale() -> None:
        while True:
            st = discovery.tailscale_status()
            me = (st or {}).get("Self") or {}
            ip = discovery._ipv4(me.get("TailscaleIPs"))
            if ip:
                _agent.tailscale_ip = ip
                _agent.tailscale_hostname = me.get("HostName")
                _agent.tailscale_dns = (me.get("DNSName") or "").rstrip(".") or None
            if ip and ip not in servers and "tailscale" in wanted:
                start(ip)
            ts_checked.set()
            time.sleep(15 if ip else 10)

    threading.Thread(target=watch_tailscale, name="tailscale-watch", daemon=True).start()

    hub_url = hub or hub_client.hub_url()
    if hub_url:
        token = hub_token or hub_client.hub_token()

        def push() -> None:
            # The hub keys hosts by the Tailscale name, so wait for the first lookup: a report sent
            # under the OS hostname (it differs on macOS) would leave a second entry that never clears.
            ts_checked.wait(timeout=30)
            last_err = None
            while True:
                try:
                    err = hub_client.post_report(hub_url, _agent.local_stats(), token)
                except Exception as exc:  # pragma: no cover
                    err = str(exc)
                if err and err != last_err:
                    log.warning("push to %s failed: %s", hub_url, err)
                elif not err and last_err:
                    log.info("push to %s recovered", hub_url)
                last_err = err
                time.sleep(push_interval)

        threading.Thread(target=push, name="hub-push", daemon=True).start()
        log.info("pushing reports to hub %s every %ss%s", hub_url, push_interval, " (with token)" if token else " (NO token)")
    if once:
        return
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        for srv in servers.values():
            srv.shutdown()
        _agent.sampler.stop()


DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet</title>
<style>
:root{--bg:#f6f7f9;--fg:#1b1f24;--muted:#6b7280;--card:#fff;--line:#e5e7eb;--bar:#d1d5db;--ok:#2f855a;--warn:#b7791f;--bad:#c53030;--fill:#3b82f6}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e6e6;--muted:#9aa0a6;--card:#171a21;--line:#2a2f3a;--bar:#2a2f3a;--fill:#60a5fa}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
h1{font-size:18px;margin:0 0 4px}.sub{color:var(--muted);font-size:12px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.card h2{font-size:15px;margin:0 0 2px;display:flex;justify-content:space-between;align-items:center}
.tag{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.tag.ok{color:var(--ok);border-color:var(--ok)}.tag.bad{color:var(--bad);border-color:var(--bad)}.tag.warn{color:var(--warn);border-color:var(--warn)}
.meta{color:var(--muted);font-size:12px;margin-bottom:8px;word-break:break-word}
.row{display:grid;grid-template-columns:52px 1fr 110px;gap:8px;align-items:center;margin:4px 0;font-size:13px}
.bar{height:8px;background:var(--bar);border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--fill)}
.bar i.warn{background:var(--warn)}.bar i.bad{background:var(--bad)}
.val{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted);font-size:12px}
.claude{margin-top:8px;padding-top:8px;border-top:1px dashed var(--line)}
.err{color:var(--bad);font-size:12px}.warns{color:var(--muted);font-size:11px;margin-top:6px}
</style></head><body>
<h1>Fleet</h1><div class="sub" id="sub">loading…</div>
<div class="grid" id="grid"></div>
<script>
const pct=v=>v==null?null:Math.max(0,Math.min(100,+v));
const cls=v=>v==null?'':v>=90?'bad':v>=70?'warn':'';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function bar(label,v,text){const p=pct(v);return `<div class="row"><span>${label}</span><div class="bar"><i class="${cls(p)}" style="width:${p??0}%"></i></div><span class="val">${esc(text??(p==null?'–':p.toFixed(0)+'%'))}</span></div>`}
function card(n){const s=n.stats||{},h=s.host||{};let tag=n.status==='ok'?'ok':n.status==='stale'?'warn':n.status==='offline'?'':'bad';
let body='';
if(n.status==='ok'||n.status==='stale'){
 if(n.status==='stale')body+=`<div class="err">${esc(n.error)}</div>`;
 body+=bar('CPU',s.cpu?.percent);
 body+=bar('RAM',s.memory?.percent,s.memory?.total_gb?`${s.memory.used_gb}/${s.memory.total_gb} GB`:null);
 const d=s.disks?.primary; body+=bar('Disk',d?.percent,d?`${d.free_gb} GB free`:null);
 const gs=s.gpu?.gpus||[]; if(gs.length){for(const g of gs){body+=bar('GPU',g.utilization_percent,(g.name||'GPU').replace('NVIDIA ','').replace('GeForce ',''));if(g.memory_total_mb)body+=bar('VRAM',g.memory_percent,`${g.memory_used_mb}/${g.memory_total_mb} MB`)}}else body+=bar('GPU',null,'no telemetry');
 const accts=n.claude_accounts||[]; if(accts.length){body+='<div class="claude">';for(const a of accts){body+=`<div class="meta">${esc(a.email||'orca')}${a.subscription?' · '+esc(a.subscription):''}</div>`;if(a.error){body+=`<div class="err">${esc(a.error)}</div>`;continue}body+=bar('5h',a.session_percent);body+=bar('7d',a.weekly_percent);for(const [m,p] of Object.entries(a.scoped||{}))if(p!=null)body+=bar(esc(m),p)}body+='</div>'}
 else body+='<div class="claude meta">no Claude account found</div>';
 const w=(s.warnings||[]).filter(x=>!/^no GPU/.test(x)); if(w.length)body+=`<div class="warns">${esc(w.join(' · '))}</div>`;
}else body+=`<div class="err">${esc(n.error||n.status)}</div>`;
return `<div class="card"><h2>${esc(n.hostname)}${n.self?' <span class="tag">this host</span>':''}<span class="tag ${tag}">${esc(n.status)}</span></h2><div class="meta">${esc(n.os||'')} · ${esc(n.ip||'')}${h.cpu_model?' · '+esc(h.cpu_model):''}${h.uptime_s?' · up '+Math.floor(h.uptime_s/3600)+'h':''}</div>${body}</div>`}
let token='';try{token=localStorage.getItem('fleetToken')||''}catch(e){}
async function load(){try{let r=await fetch('/v1/fleet?timeout=4',{headers:token?{'Authorization':'Bearer '+token}:{}});
 if(r.status===401||r.status===403){const t=prompt('Fleet hub token');if(!t)return;token=t;try{localStorage.setItem('fleetToken',t)}catch(e){}r=await fetch('/v1/fleet?timeout=4',{headers:{'Authorization':'Bearer '+token}})}
 const f=await r.json();document.getElementById('grid').innerHTML=(f.nodes||[]).map(card).join('');document.getElementById('sub').textContent=`${f.summary.ok}/${f.summary.total} hosts reporting · via ${f.via||'peers'} (${f.from_host}) · ${new Date(f.generated_at*1000).toLocaleTimeString()}`}catch(e){document.getElementById('sub').textContent='fetch failed: '+e}}
load();setInterval(load,10000);
</script></body></html>
"""
