# Install the hwprobe-fleet agent on Windows and start it at logon (Task Scheduler, current user).
#   irm https://raw.githubusercontent.com/notreallycheeks/hwprobe-mcp/main/scripts/install-agent.ps1 | iex
# Env (optional): HWPROBE_FLEET_HUB, HWPROBE_FLEET_TOKEN, HWPROBE_SRC, HWPROBE_FLEET_PORT.
$ErrorActionPreference = 'Stop'
$src  = if ($env:HWPROBE_SRC) { $env:HWPROBE_SRC } else { 'git+https://github.com/notreallycheeks/hwprobe-mcp.git' }
$base = Join-Path $env:LOCALAPPDATA 'hwprobe-fleet'
$venv = Join-Path $base 'venv'

$py = $null
foreach ($c in @('py -3.12', 'py -3.11', 'py -3.10', 'py -3', 'python')) {
  try {
    $v = & cmd /c "$c -c `"import sys;print(sys.version_info>=(3,10))`"" 2>$null
    if ($v -match 'True') { $py = $c; break }
  } catch {}
}
if (-not $py) { throw 'Python 3.10+ is required: winget install Python.Python.3.12' }

New-Item -ItemType Directory -Force $base | Out-Null
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) { & cmd /c "$py -m venv `"$venv`"" }
$vpy = Join-Path $venv 'Scripts\python.exe'
& $vpy -m pip install -q --upgrade pip
& $vpy -m pip install -q --upgrade $src

if ($env:HWPROBE_FLEET_HUB) {
  $cfgDir = Join-Path $env:APPDATA 'hwprobe'
  New-Item -ItemType Directory -Force $cfgDir | Out-Null
  $cfgPath = Join-Path $cfgDir 'fleet.json'
  $cfg = @{}
  if (Test-Path $cfgPath) {
    # Windows PowerShell 5.1 has no ConvertFrom-Json -AsHashtable, so copy the properties over.
    try { (Get-Content $cfgPath -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $cfg[$_.Name] = $_.Value } } catch { $cfg = @{} }
  }
  $cfg.hub = $env:HWPROBE_FLEET_HUB.TrimEnd('/')
  if ($env:HWPROBE_FLEET_TOKEN) { $cfg.token = $env:HWPROBE_FLEET_TOKEN }
  if ($env:HWPROBE_FLEET_PORT) { $cfg.port = [int]$env:HWPROBE_FLEET_PORT }
  # UTF-8 without a BOM (Set-Content -Encoding UTF8 adds one on Windows PowerShell 5.1).
  [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
  Write-Host "wrote $cfgPath"
}

$cli = Join-Path $venv 'Scripts\hwprobe-fleet.exe'
& $cli uninstall 2>$null | Out-Null
if ($env:HWPROBE_FLEET_PORT) { & $cli install --port $env:HWPROBE_FLEET_PORT } else { & $cli install }
# Allow tailnet peers through Windows Firewall for the agent port (current user rule; needs no admin for private profile prompts on first run).
$port = if ($env:HWPROBE_FLEET_PORT) { [int]$env:HWPROBE_FLEET_PORT } else { 7788 }
try {
  if (-not (Get-NetFirewallRule -DisplayName 'hwprobe-fleet' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'hwprobe-fleet' -Direction Inbound -Protocol TCP -LocalPort $port -RemoteAddress 100.64.0.0/10 -Action Allow | Out-Null
  }
} catch { Write-Warning "Could not add a firewall rule (run as admin): New-NetFirewallRule -DisplayName hwprobe-fleet -Direction Inbound -Protocol TCP -LocalPort $port -RemoteAddress 100.64.0.0/10 -Action Allow" }
Start-Sleep 3
Invoke-RestMethod "http://127.0.0.1:$port/healthz" | ConvertTo-Json -Compress
Write-Host "agent is up; CLI at $cli"
