# release-github-korea.ps1 — 经韩国授权服务器 SSH 代理，发布 GitHub Release（electron-updater github provider）
# 背景：本机直连 GitHub 上传大文件（200MB+）不稳定，0.3.1/0.3.2 均经韩国机 curl 代理上传。
# 用法:
#  pwsh scripts/release-github-korea.ps1 -Version 0.3.2 -Sha 1e14c03…   # 建 tag + draft + 上传三件套 + publish
#  pwsh scripts/release-github-korea.ps1 -Version 0.3.2 -Sha … -NoPublish  # 保持 draft，人工网页发布
#  pwsh scripts/release-github-korea.ps1 -Version 0.3.2 -Sha … -CheckOnly  # 仅核对远端 Release/tag 状态
# 凭据: GitHub token 三级解析（-Token > env GH_TOKEN > git credential fill）；韩国密码同 upload-release-korea.ps1
param(
  [Parameter(Mandatory=$true)][string]$Version,
  [Parameter(Mandatory=$true)][string]$Sha,
  [string]$Owner = "xsk159357",
  [string]$Repo = "frameweave-desktop",
  [string]$Token = "",
  [string]$Server = "43.108.23.200",
  [switch]$NoPublish,
  [switch]$CheckOnly,
  [switch]$AllowLegacyFallback   # t3：显式允许读取 .tmp/kr_fix_upload.py 遗留明文口令（默认禁用）
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$relDir = Join-Path $root ("release/" + $Version)
foreach ($f in @("FrameWeave-Setup-$Version.exe", "FrameWeave-Setup-$Version.exe.blockmap", "latest.yml")) {
  if (-not (Test-Path (Join-Path $relDir $f))) { throw "缺三件套: $f（先跑 build-installer + check_release）" }
}
$tag = "v$Version"
Write-Host "[gh-kr] version=$Version tag=$tag sha=$Sha repo=$Owner/$Repo noPublish=$NoPublish checkOnly=$CheckOnly"

# --- GitHub token ---
if (-not $Token) { $Token = $env:GH_TOKEN }
if (-not $Token) {
  $cred = (echo "protocol=https`nhost=github.com`n" | git credential fill 2>$null)
  $tl = $cred | Select-String '^password=' | Select-Object -First 1
  if ($tl) { $Token = $tl.ToString().Substring(9).Trim() }
}
if (-not $Token) { throw "未找到 GitHub token：-Token / env GH_TOKEN / git credential" }

# --- 韩国服务器密码 ---
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
if (-not $pw -and $AllowLegacyFallback) {
  $hist = Join-Path (Join-Path $root ".tmp") "kr_fix_upload.py"
  if (Test-Path $hist) {
    $m = [regex]::Match((Get-Content $hist -Raw), "c\.connect\('([^']+)'[^)]*password='([^']*)'")
    if ($m.Success) { $Server = $m.Groups[1].Value; $pw = $m.Groups[2].Value; Write-Host "[gh-kr] ⚠ 已显式启用 -AllowLegacyFallback 读取 .tmp 遗留明文口令（高危：尽快迁移 SSH 密钥并轮换口令）" }
  }
}
if (-not $pw) { throw "未找到韩国服务器凭据：设 env FW_KOREA_PASSWORD（-AllowLegacyFallback 可临时启用 .tmp 明文回退，建议迁移 SSH 密钥 ~/.ssh/frameweave_auth 并轮换口令）" }

$py = @'
import sys, io, os, json, shlex
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import paramiko
host = os.environ["GHK_HOST"]; owner = os.environ["GHK_OWNER"]; repo = os.environ["GHK_REPO"]
ver = os.environ["GHK_VERSION"]; tag = os.environ["GHK_TAG"]; sha = os.environ["GHK_SHA"]
rel = os.environ["GHK_RELDIR"]; tok = os.environ["GHK_TOKEN"]; pw = os.environ.get("GHK_PASSWORD", "")
nopub = os.environ.get("GHK_NOPUB", "0") == "1"; check = os.environ.get("GHK_CHECK", "0") == "1"
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, port=22, username="root", password=pw, timeout=20)
def run(cmd, t=120):
    i, o, e = c.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "replace").strip() or e.read().decode("utf-8", "replace").strip() or "(none)"
# token 一次性写入远端（600 权限），所有 curl 在远端执行
run("umask 077; echo %s > /tmp/.fw_gh_token" % shlex.quote(tok))
def gh(method, path, body=None):
    if body is not None:
        b = shlex.quote(json.dumps(body))
        cmd = 'TOKEN=$(cat /tmp/.fw_gh_token); curl -s -m 60 -X %s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" -H "User-Agent: fw-release" -d %s https://api.github.com/repos/%s/%s/%s' % (method, b, owner, repo, path)
    else:
        cmd = 'TOKEN=$(cat /tmp/.fw_gh_token); curl -s -m 60 -X %s -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" -H "User-Agent: fw-release" https://api.github.com/repos/%s/%s/%s' % (method, owner, repo, path)
    return run(cmd, 90)
def gh_upload(rid, filepath, asset_name):
    cmd = ('TOKEN=$(cat /tmp/.fw_gh_token); curl -s -m 300 -X POST -H "Authorization: token $TOKEN" '
           '-H "User-Agent: fw-release" -H "Content-Type: application/octet-stream" --data-binary @%s "https://uploads.github.com/repos/%s/%s/releases/%s/assets?name=%s"'
           % (filepath, owner, repo, rid, asset_name))
    return run(cmd, 320)
