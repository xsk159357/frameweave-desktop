# upload-release-korea.ps1 — 上传 release/<version>/ 三件套到韩国授权服务器更新源 shouquan.frameweave.top/update
# 用法:
#   pwsh scripts/upload-release-korea.ps1 -Version 0.3.2            # 上传 + 容器内确认 + 公网验证
#   pwsh scripts/upload-release-korea.ps1 -Version 0.3.2 -CheckOnly # 仅连通/目录核对，不上传
# 凭据（优先级）: env FW_KOREA_PASSWORD > server.env 键 FW_KOREA_PASSWORD > server.env 键 FW_SERVER_PASSWORD(旧) > .tmp/kr_fix_upload.py(遗留提取)
#   ——韩国服务器密码与旧日本中转 server.env 的 FW_SERVER_PASSWORD 不同，建议迁移 SSH 密钥 ~/.ssh/frameweave_auth
param(
  [Parameter(Mandatory=$true)][string]$Version,
  [string]$Server = "43.108.23.200",
  [string]$SiteRoot = "/opt/1panel/www/sites/shouquan.frameweave.top/update",
  [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$relDir = Join-Path $root ("release/" + $Version)
if (-not (Test-Path $relDir)) { throw "release 目录不存在: $relDir（先跑 build-installer.ps1 + check_release.ps1）" }
$files = @("latest.yml", "FrameWeave-Setup-$Version.exe", "FrameWeave-Setup-$Version.exe.blockmap")
foreach ($f in $files) { if (-not (Test-Path (Join-Path $relDir $f))) { throw "缺三件套文件: $f" } }
Write-Host "[upload-kr] version=$Version host=$Server site=$SiteRoot checkOnly=$CheckOnly"

# --- 凭据解析（不落盘，不打印值）---
$pw = $env:FW_KOREA_PASSWORD
if (-not $pw) { $pw = $env:FW_SERVER_PASSWORD }
$envFile = Join-Path $HOME ".frameweave\server.env"
if (-not $pw -and (Test-Path -LiteralPath $envFile)) {
  $raw = Get-Content -LiteralPath $envFile -Raw
  foreach ($key in @("FW_KOREA_PASSWORD", "FW_SERVER_PASSWORD")) {
    $line = ($raw -split "\r?\n" | Where-Object { $_ -match "^$key=" } | Select-Object -First 1)
    if ($line) { $pw = $line.Substring($key.Length + 1).Trim(); if ($pw) { break } }
  }
}
if (-not $pw) {
  $hist = Join-Path (Join-Path $root ".tmp") "kr_fix_upload.py"
  if (Test-Path $hist) {
    $src = Get-Content $hist -Raw
    $m = [regex]::Match($src, "c\.connect\('([^']+)'[^)]*password='([^']*)'")
    if ($m.Success) { $Server = $m.Groups[1].Value; $pw = $m.Groups[2].Value; Write-Host "[upload-kr] ⚠ 回退 .tmp/kr_fix_upload.py 遗留凭据（建议迁移 SSH 密钥）" }
  }
}
if (-not $pw) { throw "未找到韩国服务器凭据：设 env FW_KOREA_PASSWORD 或 server.env 对应键" }

$py = @'
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import paramiko
host = os.environ["KR_HOST"]; site = os.environ["KR_SITE"]; ver = os.environ["KR_VERSION"]
rel = os.environ["KR_RELDIR"]; check = os.environ.get("KR_CHECK", "0") == "1"
pw = os.environ.get("KR_PASSWORD", "")
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, port=22, username="root", password=pw, timeout=20)
def run(cmd, t=40):
    i, o, e = c.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "replace").strip() or e.read().decode("utf-8", "replace").strip() or "(none)"
print("=== 远端目录核对 ===")
print(run("ls -la %s/ | tail -8" % site))
if check:
    print("CheckOnly：未上传")
else:
    files = ["latest.yml", "FrameWeave-Setup-%s.exe" % ver, "FrameWeave-Setup-%s.exe.blockmap" % ver]
    sftp = c.open_sftp()
    try:
        for name in files:
            local = os.path.join(rel, name); remote = site + "/" + name
            if not os.path.exists(local):
                print("  MISSING %s" % local); continue
            sftp.put(local, remote); st = sftp.stat(remote)
            print("  %s -> %s (%d B)" % (name, remote, st.st_size))
    finally:
        sftp.close()
    print("=== 容器内确认 ===")
    print(run('docker exec 1Panel-openresty-871J sh -c "ls -la /www/sites/shouquan.frameweave.top/update/"'))
    print("=== 公网验证 ===")
    print(run("curl -s -m 25 -o /dev/null -w 'latest.yml: http=%{http_code} size=%{size_download}\\n' https://shouquan.frameweave.top/update/latest.yml"))
    print(run("curl -s -m 40 -o /dev/null -w 'setup: http=%{http_code} size=%{size_download}\\n' https://shouquan.frameweave.top/update/FrameWeave-Setup-%s.exe" % ver))
c.close(); print("DONE")
'@
$env:KR_HOST = $Server; $env:KR_SITE = $SiteRoot; $env:KR_VERSION = $Version
$env:KR_RELDIR = $relDir; $env:KR_CHECK = $(if ($CheckOnly) { "1" } else { "0" }); $env:KR_PASSWORD = $pw
try { $py | python - } finally { Remove-Item Env:KR_* -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) { throw "upload python runner exited $LASTEXITCODE" }
Write-Host "[upload-kr] done: v$Version -> 韩国更新源"