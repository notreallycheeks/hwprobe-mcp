# Fleet hub deployment

The hub is the one process every agent reports to and every AI agent reads from. It never
connects out, so it works anywhere with an inbound port, including Docker Desktop on Windows.

## Run it (Docker)

```bash
git clone https://github.com/notreallycheeks/hwprobe-mcp.git && cd hwprobe-mcp
export HWPROBE_FLEET_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs --tail 5     # shows the token if you let it generate one
curl http://127.0.0.1:7790/healthz
```

PowerShell on Windows:

```powershell
git clone https://github.com/notreallycheeks/hwprobe-mcp.git; cd hwprobe-mcp
$env:HWPROBE_FLEET_TOKEN = -join ((48..57 + 65..90 + 97..122) | Get-Random -Count 32 | % {[char]$_})
docker compose -f deploy/docker-compose.yml up -d --build
```

The hub listens on `7790` on every interface of the Docker host; other tailnet machines reach it
at `http://<docker-host-tailscale-name>:7790`. Keep the token private: it unlocks the account
emails and usage numbers of every host.

Without Docker: `pip install hwprobe-mcp` (or `pip install git+https://github.com/notreallycheeks/hwprobe-mcp.git`)
then `hwprobe-fleet hub --port 7790` (token generated into the state dir on first run).

## Point the agents at it

On every machine that should appear in the fleet:

```bash
hwprobe-fleet uninstall 2>/dev/null
HWPROBE_FLEET_HUB=http://hub-host:7790 HWPROBE_FLEET_TOKEN=<token> hwprobe-fleet install
```

or put it in the config file instead of the environment
(`~/.config/hwprobe/fleet.json`, `%APPDATA%\hwprobe\fleet.json` on Windows):

```json
{ "hub": "http://hub-host:7790", "token": "<token>" }
```

`install` writes a login service (systemd --user, launchd, or Task Scheduler) that runs
`hwprobe-fleet serve`, which reads the hub settings from the environment of the install step
or from that file.

## Point the AI agents at it

Claude Code (every machine, once):

```bash
claude mcp add --transport http --scope user fleet-hub http://hub-host:7790/mcp \
  --header "Authorization: Bearer <token>"
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.fleet-hub]
url = "http://hub-host:7790/mcp"
http_headers = { Authorization = "Bearer <token>" }
```

Any other MCP client: streamable-HTTP transport, URL `http://hub-host:7790/mcp`, the same
header. Tools: `pick_host`, `fleet_status`, `fleet_table`, `host_stats`, `claude_usage`.

Browser dashboard: `http://hub-host:7790/` (asks for the token once, keeps it in the browser).

## Caveats

- A container only sees its own VM. The Docker host still needs the native agent to report its
  own CPU/GPU/Claude accounts.
- Hosts that stop reporting show `stale` after 90 s and `lost` after 15 min; they are kept so
  an agent can see that a machine went quiet rather than vanished. `DELETE /v1/hosts/<name>`
  removes one.
- State (last reports, generated token) lives in the `fleet-data` volume.
