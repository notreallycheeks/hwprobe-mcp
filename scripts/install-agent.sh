#!/usr/bin/env bash
# Install the hwprobe-fleet agent on Linux or macOS and start it at login.
#   curl -fsSL https://raw.githubusercontent.com/notreallycheeks/hwprobe-mcp/main/scripts/install-agent.sh | \
#     HWPROBE_FLEET_HUB=http://hub-host:7790 HWPROBE_FLEET_TOKEN=<token> bash
# Env: HWPROBE_FLEET_HUB / HWPROBE_FLEET_TOKEN (optional hub), HWPROBE_SRC (git URL or local path),
#      HWPROBE_FLEET_PORT (default 7788).
set -euo pipefail

SRC="${HWPROBE_SRC:-git+https://github.com/notreallycheeks/hwprobe-mcp.git}"
if [ "$(uname -s)" = "Darwin" ]; then
  BASE="$HOME/Library/Application Support/hwprobe-fleet"
else
  BASE="${XDG_DATA_HOME:-$HOME/.local/share}/hwprobe-fleet"
fi
VENV="$BASE/venv"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "python3 >= 3.10 is required (apt install python3-venv / brew install python)"; exit 1; }

mkdir -p "$BASE"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q --upgrade "$SRC"

# Persist hub settings so the login service finds them without environment variables.
if [ -n "${HWPROBE_FLEET_HUB:-}" ]; then
  CFG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hwprobe"
  mkdir -p "$CFG_DIR"
  "$VENV/bin/python" - "$CFG_DIR/fleet.json" <<'PY'
import json, os, sys
p = sys.argv[1]
d = {}
if os.path.exists(p):
    try: d = json.load(open(p))
    except Exception: d = {}
d["hub"] = os.environ["HWPROBE_FLEET_HUB"].rstrip("/")
if os.environ.get("HWPROBE_FLEET_TOKEN"): d["token"] = os.environ["HWPROBE_FLEET_TOKEN"]
if os.environ.get("HWPROBE_FLEET_PORT"): d["port"] = int(os.environ["HWPROBE_FLEET_PORT"])
json.dump(d, open(p, "w"), indent=2); os.chmod(p, 0o600)
print(f"wrote {p}")
PY
fi

"$VENV/bin/hwprobe-fleet" uninstall >/dev/null 2>&1 || true
"$VENV/bin/hwprobe-fleet" install ${HWPROBE_FLEET_PORT:+--port "$HWPROBE_FLEET_PORT"}
mkdir -p "$HOME/.local/bin"; ln -sf "$VENV/bin/hwprobe-fleet" "$HOME/.local/bin/hwprobe-fleet"
sleep 2
curl -fsS "http://127.0.0.1:${HWPROBE_FLEET_PORT:-7788}/healthz" && echo && echo "agent is up; CLI linked at ~/.local/bin/hwprobe-fleet"
