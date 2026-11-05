"""Claude usage (rate-limit windows) for every Claude Code account signed in on this host.

Two sources, merged:

* **Direct** — each Claude Code config dir (`~/.claude`, `$CLAUDE_CONFIG_DIR`, Orca-managed
  account folders) holds `.claude.json` (who is signed in) and OAuth credentials. The same
  endpoint Claude Code's `/usage` screen reads, `GET /api/oauth/usage`, gives the 5-hour session
  window, the 7-day window and any per-model weekly window as percentages.
* **Orca** — `orca account list --json` reports the rate limits Orca itself tracks for the
  active Claude account (and Codex, Gemini, ... when signed in) plus its managed accounts.

Tokens are read only to make the request; they are never included in any output.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..util import IS_LINUX, IS_MAC, IS_WINDOWS, run, which

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
API_TTL_S = 120.0
ORCA_TTL_S = 60.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


# --------------------------------------------------------------------------- locating accounts


def orca_config_roots() -> list[Path]:
    """Where Orca keeps its profile (and therefore its managed Claude account folders)."""
    roots: list[Path] = []
    env = os.environ.get("HWPROBE_ORCA_CONFIG_DIR")
    if env:
        roots.append(Path(env))
    home = Path.home()
    if IS_WINDOWS:
        for var in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                roots.append(Path(base) / "Orca")
                roots.append(Path(base) / "orca")
    elif IS_MAC:
        roots.append(home / "Library" / "Application Support" / "Orca")
        roots.append(home / "Library" / "Application Support" / "orca")
    roots.append(home / ".config" / "orca")
    return [r for r in roots if r.is_dir()]


def _looks_like_config_dir(d: Path) -> bool:
    return (d / ".claude.json").exists() or (d / ".credentials.json").exists() or (
        d == Path.home() / ".claude" and (Path.home() / ".claude.json").exists()
    )


def claude_config_dirs() -> list[Path]:
    """Every Claude Code config dir that has an account on this host, default first."""
    cands: list[Path] = []
    extra = os.environ.get("HWPROBE_CLAUDE_CONFIG_DIRS")
    if extra:
        cands += [Path(p) for p in extra.split(os.pathsep) if p]
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        cands.append(Path(env))
    cands.append(Path.home() / ".claude")
    for root in orca_config_roots():
        observed = root / "claude-observed-config-dirs.json"
        try:
            if observed.exists():
                cands += [Path(p) for p in json.loads(observed.read_text("utf-8")) if isinstance(p, str)]
        except Exception:
            pass
        for sub in ("claude-profiles", "claude-accounts"):
            d = root / sub
            if d.is_dir():
                for child in sorted(d.iterdir()):
                    if child.is_dir():
                        cands.append(child)
                        # Orca may nest the real config dir one level down.
                        for nested in ("config", ".claude", "claude"):
                            if (child / nested).is_dir():
                                cands.append(child / nested)
    out: list[Path] = []
    seen: set[str] = set()
    for c in cands:
        try:
            c = c.expanduser().resolve()
        except Exception:
            continue
        key = str(c).lower() if IS_WINDOWS else str(c)
        if key in seen or not c.is_dir() or not _looks_like_config_dir(c):
            continue
        seen.add(key)
        out.append(c)
    return out


def _account_file(config_dir: Path) -> Path | None:
    if (config_dir / ".claude.json").exists():
        return config_dir / ".claude.json"
    if config_dir == (Path.home() / ".claude").resolve() and (Path.home() / ".claude.json").exists():
        return Path.home() / ".claude.json"
    return None


def read_account(config_dir: Path, warns: list[str]) -> dict[str, Any]:
    """Who is signed in (email, org, display name) — never the token."""
    f = _account_file(config_dir)
    info: dict[str, Any] = {"config_dir": str(config_dir), "email": None, "organization": None}
    if f is None:
        return info
    try:
        d = json.loads(f.read_text("utf-8"))
    except Exception as exc:
        warns.append(f"{f}: unreadable ({exc})")
        return info
    oa = d.get("oauthAccount") or {}
    info.update(
        email=oa.get("emailAddress"),
        display_name=oa.get("displayName"),
        organization=oa.get("organizationName"),
        account_uuid=oa.get("accountUuid"),
    )
    return info


def _keychain_credentials(service: str) -> dict[str, Any] | None:
    r = run(["security", "find-generic-password", "-a", os.environ.get("USER", ""), "-w", "-s", service], timeout=8)
    if r["rc"] != 0 or not r["out"].strip():
        return None
    try:
        return json.loads(r["out"].strip())
    except Exception:
        return None


def read_credentials(config_dir: Path, warns: list[str]) -> dict[str, Any] | None:
    """The `claudeAiOauth` block: token + expiry + subscription. Returned to the caller only."""
    f = config_dir / ".credentials.json"
    d: dict[str, Any] | None = None
    if f.exists():
        try:
            d = json.loads(f.read_text("utf-8"))
        except Exception as exc:
            warns.append(f"{f}: unreadable ({exc})")
    elif IS_MAC:
        # Claude Code keeps credentials in the login keychain on macOS. The default dir uses the
        # plain service name; other config dirs are not covered here.
        if config_dir == (Path.home() / ".claude").resolve():
            d = _keychain_credentials("Claude Code-credentials")
        if d is None:
            warns.append(f"{config_dir}: no credentials file and keychain lookup failed")
    if not d:
        return None
    oauth = d.get("claudeAiOauth")
    return oauth if isinstance(oauth, dict) and oauth.get("accessToken") else None


# --------------------------------------------------------------------------- usage API


def _iso(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def _window(block: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(block, dict) or block.get("utilization") is None:
        return None
    return {"percent": round(float(block["utilization"]), 1), "resets_at": _iso(block.get("resets_at"))}


def summarize_usage(raw: dict[str, Any]) -> dict[str, Any]:
    """Reduce the `/api/oauth/usage` document to the windows a scheduler cares about."""
    out: dict[str, Any] = {
        "session": _window(raw.get("five_hour")),
        "weekly": _window(raw.get("seven_day")),
        "weekly_scoped": [],
        "extra_usage": None,
    }
    for lim in raw.get("limits") or []:
        if not isinstance(lim, dict) or lim.get("percent") is None:
            continue
        kind = lim.get("kind")
        entry = {
            "percent": round(float(lim["percent"]), 1),
            "resets_at": _iso(lim.get("resets_at")),
            "severity": lim.get("severity"),
        }
        if kind == "session" and out["session"] is None:
            out["session"] = entry
        elif kind == "weekly_all" and out["weekly"] is None:
            out["weekly"] = entry
        elif kind == "weekly_scoped":
            scope = (lim.get("scope") or {}).get("model") or {}
            out["weekly_scoped"].append({"model": scope.get("display_name"), **entry})
    for key in ("seven_day_opus", "seven_day_sonnet", "seven_day_fable"):
        w = _window(raw.get(key))
        if w and not any(s.get("model", "").lower() == key.split("_")[-1] for s in out["weekly_scoped"]):
            out["weekly_scoped"].append({"model": key.split("_")[-1].capitalize(), **w})
    eu = raw.get("extra_usage")
    if isinstance(eu, dict):
        out["extra_usage"] = {
            "enabled": bool(eu.get("is_enabled")),
            "percent": eu.get("utilization"),
            "spend_limit_reached": bool(eu.get("spend_limit_reached")),
        }
    bd = raw.get("seven_day_breakdown")
    if isinstance(bd, dict) and isinstance(bd.get("rows"), list):
        out["weekly_breakdown"] = {
            r.get("key"): r.get("percent") for r in bd["rows"] if isinstance(r, dict) and r.get("key")
        }
    return out


def fetch_usage(access_token: str, timeout: float = 12.0) -> tuple[dict[str, Any] | None, str | None]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": OAUTH_BETA,
            "Accept": "application/json",
            "User-Agent": "hwprobe-fleet",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp), None
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:160]
        except Exception:
            pass
        if exc.code == 401:
            return None, "token rejected (401) — run `claude` on this host to refresh the sign-in"
        return None, f"HTTP {exc.code} {body}".strip()
    except Exception as exc:
        return None, f"request failed: {exc}"


def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def account_usage(config_dir: Path) -> dict[str, Any]:
    """One account: identity + usage windows, or an `error` string explaining why not."""
    warns: list[str] = []
    acct = read_account(config_dir, warns)
    acct.update(source="claude-code", subscription=None, rate_limit_tier=None, usage=None, error=None)
    creds = read_credentials(config_dir, warns)
    if creds is None:
        acct["error"] = "not signed in (no OAuth credentials)"
    else:
        acct["subscription"] = creds.get("subscriptionType")
        acct["rate_limit_tier"] = creds.get("rateLimitTier")
        exp = creds.get("expiresAt")
        if isinstance(exp, (int, float)) and exp / 1000.0 < time.time():
            acct["error"] = "access token expired — run `claude` on this host to refresh the sign-in"
        else:
            raw, err = _cached(f"api:{config_dir}", API_TTL_S, lambda: fetch_usage(creds["accessToken"]))
            if raw:
                acct["usage"] = summarize_usage(raw)
                acct["fetched_at"] = time.time()
            else:
                acct["error"] = err
    acct["warnings"] = warns
    return acct


# --------------------------------------------------------------------------- orca


def orca_cli() -> str | None:
    """The Orca (onorca.dev) CLI. On Linux `orca` on PATH is the GNOME screen reader, so only
    `orca-ide` or an explicit path counts there."""
    env = os.environ.get("HWPROBE_ORCA_CLI")
    if env and Path(env).exists():
        return env
    for name in ("orca-ide",):
        p = which(name)
        if p:
            return p
    cands: list[str] = ["/opt/Orca/resources/bin/orca-ide", str(Path.home() / ".local/bin/orca-ide")]
    if IS_MAC:
        cands += [
            "/Applications/Orca.app/Contents/Resources/bin/orca",
            "/Applications/Orca.app/Contents/Resources/bin/orca-ide",
            str(Path.home() / "Applications/Orca.app/Contents/Resources/bin/orca"),
        ]
    if IS_WINDOWS:
        for var in ("LOCALAPPDATA", "PROGRAMFILES"):
            base = os.environ.get(var)
            if base:
                for sub in ("Programs/Orca/resources/bin", "Orca/resources/bin"):
                    for exe in ("orca.cmd", "orca.exe", "orca-ide.cmd", "orca-ide.exe", "orca"):
                        cands.append(str(Path(base) / sub / exe))
    if not IS_LINUX:
        p = which("orca")
        if p:
            cands.append(p)
    for c in cands:
        if Path(c).exists():
            return c
    return None


def _orca_rate_limits() -> dict[str, Any] | None:
    cli = orca_cli()
    if not cli:
        return None
    r = run([cli, "account", "list", "--json"], timeout=20)
    if r["rc"] != 0 or not r["out"].strip():
        return {"present": True, "cli": cli, "error": (r["err"] or r["out"] or f"rc={r['rc']}").strip()[:200]}
    try:
        start = r["out"].index("{")
        doc = json.loads(r["out"][start:])
    except Exception as exc:
        return {"present": True, "cli": cli, "error": f"bad JSON: {exc}"}
    res = doc.get("result") or {}
    limits = res.get("rateLimits") or {}
    providers: dict[str, Any] = {}
    unavailable: list[str] = []
    for name, block in limits.items():
        if not isinstance(block, dict) or "provider" not in block:
            continue
        if block.get("status") == "unavailable":
            unavailable.append(block["provider"])
            continue
        entry: dict[str, Any] = {"status": block.get("status"), "error": block.get("error")}
        for win in ("session", "weekly", "fableWeekly", "monthly"):
            w = block.get(win)
            if isinstance(w, dict) and w.get("usedPercent") is not None:
                entry[win] = {
                    "percent": w.get("usedPercent"),
                    "resets_at_epoch_ms": w.get("resetsAt"),
                    "resets": w.get("resetDescription"),
                }
        entry["updated_at_epoch_ms"] = block.get("updatedAt")
        providers[block["provider"]] = entry
    managed = []
    for prov in ("claude", "codex"):
        for a in (res.get(prov) or {}).get("accounts") or []:
            if isinstance(a, dict):
                managed.append(
                    {
                        "provider": prov,
                        "id": a.get("id"),
                        "email": a.get("email"),
                        "home": a.get("home"),
                        "needs_sign_in": a.get("needsSignIn"),
                        "active": a.get("id") == (res.get(prov) or {}).get("activeAccountId"),
                    }
                )
    return {
        "present": True,
        "cli": cli,
        "providers": providers,
        "not_signed_in": unavailable,
        "managed_accounts": managed,
        "error": None,
    }


# --------------------------------------------------------------------------- public


def collect(force: bool = False) -> dict[str, Any]:
    """Everything the fleet needs about this host's agent accounts. Cached; `force` bypasses."""
    if force:
        with _lock:
            _cache.clear()
    warns: list[str] = []
    accounts = [account_usage(d) for d in claude_config_dirs()]
    # Dedupe accounts that appear through several config dirs (same account UUID).
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for a in accounts:
        key = a.get("account_uuid") or a.get("email") or a["config_dir"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(a)
    orca = _cached("orca", ORCA_TTL_S, _orca_rate_limits)
    if orca is None:
        warns.append("Orca CLI not found on this host (Orca rate limits unavailable)")
    return {"accounts": uniq, "orca": orca, "collected_at": time.time(), "warnings": warns}


def headline(claude: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Compact per-account rows for tables: email, session %, weekly %, scoped %, error."""
    rows: list[dict[str, Any]] = []
    if not claude:
        return rows
    for a in claude.get("accounts") or []:
        u = a.get("usage") or {}
        rows.append(
            {
                "email": a.get("email"),
                "subscription": a.get("subscription"),
                "session_percent": (u.get("session") or {}).get("percent"),
                "session_resets_at": (u.get("session") or {}).get("resets_at"),
                "weekly_percent": (u.get("weekly") or {}).get("percent"),
                "weekly_resets_at": (u.get("weekly") or {}).get("resets_at"),
                "scoped": {s.get("model"): s.get("percent") for s in u.get("weekly_scoped") or []},
                "error": a.get("error"),
            }
        )
    orca = claude.get("orca") or {}
    if not rows and isinstance(orca, dict):
        c = (orca.get("providers") or {}).get("claude")
        if c and c.get("status") == "ok":
            rows.append(
                {
                    "email": None,
                    "subscription": None,
                    "session_percent": (c.get("session") or {}).get("percent"),
                    "weekly_percent": (c.get("weekly") or {}).get("percent"),
                    "scoped": {"Fable": (c.get("fableWeekly") or {}).get("percent")},
                    "error": None,
                    "source": "orca",
                }
            )
    return rows