def jparse(r):
    try: return json.loads(r)
    except Exception: return None

print("=== 1. 建 tag ref ===")
r = gh("POST", "git/refs", {"ref": "refs/tags/%s" % tag, "sha": sha})
d = jparse(r)
if d and d.get("ref"): print("tag OK: %s" % d["ref"])
elif d and d.get("errors"): print("tag errors: %s (若 422 ref 已存在则继续)" % str(d["errors"])[:160])
else: print("tag RAW: %s" % r[:160])

print("=== 2. 建/复用 release（draft）===")
rid = None
if check:
    # CheckOnly：只 GET 核对，不创建任何资源
    rl = gh("GET", "releases?per_page=100")
    dl = jparse(rl)
    if isinstance(dl, list):
        hit = [x for x in dl if x.get("tag_name") == tag]
        if hit:
            rid = hit[0]["id"]
            print("现有 release id=%s draft=%s html=%s" % (rid, hit[0].get("draft"), hit[0].get("html_url")))
        else:
            print("当前无 %s release（CheckOnly 不创建）" % tag)
    else:
        print("releases 列表解析失败: %s" % str(rl)[:160])
    print("CheckOnly：不执行上传/发布")
elif nopub:
    print("=== 2b. 建/复用 release（draft）===")
    r = gh("POST", "releases", {"tag_name": tag, "name": "FrameWeave v%s" % ver, "draft": True})
    d = jparse(r)
    if d and d.get("id"):
        rid = d["id"]; print("release id=%s" % rid)
    else:
        rl = gh("GET", "releases?per_page=100")
        dl = jparse(rl)
        if isinstance(dl, list):
            hit = [x for x in dl if x.get("tag_name") == tag]
            if hit:
                rid = hit[0]["id"]; print("复用 release id=%s (draft=%s)" % (rid, hit[0].get("draft")))
        if not rid:
            print("release RAW: %s" % r[:200]); sys.exit(1)
    print("=== 3. 上传 assets（draft）===")
    assets = ["FrameWeave-Setup-%s.exe" % ver, "FrameWeave-Setup-%s.exe.blockmap" % ver, "latest.yml"]
    sftp = c.open_sftp()
    for name in assets:
        local = os.path.join(rel, name); remote = "/tmp/relkr_" + name
        sftp.put(local, remote); print("scp OK %s (%d B)" % (name, os.path.getsize(local)))
    sftp.close()
    for name in assets:
        r = gh_upload(rid, "/tmp/relkr_" + name, name)
        d = jparse(r)
        print("  %s: %s" % (name, ("uploaded id=%s size=%s" % (d["id"], d.get("size"))) if (d and d.get("id")) else ("RAW %s" % r[:180])))
    print("=== -NoPublish：保持 draft，请到网页发布 ===")
    print("  https://github.com/%s/%s/releases" % (owner, repo))
else:
    print("=== 2b. 建/复用 release（draft）===")
    r = gh("POST", "releases", {"tag_name": tag, "name": "FrameWeave v%s" % ver, "draft": True})
    d = jparse(r)
    if d and d.get("id"):
        rid = d["id"]; print("release id=%s" % rid)
    else:
        rl = gh("GET", "releases?per_page=100")
        dl = jparse(rl)
        if isinstance(dl, list):
            hit = [x for x in dl if x.get("tag_name") == tag]
            if hit:
                rid = hit[0]["id"]; print("复用 release id=%s (draft=%s)" % (rid, hit[0].get("draft")))
        if not rid:
            print("release RAW: %s" % r[:200]); sys.exit(1)
    print("=== 3. 上传 assets ===")
    assets = ["FrameWeave-Setup-%s.exe" % ver, "FrameWeave-Setup-%s.exe.blockmap" % ver, "latest.yml"]
    sftp = c.open_sftp()
    for name in assets:
        local = os.path.join(rel, name); remote = "/tmp/relkr_" + name
        sftp.put(local, remote); print("scp OK %s (%d B)" % (name, os.path.getsize(local)))
    sftp.close()
    for name in assets:
        r = gh_upload(rid, "/tmp/relkr_" + name, name)
        d = jparse(r)
        print("  %s: %s" % (name, ("uploaded id=%s size=%s" % (d["id"], d.get("size"))) if (d and d.get("id")) else ("RAW %s" % r[:180])))
    print("=== 4. publish ===")
    r = gh("PATCH", "releases/%s" % rid, {"draft": False})
    d = jparse(r)
    print("published_at=%s html=%s" % ((d or {}).get("published_at"), (d or {}).get("html_url")))
run("rm -f /tmp/.fw_gh_token /tmp/relkr_*")
c.close(); print("DONE")
'@
$env:GHK_HOST = $Server; $env:GHK_OWNER = $Owner; $env:GHK_REPO = $Repo; $env:GHK_VERSION = $Version
$env:GHK_TAG = $tag; $env:GHK_SHA = $Sha; $env:GHK_RELDIR = $relDir; $env:GHK_TOKEN = $Token
$env:GHK_PASSWORD = $pw; $env:GHK_NOPUB = $(if ($NoPublish) { "1" } else { "0" }); $env:GHK_CHECK = $(if ($CheckOnly) { "1" } else { "0" })
try { $py | python - } finally { Remove-Item Env:GHK_* -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) { throw "gh-kr python runner exited $LASTEXITCODE" }
Write-Host "[gh-kr] done: $tag"