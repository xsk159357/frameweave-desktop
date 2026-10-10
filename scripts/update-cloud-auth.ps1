# Update FrameWeave cloud authorization server
# Usage: pwsh scripts/update-cloud-auth.ps1 [-CheckOnly] [-Server 43.108.23.200]
# Credentials (never stored in this repo):
#   1) env FW_SERVER_PASSWORD   (recommended for one-off; Korea alias: FW_KOREA_PASSWORD)
#   2) file $HOME\.frameweave\server.env   (local machine secret, key FW_SERVER_PASSWORD)
#   3) SSH key $HOME\.ssh\frameweave_auth  (preferred, no password needed)
param(
  [string]$Server = "43.108.23.200",
  [int]$Port = 22,
  [string]$User = "root",
  [string]$CloudDir = "/opt/frameweave-cloud",
  [string]$Service = "frameweave-cloud.service",
  [string]$SourcePath = (Join-Path $PSScriptRoot "..\backend\cloud_server.py"),
  [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"
function Fail($msg) { Write-Host "[update] FAIL: $msg" -ForegroundColor Red; exit 1 }
$resolved = (Resolve-Path $SourcePath).Path
if (-not (Test-Path -LiteralPath $resolved)) { Fail "source not found: $resolved" }
Write-Host "[update] source: $resolved"

# --- credentials resolution ---
$pw = $env:FW_SERVER_PASSWORD
if (-not $pw) { $pw = $env:FW_KOREA_PASSWORD }   # Korea migration alias (env-only)
$envFile = Join-Path $HOME ".frameweave\server.env"
if (-not $pw -and (Test-Path -LiteralPath $envFile)) {
  $line = (Get-Content -LiteralPath $envFile -Raw) -split "\r?\n" | Where-Object { $_ -match "^FW_SERVER_PASSWORD=" } | Select-Object -First 1
  if ($line) { $pw = $line.Substring("FW_SERVER_PASSWORD=".Length).Trim() }
}
$keyPath = Join-Path $HOME ".ssh\frameweave_auth"
$useKey = Test-Path -LiteralPath $keyPath
if (-not $pw -and -not $useKey) { Fail "no credential: set FW_SERVER_PASSWORD env, create $envFile, or configure $keyPath" }
Write-Host "[update] host=$Server port=$Port user=$User auth=" $(if ($useKey) { "ssh-key" } else { "password" })

# build embedded python (credentials pass via env only, never via repo file)
$py = @'
import json, os, sys, time, urllib.request
import paramiko
host = os.environ["FW_SERVER_HOST"]; port = int(os.environ["FW_SERVER_PORT"])
user = os.environ["FW_SERVER_USER"]; cloud = os.environ["FW_CLOUD_DIR"]
service = os.environ["FW_SERVICE"]
pw = os.environ.get("FW_SERVER_PASSWORD", "") or None
key = os.environ.get("FW_SSH_KEY", "") or None
local = os.environ["FW_SRC_PATH"]
check_only = os.environ.get("FW_CHECK_ONLY", "0") == "1"
kw = dict(hostname=host, port=port, username=user, timeout=15, look_for_keys=False, allow_agent=False)
if key and os.path.exists(key): kw["key_filename"] = key
if pw: kw["password"] = pw
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(**kw)
def run(cmd, t=60):
    i, o, e = c.exec_command(cmd, timeout=t)
    out = o.read().decode(errors="replace"); err = e.read().decode(errors="replace")
    return out, err
# remote backup of current server source
ts = time.strftime("%Y%m%d%H%M%S")
out, err = run("mkdir -p %s/backups/%s && cp -a %s/cloud_server.py %s/backups/%s/ 2>/dev/null; echo BACKUP_OK" % (cloud, ts, cloud, cloud, ts))
print(out.strip()); 
if not check_only:
    sftp = c.open_sftp(); sftp.put(local, cloud + "/cloud_server.py"); sftp.close(); print("[upload] cloud_server.py ->", cloud)
out, err = run("python3 -m py_compile %s/cloud_server.py && echo COMPILE_OK" % cloud)
print(out.strip()); 
if "COMPILE_OK" not in out: Fail(err or out)
if not check_only:
    out, err = run("systemctl restart %s && sleep 2 && systemctl is-active %s" % (service, service))
    print(out.strip())
    if "active" not in out: Fail("service not active: " + err)
time.sleep(1)
out, err = run("curl -s http://127.0.0.1:8789/api/health")
print("[health]", out.strip())
h = json.loads(out or "{}")
if not h.get("ok") or h.get("protocol_version") != 3:
    Fail("protocol/revision mismatch: %s" % out)
print("[health] service=%s version=%s protocol=%s auth=%s" % (h.get("service"), h.get("version"), h.get("protocol_version"), h.get("auth_revision")))
out, err = run("curl -s -X POST http://127.0.0.1:8789/api/auth/email/send-code -H 'Content-Type: application/json' --data '{\"email\":\"bad@163.com\",\"scene\":\"register\"}'")
print("[route]", out.strip())
if "QQ" not in out: Fail("send-code validation unexpected: " + out)
c.close(); print("[update] OK")
'@
$env:FW_SERVER_HOST=$Server; $env:FW_SERVER_PORT=[string]$Port; $env:FW_SERVER_USER=$User
$env:FW_CLOUD_DIR=$CloudDir; $env:FW_SRC_PATH=$resolved; $env:FW_SERVICE=$Service
$env:FW_SSH_KEY = if ($useKey) { $keyPath } else { "" }
$env:FW_CHECK_ONLY=$(if ($CheckOnly) { "1" } else { "0" })
if ($useKey) { $env:FW_SERVER_PASSWORD="" } else { $env:FW_SERVER_PASSWORD=$pw }
$py | python -
if ($LASTEXITCODE -ne 0) { Fail "python runner exited $LASTEXITCODE" }
Write-Host "[update] done: $Service on $Server"
