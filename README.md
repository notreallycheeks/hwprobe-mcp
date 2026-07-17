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
