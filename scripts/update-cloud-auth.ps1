# Update FrameWeave cloud authorization server
# Usage: pwsh scripts/update-cloud-auth.ps1 [-CheckOnly] [-Server 43.108.23.200]
# Credentials (never stored in this repo):
#  1) env FW_SERVER_PASSWORD   (recommended for one-off; Korea alias: FW_KOREA_PASSWORD)
#  2) file $HOME\.frameweave\server.env   (local machine secret, key FW_SERVER_PASSWORD)
#  3) SSH key $HOME\.ssh\frameweave_auth  (preferred, no password needed)
param(
  [string]$Server = "43.108.23.200",
  [int]$Port = 22,
  [string]$User = "root",
  [string]$CloudDir = "/opt/frameweave-cloud",
  [string]$Service = "frameweave-cloud.service",
  [string]$SourcePath = (Join-Path $PSScriptRoot "..\backend\cloud_server.py"),
  [string]$AdminHtmlPath = (Join-Path $PSScriptRoot "..\backend\admin.html"),
  [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"
function Fail($msg) { Write-Host "[update] FAIL: $msg" -ForegroundColor Red; exit 1 }
$resolved = (Resolve-Path $SourcePath).Path
if (-not (Test-Path -LiteralPath $resolved)) { Fail "source not found: $resolved" }
$admResolved = $null
if (Test-Path -LiteralPath $AdminHtmlPath) { $admResolved = (Resolve-Path $AdminHtmlPath).Path }
Write-Host "[update] source: $resolved  admin.html: $(if ($admResolved) { $admResolved } else { '(none)' })"

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
import json, os, sys, time, urllib.request, hashlib
import paramiko
host = os.environ["FW_SERVER_HOST"]; port = int(os.environ["FW_SERVER_PORT"])
user = os.environ["FW_SERVER_USER"]; cloud = os.environ["FW_CLOUD_DIR"]
service = os.environ["FW_SERVICE"]
pw = os.environ.get("FW_SERVER_PASSWORD", "") or None
key = os.environ.get("FW_SSH_KEY", "") or None
local = os.environ["FW_SRC_PATH"]
check_only = os.environ.get("FW_CHECK_ONLY", "0") == "1"
admin_local = os.environ.get("FW_ADMIN_HTML", "") or ""
kw = dict(hostname=host, port=port, username=user, timeout=15, look_for_keys=False, allow_agent=False)
if key and os.path.exists(key): kw["key_filename"] = key
if pw: kw["password"] = pw
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(**kw)
def run(cmd, t=60):
    i, o, e = c.exec_command(cmd, timeout=t)
    out = o.read().decode(errors="replace"); err = e.read().decode(errors="replace")
    return out, err
def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()
# remote backup of current server source
ts = time.strftime("%Y%m%d%H%M%S")
out, err = run("mkdir -p %s/backups/%s && cp -a %s/cloud_server.py %s/backups/%s/ 2>/dev/null; echo BACKUP_OK" % (cloud, ts, cloud, cloud, ts))
print(out.strip()); 
if not check_only:
    sftp = c.open_sftp()
    sftp.put(local, cloud + "/cloud_server.py"); print("[upload] cloud_server.py ->", cloud)
    if admin_local:
        try: sftp.stat(cloud + "/admin.html")
        except Exception: pass
        sftp.put(admin_local, cloud + "/admin.html"); print("[upload] admin.html ->", cloud)
        st = sftp.stat(cloud + "/admin.html"); print("[admin] remote size", st.st_size)
    sftp.close()
    # t3：上传后远端 sha256 完整性校验，防半包/截断上传（sha256 不匹配即中止，不重启服务）
    for name, remote in [("cloud_server.py", cloud + "/cloud_server.py")] + ([("admin.html", cloud + "/admin.html")] if admin_local else []):
        lo = local if name == "cloud_server.py" else admin_local
        ls = sha256_file(lo)
        out, err = run("sha256sum %s | cut -d' ' -f1" % remote)
        rs = (out or "").strip()
        if rs != ls:
            print("[verify] FAIL %s local=%s remote=%s" % (name, ls, rs)); c.close(); sys.exit(2)
        print("[verify] sha256 OK %s (%s)" % (name, ls[:12]))
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
$env:FW_ADMIN_HTML=$(if ($admResolved) { $admResolved } else { "" })
$env:FW_SSH_KEY = if ($useKey) { $keyPath } else { "" }
$env:FW_CHECK_ONLY=$(if ($CheckOnly) { "1" } else { "0" })
if ($useKey) { $env:FW_SERVER_PASSWORD="" } else { $env:FW_SERVER_PASSWORD=$pw }
# t3：python runner 优先 venv（0.3.5 教训：本机可能无裸 python）；无则报错退出
$pyRunner = if (Get-Command python -ErrorAction SilentlyContinue) { (Get-Command python).Source } else { Join-Path $PSScriptRoot "..\backend\.venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $pyRunner)) { Fail "python runner not found: $pyRunner（安装 backend/.venv 或把 python 加入 PATH）" }
$py | & $pyRunner -
if ($LASTEXITCODE -ne 0) { Fail "python runner exited $LASTEXITCODE" }
Write-Host "[update] done: $Service on $Server"
