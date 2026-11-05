---
name: fleet-status
description: >-
  Check which machines on the tailnet have spare CPU, RAM, disk, GPU and Claude usage before
  delegating, handing off, or spawning work on another computer (Orca workers, SSH targets,
  paired environments, remote Claude/Codex sessions). Use whenever you are choosing a host for
  a task, when a user asks "which machine is free / maxed out", or before starting a heavy job
  (training, builds, video processing) somewhere other than the current machine.
---

# Fleet status

Every machine runs the `hwprobe-fleet` agent, which reports CPU %, RAM, disk, GPU and the
Claude Code accounts signed in there (5-hour and 7-day usage windows). A central hub
aggregates the reports and exposes them over MCP and HTTP.

## Read it

Prefer, in order:

1. **MCP tools** if your session has them: `pick_host` (ranked recommendation), `fleet_status`
   (raw numbers), `claude_usage` (accounts + windows), `host_stats(hostname)`, `fleet_table`.
   They come from the `fleet-hub` MCP server (HTTP) or the local `hwprobe` MCP server.
2. **CLI** when installed on this machine:
   ```bash
   hwprobe-fleet                 # table of every host
   hwprobe-fleet pick --gpu      # ranked hosts; --ram 16 --disk 50 --max-claude 80 also work
   hwprobe-fleet status --json   # full document
   ```
3. **HTTP** from anything else (curl, a browser, an Orca panel):
   `GET http://<hub>:7790/v1/fleet` with `Authorization: Bearer <token>`, or on any machine
   with the agent `GET http://<host>:7788/v1/fleet` (polls peers; no token on the tailnet).
   The dashboard is the same URL without `/v1/fleet`.

## Decide

- `status` must be `ok`; `stale`, `lost`, `no-agent`, `offline` mean the numbers are not
  current — do not delegate there without checking.
- Rule out a host when: `cpu.percent` > 85, `memory.available_gb` is below what the task
  needs, `disks.primary.free_gb` is below what the task writes, or the task needs a GPU and
  `gpu.gpus` is empty / `utilization_percent` is high.
- Claude usage: `claude_accounts[].session_percent` is the 5-hour window, `weekly_percent`
  the 7-day window, `scoped` per-model windows (e.g. Fable). Near 90 % on every account on a
  host means a Claude session there will throttle soon; pick a host (or account) with
  headroom. Reset times are in `session_resets_at` / `weekly_resets_at`.
- Map a fleet `hostname` to the Orca target with the same name (`orca host list`,
  `orca environment list`) or the SSH alias of that machine.
- A machine missing from the list has no agent: say so instead of assuming it is free.

## Keep it honest

Report the numbers you saw (host, CPU, free RAM, free disk, GPU, Claude %) and the report age
when you explain a choice. Never edit the agent, hub, or its token to make a host look free.
