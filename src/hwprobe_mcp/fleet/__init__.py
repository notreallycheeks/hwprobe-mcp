"""Fleet: a tiny per-host telemetry agent plus a client that reads every host on the tailnet.

`hwprobe-fleet serve` runs on each machine and answers `GET /v1/stats` (this host) and
`GET /v1/fleet` (every reachable host) on the Tailscale interface. `hwprobe-fleet` (the
client), the `fleet_status` MCP tool and the Orca panel all read those endpoints so an agent
can see CPU, RAM, disk, GPU and Claude usage across the fleet before delegating work.
"""

from __future__ import annotations

DEFAULT_PORT = 7788
SCHEMA_VERSION = 1
