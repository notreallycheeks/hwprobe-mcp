# Fleet panel for Orca

A right-sidebar panel that shows every machine on the tailnet: CPU, RAM, disk, GPU meters and
the Claude accounts signed in on each host with their 5-hour / 7-day usage.

It reads `/v1/fleet` from the fleet hub (`http://hub-host:7790`, token required) or from the
local agent (`http://127.0.0.1:7788`, no token) — set the URL and token under
Settings → Plugins → Fleet.

```bash
orca plugin validate orca-plugin --json
orca plugin dev load orca-plugin        # prints the access it asks for; re-run with --approve <fingerprint>
orca plugin open local.hwprobe-fleet --panel fleet
```

Capabilities: `storage:own` (keeps the last document across reloads) and `settings:own`.
