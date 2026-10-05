# publish-github.ps1 — 上传 release 三件套到 GitHub Releases（electron-updater github provider）
# 用法:
#   pwsh scripts/publish-github.ps1 -Owner <gh用户名> -Repo <仓库名> -Token <GH_TOKEN>
#   或设置环境变量 GITHUB_OWNER / GITHUB_REPO / GH_TOKEN 后直接运行
# 依赖: 先跑 scripts/publish.ps1 产出 release/<version>/ 三件套
param(
  [string]$Owner = "",
  [string]$Repo = "",
  [string]$Token = "",
  [string]$Version = "",
  [string]$ReleaseDir = ""
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if (-not $Owner) { $Owner = $env:GITHUB_OWNER }
if (-not $Repo)  { $Repo  = $env:GITHUB_REPO }
if (-not $Token) { $Token = $env:GH_TOKEN }
if (-not $Owner -or -not $Repo -or -not $Token) {
  throw "需要 GITHUB_OWNER / GITHUB_REPO / GH_TOKEN（参数或环境变量）"
}
if (-not $Version) {
  $pkg = Get-Content (Join-Path $root 'desktop/package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  $Version = $pkg.version
}
if (-not $ReleaseDir) { $ReleaseDir = Join-Path $root ("release/" + $Version) }
if (-not (Test-Path $ReleaseDir)) { throw "release 目录不存在: $ReleaseDir（请先运行 scripts/publish.ps1）" }

$exe  = Get-ChildItem $ReleaseDir -Filter "FrameWeave-Setup-*.exe" | Select-Object -First 1
$bmap = Get-ChildItem $ReleaseDir -Filter "*.blockmap" | Select-Object -First 1
$ly   = Join-Path $ReleaseDir "latest.yml"
if (-not $exe -or -not (Test-Path $ly)) { throw "release 目录缺三件套（exe + blockmap + latest.yml）" }

$headers = @{ Authorization = "Bearer $Token"; Accept = "application/vnd.github+json" }
$api = "https://api.github.com"
$tag = "v$Version"

# 1. 查找或创建 Release（同名 tag 复用；新标签默认 draft，网页确认后正式发布）
$rel = $null
try { $rel = Invoke-RestMethod -Uri "$api/repos/$Owner/$Repo/releases/tags/$tag" -Headers $headers -Method Get } catch { }
if (-not $rel) {
  $body = @{ tag_name = $tag; name = "FrameWeave v$Version"; draft = $true; generate_release_notes = $false } | ConvertTo-Json
  $rel = Invoke-RestMethod -Uri "$api/repos/$Owner/$Repo/releases" -Headers $headers -Method Post -Body $body
  Write-Host "[gh] 创建 Release $tag (draft)"
} else {
  Write-Host "[gh] 复用 Release $tag"
}

# 2. 上传/覆盖资产（同名先删除旧资产，避免 422）
function Upload-Asset([object]$Rel, [System.IO.FileInfo]$File, [string]$Name) {
  $existing = $Rel.assets | Where-Object { $_.name -eq $Name }
  if ($existing) {
    Invoke-RestMethod -Uri "$api/repos/$Owner/$Repo/releases/assets/$($existing.id)" -Headers $headers -Method Delete
    Write-Host "[gh] 删除旧资产 $Name"
  }
  $up = $Rel.upload_url -replace '\{\?name,label\}', ("?name=" + [uri]::EscapeDataString($Name))
  Invoke-RestMethod -Uri $up -Headers @{ Authorization = "Bearer $Token"; "Content-Type" = "application/octet-stream" } -Method Post -InFile $File.FullName | Out-Null
  Write-Host "[gh] 上传 $Name ($([math]::Round($File.Length/1MB,1)) MB)"
}
Upload-Asset $rel $exe $exe.Name
if ($bmap) { Upload-Asset $rel $bmap $bmap.Name }
Upload-Asset $rel (Get-Item $ly) "latest.yml"

Write-Host ""
Write-Host "[gh] 完成: https://github.com/$Owner/$Repo/releases/tag/$tag"
Write-Host "[gh] 注意: 新 Release 为 draft，请在网页上点击 Publish 后用户端才可见"
