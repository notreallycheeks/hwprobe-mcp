# hwprobe-mcp — notes for AI agents working on this repo

## What this is
An MCP server (Python, FastMCP) exposing hardware inventory + live sensors to agents. See `README.md`.

## Layout
- `src/hwprobe_mcp/server.py` — FastMCP server; each `@mcp.tool()` is a thin wrapper over `aggregate`.
- `src/hwprobe_mcp/aggregate.py` — composes collectors into the 6 tool responses.
- `src/hwprobe_mcp/collectors/` — one module per backend; each is independent and degrades gracefully.
- `src/hwprobe_mcp/util.py` — `run()` / `run_json()` (never raise), platform flags, `envelope()`.
- `src/hwprobe_mcp/__main__.py` — `--selftest` (dumps all collector JSON), `--version`, else runs the server.

## Conventions
- **Collectors never raise.** Catch everything; append a string to the `warns` list passed in.
- Every tool returns the `envelope(sources, data, warnings, ok)` shape — keep it consistent.
- A missing binary, absent device, or permission error is a `warnings[]` entry, not an error.
- Numbers stay numbers (parse CSV/JSON to int/float); unavailable values become `null`, not `"N/A"`.

## Verify locally
```bash
PYTHONPATH=src python -m hwprobe_mcp --selftest | jq .   # no `mcp` dep needed for selftest
pytest                                                    # smoke tests
```
Full sensor/inventory/SMART data needs root; unprivileged runs are expected to show warnings.
