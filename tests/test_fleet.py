"""Fleet: discovery parsing, usage summarising, ranking, the agent's HTTP surface, and the hub."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from hwprobe_mcp.fleet import claude_usage, client, discovery, rank, server, stats

TS_STATUS = {
    "Self": {"HostName": "alpha", "DNSName": "alpha.tail.ts.net.", "TailscaleIPs": ["100.1.1.1", "fd7a::1"], "OS": "linux"},
    "Peer": {
        "a": {"HostName": "bravo", "DNSName": "bravo.tail.ts.net.", "TailscaleIPs": ["100.1.1.2"], "OS": "linux", "Online": True},
        "b": {"HostName": "phone", "DNSName": "phone.tail.ts.net.", "TailscaleIPs": ["100.1.1.3"], "OS": "android", "Online": True},
        "c": {"HostName": "charlie", "DNSName": "charlie.tail.ts.net.", "TailscaleIPs": ["100.1.1.4"], "OS": "windows", "Online": False},
    },
}

USAGE_RAW = {
    "five_hour": {"utilization": 81.0, "resets_at": "2026-11-05T06:30:00+00:00"},
    "seven_day": {"utilization": 29.0, "resets_at": "2026-11-10T18:00:00+00:00"},
    "limits": [
        {"kind": "session", "percent": 81, "severity": "warning", "resets_at": "2026-11-05T06:30:00+00:00"},
        {"kind": "weekly_all", "percent": 29, "severity": "normal", "resets_at": "2026-11-10T18:00:00+00:00"},
        {"kind": "weekly_scoped", "percent": 21, "severity": "normal", "resets_at": "x", "scope": {"model": {"display_name": "Fable"}}},
    ],
    "extra_usage": {"is_enabled": False, "utilization": None, "spend_limit_reached": False},
    "seven_day_breakdown": {"rows": [{"key": "claude_code", "percent": 100}, {"key": "chat", "percent": 0}]},
}


def test_parse_tailscale_status_skips_mobile_and_keeps_self_first():
    rows = discovery.parse_status(TS_STATUS)
    assert rows[0]["self"] is True and rows[0]["hostname"] == "alpha" and rows[0]["ip"] == "100.1.1.1"
    names = [r["hostname"] for r in rows]
    assert "phone" not in names and "bravo" in names and "charlie" in names
    assert next(r for r in rows if r["hostname"] == "charlie")["online"] is False
    assert any(r["hostname"] == "phone" for r in discovery.parse_status(TS_STATUS, include_mobile=True))


def test_summarize_usage():
    u = claude_usage.summarize_usage(USAGE_RAW)
    assert u["session"]["percent"] == 81.0 and u["weekly"]["percent"] == 29.0
    assert u["weekly_scoped"] == [{"model": "Fable", "percent": 21.0, "resets_at": "x", "severity": "normal"}]
    assert u["extra_usage"]["enabled"] is False
    assert u["weekly_breakdown"]["claude_code"] == 100


def test_headline_rows_and_no_tokens():
    doc = {
        "accounts": [
            {"email": "a@x", "subscription": "max", "usage": claude_usage.summarize_usage(USAGE_RAW), "error": None},
            {"email": "b@x", "subscription": None, "usage": None, "error": "not signed in"},
        ],
        "orca": None,
    }
    rows = claude_usage.headline(doc)
    assert rows[0]["session_percent"] == 81.0 and rows[0]["scoped"] == {"Fable": 21.0}
    assert rows[1]["error"] == "not signed in"
    assert "accessToken" not in json.dumps(doc)


def test_local_snapshot_shape():
    snap = stats.snapshot()
    assert snap["schema"] == 1
    assert set(snap) >= {"host", "cpu", "memory", "disks", "gpu", "claude", "warnings"}
    assert snap["host"]["hostname"]
    assert snap["memory"]["total_gb"] is None or snap["memory"]["total_gb"] > 0
    assert isinstance(snap["disks"]["volumes"], list)


def _node(name, cpu, avail, free, sess=None, gpu=None, status="ok", age=1.0):
    return {
        "hostname": name,
        "status": status,
        "error": None if status == "ok" else "down",
        "age_s": age,
        "stats": {
            "cpu": {"percent": cpu},
            "memory": {"available_gb": avail, "percent": 40.0, "total_gb": 64, "used_gb": 25},
            "disks": {"primary": {"free_gb": free, "percent": 50.0, "total_gb": 1000}},
            "gpu": {"gpus": [{"name": "RTX", "utilization_percent": gpu}] if gpu is not None else []},
            "ts": time.time(),
        },
        "claude_accounts": [{"email": "me@x", "session_percent": sess, "weekly_percent": 10}] if sess is not None else [],
    }


def test_rank_hosts_orders_by_headroom_and_flags_ineligible():
    fleet = {
        "nodes": [
            _node("busy", 95.0, 10, 500, sess=20),
            _node("idle", 5.0, 50, 500, sess=20, gpu=3),
            _node("claude-maxed", 5.0, 50, 500, sess=95),
            _node("down", 0, 0, 0, status="offline"),
            _node("old", 5.0, 50, 500, sess=10, age=999),
        ]
    }
    ranked = rank.rank_hosts(fleet)
    assert ranked[0]["hostname"] == "idle" and ranked[0]["eligible"]
    by = {r["hostname"]: r for r in ranked}
    assert not by["busy"]["eligible"] and "CPU 95%" in by["busy"]["reasons"][0]
    assert not by["claude-maxed"]["eligible"] and "Claude 5h" in by["claude-maxed"]["reasons"][0]
    assert not by["down"]["eligible"] and not by["old"]["eligible"]
    gpu_only = rank.rank_hosts(fleet, need_gpu=True)
    assert [r["hostname"] for r in gpu_only if r["eligible"]] == ["idle"]


def test_render_table_runs_on_mixed_statuses():
    fleet = {"nodes": [_node("idle", 5.0, 50, 500, sess=20, gpu=3), _node("down", 0, 0, 0, status="offline")], "summary": {"ok": 1, "total": 2, "offline": 1}, "port": 7788}
    text = client.render_table(fleet)
    assert "idle" in text and "offline" in text and "RTX 3%" in text and "5h:20%" in text


@pytest.fixture()
def agent():
    server._agent = server.Agent(port=0, claude=False)
    server._agent.sampler.refresh_gpu()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def test_agent_http_surface(agent):
    code, ctype, body = _get(agent + "/healthz")
    assert code == 200 and json.loads(body)["ok"] is True
    code, ctype, body = _get(agent + "/v1/stats")
    doc = json.loads(body)
    assert code == 200 and doc["schema"] == 1 and doc["host"]["hostname"]
    code, ctype, body = _get(agent + "/")
    assert code == 200 and ctype.startswith("text/html") and b"Fleet" in body
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(agent + "/nope")
    assert exc.value.code == 404
    # DNS-rebinding guard: a Host header that is not this agent's address is refused.
    req = urllib.request.Request(agent + "/healthz", headers={"Host": "evil.example.com"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 421
    assert "Access-Control-Allow-Origin" not in dict(exc.value.headers)


def test_host_header_check():
    a = server.Agent(port=0)
    a.tailscale_ip, a.tailscale_hostname, a.tailscale_dns = "100.64.0.10", "alpha", "alpha.tail.ts.net"
    for ok in ("127.0.0.1:7788", "localhost", "[::1]:7788", "100.64.0.10:7788", "alpha:7788", "ALPHA.tail.ts.net:7788"):
        assert a.allowed_host_header(ok), ok
    for bad in (None, "", "evil.example.com:7788", "100.64.0.11:7788", "127.0.0.1.evil.com"):
        assert not a.allowed_host_header(bad), bad


def test_source_allowlist():
    a = server.Agent(port=0, allow=["10.0.0.0/8"])
    assert a.allowed_source("127.0.0.1") and a.allowed_source("100.64.0.10") and a.allowed_source("fd7a:115c:a1e0::1")
    assert a.allowed_source("10.1.2.3") and not a.allowed_source("8.8.8.8") and not a.allowed_source("garbage")
    assert a.allowed_source("::ffff:100.64.0.9")


def test_hub_store_and_app(tmp_path):
    from starlette.testclient import TestClient

    from hwprobe_mcp.fleet import hub

    store = hub.Store(tmp_path / "hosts.json", stale_after=60)
    app = hub.build_app(store, token="secret")
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/v1/fleet").status_code == 401
        report = stats.snapshot()
        report["tailscale"] = {"ip": "100.1.1.9", "hostname": "boxy"}
        r = c.post("/v1/report", json=report, headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200 and r.json()["host"] == "boxy"
        f = c.get("/v1/fleet", headers={"Authorization": "Bearer secret"}).json()
        assert f["via"] == "hub" and f["nodes"][0]["hostname"] == "boxy" and f["nodes"][0]["status"] == "ok"
        assert c.get("/v1/fleet?token=secret").status_code == 401  # header only, never the URL
        assert c.get("/v1/hosts/boxy", headers={"Authorization": "Bearer secret"}).json()["report"]["schema"] == 1
        bad = c.post("/v1/report", json={"schema": 99}, headers={"Authorization": "Bearer secret"})
        assert bad.status_code == 400
        # MCP over streamable HTTP: initialize + list tools.
        init = c.post(
            "/mcp",
            headers={"Authorization": "Bearer secret", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        )
        assert init.status_code == 200, init.text
        tools = c.post(
            "/mcp",
            headers={"Authorization": "Bearer secret", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        names = {t["name"] for t in tools.json()["result"]["tools"]}
        assert {"fleet_status", "pick_host", "host_stats", "claude_usage", "fleet_table"} <= names
        pick = c.post(
            "/mcp",
            headers={"Authorization": "Bearer secret", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "pick_host", "arguments": {}}},
        )
        assert pick.status_code == 200 and "boxy" in pick.text
    # Stale marking.
    store._hosts["boxy"]["received_at"] -= 120
    assert store.fleet()["nodes"][0]["status"] == "stale"


def test_service_python_stays_in_the_venv(tmp_path, monkeypatch):
    from hwprobe_mcp.fleet import service

    real = tmp_path / "base" / "python3"
    real.parent.mkdir()
    real.write_text("")
    venv_py = tmp_path / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.symlink_to(real)
    monkeypatch.setattr(service.sys, "executable", str(venv_py))
    monkeypatch.setattr(service, "IS_WINDOWS", False)
    assert service._python() == str(venv_py)


def test_load_config_accepts_a_bom(tmp_path, monkeypatch):
    cfg = tmp_path / "fleet.json"
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps({"hub": "http://hub-host:7790", "token": "t"}).encode())
    monkeypatch.setenv("HWPROBE_FLEET_CONFIG", str(cfg))
    assert discovery.load_config()["hub"] == "http://hub-host:7790"
