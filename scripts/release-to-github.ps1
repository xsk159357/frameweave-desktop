# release-to-github.ps1 — 一键发布：打包出片 → 上传 GitHub Release → 正式发布
# 用法:
#   pwsh scripts/release-to-github.ps1                        # 默认仓库 xsk159357/frameweave-desktop
#   pwsh scripts/release-to-github.ps1 -Owner xx -Repo yy -Token ghp_xxx
#   pwsh scripts/release-to-github.ps1 -SkipPack              # 跳过打包，只上传+发布现有产物
#   pwsh scripts/release-to-github.ps1 -SignOff               # 出包时跳过代码签名（无证书阶段）
#   pwsh scripts/release-to-github.ps1 -NoPublish             # 只上传为 draft，人工核对后再网页发布
# Token 解析优先级: -Token 参数 > GH_TOKEN 环境变量 > 本机 Windows 凭据管理器缓存的 GitHub token
param(
  [string]$Owner = "",
  [string]$Repo = "",
  [string]$Token = "",
  [switch]$SkipPack,
  [switch]$SignOff,
  [switch]$NoPublish
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# ---- 仓库与版本 ----
if (-not $Owner) { $Owner = $env:GITHUB_OWNER }
if (-not $Repo)  { $Repo  = $env:GITHUB_REPO }
if (-not $Owner) { $Owner = 'xsk159357' }
if (-not $Repo)  { $Repo  = 'frameweave-desktop' }
$pkg = Get-Content (Join-Path $root 'desktop/package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $pkg.version
$tag = "v$ver"
Write-Host "[release] 版本=$ver 仓库=$Owner/$Repo"

# ---- Token 三级解析 ----
if (-not $Token) { $Token = $env:GH_TOKEN }
if (-not $Token) {
  try {
    $cred = (echo "protocol=https`nhost=github.com`n" | git credential fill)
    $tokLine = ($cred | Select-String '^password=' | Select-Object -First 1).ToString()
    $Token = $tokLine.Substring(9).Trim()
    if ($Token) { Write-Host "[release] 使用本机 git 凭据缓存 token（前4位 $($Token.Substring(0,4))...）" }
  } catch { }
}
if (-not $Token) { throw "未找到 GitHub token：请用 -Token 参数或设置 GH_TOKEN 环境变量" }
$env:GH_TOKEN = $Token

# ---- 1) 出包 ----
if (-not $SkipPack) {
  $packArgs = @()
  if ($SignOff) { $packArgs += '-SignOff' }
  Write-Host "[release] 步骤1/3：打包（publish.ps1）..."
  & (Join-Path $PSScriptRoot 'publish.ps1') @packArgs
  if ($LASTEXITCODE -ne 0) { throw "打包失败 (exit $LASTEXITCODE)" }
} else {
  Write-Host "[release] 步骤1/3：-SkipPack 跳过打包，使用现有 release/$ver"
}

# 校验三件套
$rd = Join-Path $root ("release/" + $ver)
$exe  = Get-ChildItem $rd -Filter "FrameWeave-Setup-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
$ly   = Join-Path $rd "latest.yml"
if (-not $exe -or -not (Test-Path $ly)) { throw "release/$ver 缺三件套（先出包或去 -SkipPack）" }

# ---- 2) 上传（draft） ----
Write-Host "[release] 步骤2/3：上传到 GitHub Release $tag ..."
& (Join-Path $PSScriptRoot 'publish-github.ps1') -Owner $Owner -Repo $Repo
if ($LASTEXITCODE -ne 0) { throw "上传失败 (exit $LASTEXITCODE)" }

# ---- 3) 发布（draft -> published） ----
if ($NoPublish) {
  Write-Host "[release] 步骤3/3：-NoPublish，保持 draft。请到网页发布:"
  Write-Host "  https://github.com/$Owner/$Repo/releases"
  exit 0
}
Write-Host "[release] 步骤3/3：正式发布 Release ..."
$api = 'https://api.github.com'
$h = @{ Authorization = "Bearer $Token"; Accept = 'application/vnd.github+json'; 'Content-Type' = 'application/json' }
# 按 tag 找 release；draft 刚创建时 tags GET 可能有一致性延迟，改用 releases 列表按 tag_name 匹配
$rels = Invoke-RestMethod -Uri "$api/repos/$Owner/$Repo/releases?per_page=100" -Headers $h -Method Get
$rel = $rels | Where-Object { $_.tag_name -eq $tag } | Select-Object -First 1
if (-not $rel) { throw "未找到 release $tag（上传可能未完成）" }
$pub = Invoke-RestMethod -Uri "$api/repos/$Owner/$Repo/releases/$($rel.id)" -Headers $h -Method Patch -Body (@{ draft = $false } | ConvertTo-Json)
Write-Host "[release] ✅ 发布完成: $($pub.html_url)"
Write-Host "[release]   下载URL : https://github.com/$Owner/$Repo/releases/download/$tag/$($pub.assets[0].name)"
Write-Host "[release]   注意    : 旧版本客户端（内置旧更新逻辑）不会自动收到本版本，需手动分发一次；安装后后续版本自动更新"
