# hwprobe-mcp

**A hardware probe for AI agents, over [MCP](https://modelcontextprotocol.io).**

`hwprobe-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server that gives an
AI agent (any MCP client) both **deep hardware inventory** *and*
**live sensor telemetry** through a small set of clean, JSON-returning tools.

Most "system monitor" MCP servers are thin `psutil` wrappers that only report utilization. `hwprobe-mcp`
deliberately fuses three layers so an agent gets the *whole* picture in one place:

| Layer | Backends |
|-------|----------|
| **Cross-platform core** | [`psutil`](https://github.com/giampaolo/psutil) — CPU/mem/disk/net + component temps, fans, battery |
| **Rich Linux sensors** | `sensors -j` (lm-sensors) — temperatures, fan RPM, **voltages**, **power** |
| **Deep inventory** | `lscpu -J`, `lsblk -O -J`, `dmidecode`, `lspci` (Linux) · `system_profiler -json` (macOS) · CIM/WMI (Windows) |
| **Devices** | `nvidia-smi` (GPU inventory + telemetry) · `smartctl -j` (disk SMART: model, temp, health, hours) |

Everything degrades gracefully: a missing tool, an absent GPU, or a lack of root privileges becomes a
`warnings[]` entry, never a crash.

---

## Tools

| MCP tool | What it returns |
|----------|-----------------|
| `hardware_inventory` | Static deep inventory — CPU model/cores/arch/cache, RAM + DIMM layout, disks (model/serial/size), GPUs, motherboard/BIOS, OS/platform. |
| `live_sensors` | Live snapshot — CPU/component temperatures, fan RPM, battery, plus voltages/power (via lm-sensors) and NVIDIA GPU temp/power/util. |
| `cpu_status` | CPU identity + live per-core utilization %, per-core frequency, load average, core temperatures. |
| `gpu_status` | GPU inventory + live telemetry (NVIDIA via `nvidia-smi`: temp, util, power, clocks, memory). |
| `disk_health` | Per-disk SMART: model, serial, firmware, capacity, temperature, SMART pass/fail, power-on hours, power cycles. |
| `system_snapshot` | Everything above in a single call — the "tell me everything about this machine" tool. |
| `fleet_status` / `pick_host` / `claude_usage` | Fleet view across every tailnet host, a ranked "where should this run" answer, and Claude account usage — see **Fleet** below. |
| `check_dependencies` | Which optional backends are installed vs missing, what each unlocks, and the exact command to install any missing one (auto-detects the package manager). |

Every tool returns a consistent envelope:

```json
{
  "ok": true,
  "platform": "Linux",
  "sources": ["psutil", "lm-sensors"],
  "warnings": ["nvidia-smi: NVIDIA driver not loaded"],
  "data": { "...": "..." }
}
```

---

## Install

Requires Python **3.10+**.

```bash
# from source (until published to PyPI)
git clone git@github.com:notreallycheeks/hwprobe-mcp.git
cd hwprobe-mcp
pip install -e .
```

For the fullest data on Linux, install the native helpers (all optional):

```bash
sudo apt install lm-sensors smartmontools pciutils util-linux dmidecode
sudo sensors-detect --auto      # one-time, sets up lm-sensors
```

> NVIDIA GPU telemetry uses the `nvidia-smi` binary shipped with the NVIDIA driver —
> there is no pip extra to install.

### Checking what's installed

hwprobe works with whatever is present and degrades gracefully — but it will also *tell you*
what's missing and how to install it. Run the built-in doctor:

```bash
hwprobe-mcp --doctor
```

...or have your agent call the **`check_dependencies`** tool. Each missing backend comes with the
exact install command for your platform's package manager, and the JSON-returning tools embed the
same hint in their `warnings`.

Two backends need elevated privileges to return data — **`smartctl`** (disk SMART) and
**`dmidecode`** (motherboard/BIOS/DIMM). Run the server as root, or grant *scoped* passwordless
sudo just for smartctl so `disk_health` works from the unprivileged server:

```bash
echo "$USER ALL=(root) NOPASSWD: $(command -v smartctl)" | sudo tee /etc/sudoers.d/hwprobe-smartctl
sudo chmod 0440 /etc/sudoers.d/hwprobe-smartctl
```

hwprobe automatically uses `sudo -n smartctl` when it isn't root, so no code changes are needed.

---

## Fleet: every machine on your tailnet, in one view

`hwprobe-fleet` turns hwprobe into a fleet monitor so an AI agent can see **which machine is
busy and which is idle before it delegates work**. Each host runs a tiny agent; a hub (or any
agent) aggregates them; agents read the result over MCP, CLI or HTTP.

| Piece | Command | What it does |
|-------|---------|--------------|
| Agent | `hwprobe-fleet serve` / `install` | Serves this host's CPU %, RAM, disk, GPU and **Claude usage** on `:7788` (loopback + Tailscale IP only) and, with `--hub`, pushes it to the hub every 15 s |
| Hub | `hwprobe-fleet hub` (or `deploy/docker-compose.yml`) | Collects reports on `:7790`, serves `/v1/fleet`, a dashboard at `/`, and **MCP over HTTP at `/mcp`** |
| Client | `hwprobe-fleet`, `hwprobe-fleet pick --gpu` | Table / ranked recommendation, from the hub or by polling every Tailscale peer directly |
| MCP tools | `fleet_status`, `pick_host`, `claude_usage`, `host_stats`, `fleet_table` | On the hub (HTTP) and in the stdio `hwprobe-mcp` server |
| Orca panel | `orca-plugin/` | Right-sidebar "Fleet" panel in [Orca](https://onorca.dev) |
| Skill | `skills/fleet-status/SKILL.md` | Tells Claude Code / Codex agents when and how to consult the fleet |

### Claude usage

For every Claude Code account signed in on a host (`~/.claude`, `$CLAUDE_CONFIG_DIR`, Orca
managed-account folders), the agent reports email, plan, the **5-hour** and **7-day** windows,
per-model weekly windows and reset times — the same numbers as Claude Code's `/usage` — plus
whatever [Orca](https://onorca.dev) tracks (`orca account list --json`) when Orca is installed.
Tokens are read only to make that request and never leave the machine.

```
HOST     OS     STATUS  CPU  RAM           DISK                       GPU             CLAUDE (5h / 7d / per-model)       AGE
alpha*   Linux  ok      5%   12/64G (19%)  310G free/954G (68% used)  RTX 4070 2%     me@example.com 5h:93% 7d:31% f:24% 2s
```

### Install the agent on a machine

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/notreallycheeks/hwprobe-mcp/main/scripts/install-agent.sh | \
  HWPROBE_FLEET_HUB=http://hub-host:7790 HWPROBE_FLEET_TOKEN=<token> bash
```

```powershell
# Windows
$env:HWPROBE_FLEET_HUB='http://hub-host:7790'; $env:HWPROBE_FLEET_TOKEN='<token>'
irm https://raw.githubusercontent.com/notreallycheeks/hwprobe-mcp/main/scripts/install-agent.ps1 | iex
```

Both create a venv, install this package, write `fleet.json`, and register a login service
(systemd --user / launchd / Task Scheduler). Leave the hub variables out to run peer-to-peer
only. Everything is per-user; nothing needs root.

### Run the hub

See [`deploy/README.md`](deploy/README.md): `docker compose -f deploy/docker-compose.yml up -d`
on the machine that stays up, then `claude mcp add --transport http fleet-hub http://<host>:7790/mcp --header "Authorization: Bearer <token>"`
on every machine. The hub only ever receives connections, so Docker NAT is fine.

### Install the Orca panel

This repository is also an Orca plugin catalog (`orca-catalog.json`). In Orca, open
Settings → Plugins → Add catalog, enter `https://github.com/notreallycheeks/hwprobe-mcp.git`
and install **Fleet**, or open
`orca://plugins/notreallycheeks.hwprobe-fleet?catalog=https://github.com/notreallycheeks/hwprobe-mcp.git`.
Then set the hub URL and token under Settings → Plugins → Fleet. For a new release, commit the
plugin, run `orca plugin release-entry orca-plugin` and add its entry to the top of `releases`.

### Security model

Agents listen only on loopback and the Tailscale address and refuse non-tailnet source IPs.
The hub requires a bearer token (generated on first start if you do not set one) because its
Docker port is reachable from every interface of its host. Reports carry hostnames, resource
figures and Claude account e-mails/usage — fine for a personal tailnet, so keep the token private.

## Use with an MCP client

Add it to your MCP client's server config:

```json
{
  "mcpServers": {
    "hwprobe": {
      "command": "hwprobe-mcp"
    }
  }
}
```

Then ask your agent things like *"what's this machine's CPU and how hot is it right now?"* or
*"check disk SMART health and current GPU power draw."*

## Try it without an MCP client

`--selftest` runs every collector and dumps the JSON an agent would see — handy for verifying your
box and for CI:

```bash
# from the repo root, before install:
PYTHONPATH=src python -m hwprobe_mcp --selftest | jq .
# or, once installed (pip install -e .):
hwprobe-mcp --selftest
```

---

## Platform support

| Capability | Linux | macOS | Windows |
|------------|:-----:|:-----:|:-------:|
| Inventory (CPU/mem/disk/GPU/OS) | ✅ full | ✅ (system_profiler) | ✅ (CIM/WMI) |
| Component temps / fans | ✅ psutil + lm-sensors | ⚠️ limited | ⚠️ needs LibreHardwareMonitor |
| Voltages / power | ✅ lm-sensors | ⚠️ | ⚠️ |
| Battery | ✅ | ✅ | ✅ |
| NVIDIA GPU telemetry | ✅ | — | ✅ |
| Disk SMART | ✅ (root) | ✅ (root) | ✅ (admin) |

> **Note:** deep motherboard/BIOS/DIMM inventory (`dmidecode`) and full SMART data need root/admin.
> Without it, `hwprobe-mcp` returns everything it *can* read and flags the rest in `warnings`.

## Roadmap

- [ ] Windows deep sensors via a bundled LibreHardwareMonitor bridge
- [ ] macOS `powermetrics` power/thermal integration (opt-in, needs sudo)
- [ ] AMD/Intel GPU telemetry (`rocm-smi`, `intel_gpu_top`)
- [ ] Optional streaming/`subscribe` tool for continuous sensor sampling
- [ ] Publish to PyPI + `uvx hwprobe-mcp`

## License

MIT © 2026 notreallycheeks
