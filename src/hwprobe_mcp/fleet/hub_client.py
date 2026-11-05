"""Talk to a fleet hub (optional central aggregator) from agents and clients."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from . import discovery


def hub_url() -> str | None:
    env = os.environ.get("HWPROBE_FLEET_HUB")
    if env:
        return env.rstrip("/")
    hub = discovery.load_config().get("hub")
    return hub.rstrip("/") if isinstance(hub, str) and hub else None


def hub_token() -> str | None:
    env = os.environ.get("HWPROBE_FLEET_TOKEN")
    if env:
        return env
    tok = discovery.load_config().get("token")
    return tok if isinstance(tok, str) and tok else None


def _headers(token: str | None) -> dict[str, str]:
    h = {"Accept": "application/json", "User-Agent": "hwprobe-fleet", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get_fleet(url: str, token: str | None = None, timeout: float = 8.0) -> tuple[dict[str, Any] | None, str | None]:
    req = urllib.request.Request(f"{url}/v1/fleet", headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp), None
    except urllib.error.HTTPError as exc:
        return None, f"hub HTTP {exc.code}" + (" (bad or missing token)" if exc.code in (401, 403) else "")
    except Exception as exc:
        return None, f"hub unreachable: {getattr(exc, 'reason', exc)}"


def post_report(url: str, report: dict[str, Any], token: str | None = None, timeout: float = 8.0) -> str | None:
    body = json.dumps(report, default=str).encode("utf-8")
    req = urllib.request.Request(f"{url}/v1/report", data=body, headers=_headers(token), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return None
    except urllib.error.HTTPError as exc:
        return f"hub HTTP {exc.code}" + (" (bad or missing token)" if exc.code in (401, 403) else "")
    except Exception as exc:
        return f"hub unreachable: {getattr(exc, 'reason', exc)}"
