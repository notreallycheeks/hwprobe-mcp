"""Turn a fleet document into a ranked "where should this work go?" answer."""

from __future__ import annotations

from typing import Any


def _acct_session_max(node: dict[str, Any]) -> float | None:
    vals = [a.get("session_percent") for a in node.get("claude_accounts") or [] if a.get("session_percent") is not None]
    return max(vals) if vals else None


def _acct_session_min(node: dict[str, Any]) -> float | None:
    vals = [a.get("session_percent") for a in node.get("claude_accounts") or [] if a.get("session_percent") is not None]
    return min(vals) if vals else None


def rank_hosts(
    fleet: dict[str, Any],
    *,
    need_gpu: bool = False,
    min_free_ram_gb: float = 0.0,
    min_free_disk_gb: float = 0.0,
    max_cpu_percent: float = 85.0,
    max_claude_session_percent: float = 90.0,
    require_claude: bool = False,
    exclude: list[str] | None = None,
    max_age_s: float = 180.0,
) -> list[dict[str, Any]]:
    """Score every reporting host 0..100 (higher = more headroom) and say why it is or is not
    eligible. Hosts without a fresh report are listed as ineligible, never dropped."""
    excl = {e.lower() for e in exclude or []}
    out: list[dict[str, Any]] = []
    for n in fleet.get("nodes") or []:
        name = n.get("hostname") or "?"
        s = n.get("stats") or {}
        reasons: list[str] = []
        eligible = True
        if name.lower() in excl:
            eligible, reasons = False, ["excluded by caller"]
        if n.get("status") != "ok" or not s:
            out.append({"hostname": name, "eligible": False, "score": 0, "reasons": [f"{n.get('status')}: {n.get('error') or 'no report'}"]})
            continue
        age = n.get("age_s")
        if age is not None and age > max_age_s:
            eligible = False
            reasons.append(f"report is {int(age)}s old (> {int(max_age_s)}s)")
        cpu = (s.get("cpu") or {}).get("percent")
        mem = s.get("memory") or {}
        disk = (s.get("disks") or {}).get("primary") or {}
        gpus = (s.get("gpu") or {}).get("gpus") or []
        free_ram = mem.get("available_gb")
        free_disk = disk.get("free_gb")
        if cpu is not None and cpu > max_cpu_percent:
            eligible = False
            reasons.append(f"CPU {cpu:.0f}% > {max_cpu_percent:.0f}%")
        if free_ram is not None and free_ram < min_free_ram_gb:
            eligible = False
            reasons.append(f"free RAM {free_ram:.0f} GB < {min_free_ram_gb:.0f} GB")
        if free_disk is not None and free_disk < min_free_disk_gb:
            eligible = False
            reasons.append(f"free disk {free_disk:.0f} GB < {min_free_disk_gb:.0f} GB")
        gpu_util = None
        if need_gpu:
            if not gpus:
                eligible = False
                reasons.append("no GPU telemetry (no usable GPU, or driver down)")
            else:
                gpu_util = min((g.get("utilization_percent") or 0) for g in gpus)
                if gpu_util > max_cpu_percent:
                    eligible = False
                    reasons.append(f"GPU {gpu_util:.0f}% busy")
        sess = _acct_session_min(n)
        if require_claude and sess is None:
            eligible = False
            reasons.append("no Claude account with usage data")
        if sess is not None and sess > max_claude_session_percent:
            eligible = False
            reasons.append(f"Claude 5h window {sess:.0f}% used on every account")
        # Score: average headroom across the dimensions we know about.
        parts: list[float] = []
        if cpu is not None:
            parts.append(100 - cpu)
        if mem.get("percent") is not None:
            parts.append(100 - mem["percent"])
        if disk.get("percent") is not None:
            parts.append(min(100.0, (free_disk or 0) * 2))  # 50 GB free = full marks
        if need_gpu and gpu_util is not None:
            parts.append(100 - gpu_util)
        if sess is not None:
            parts.append(100 - sess)
        score = round(sum(parts) / len(parts), 1) if parts else 0.0
        if eligible:
            reasons.append(
                f"CPU {cpu if cpu is not None else '?'}%, free RAM {free_ram if free_ram is not None else '?'} GB, "
                f"free disk {free_disk if free_disk is not None else '?'} GB"
                + (f", GPU {gpu_util:.0f}% busy" if gpu_util is not None else "")
                + (f", Claude 5h {sess:.0f}%" if sess is not None else "")
            )
        out.append(
            {
                "hostname": name,
                "os": n.get("os"),
                "ip": n.get("ip"),
                "eligible": eligible,
                "score": score,
                "cpu_percent": cpu,
                "free_ram_gb": free_ram,
                "free_disk_gb": free_disk,
                "gpu_utilization_percent": gpu_util if need_gpu else (min((g.get("utilization_percent") or 0) for g in gpus) if gpus else None),
                "gpu_names": [g.get("name") for g in gpus],
                "claude_session_percent_min": sess,
                "claude_session_percent_max": _acct_session_max(n),
                "reasons": reasons,
            }
        )
    out.sort(key=lambda r: (not r["eligible"], -r["score"], r["hostname"]))
    return out
