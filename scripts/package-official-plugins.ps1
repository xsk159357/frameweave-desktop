param(
  [string]$ReleaseBase = "https://github.com/xsk159357/frameweave-plugins/releases/download",
  [string]$TagPrefix = "core",
  [string]$OutputDir = ""
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutputDir) { $OutputDir = Join-Path $root '.tmp/official-plugin-release' }
$nodesDir = Join-Path $root 'backend/user_nodes'
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$rows = @()
Get-ChildItem $nodesDir -Directory -Filter 'core_*' | Sort-Object Name | ForEach-Object {
  $manifestPath = Join-Path $_.FullName 'manifest.json'
  if (-not (Test-Path $manifestPath)) { throw "Missing manifest: $manifestPath" }
  $m = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($m.runtime -ne 'python') { throw "Unsupported runtime $($m.runtime): $($m.type_id)" }
  $slug = $m.type_id.Replace('/', '-')
  $filename = "$slug-$($m.version).zip"
  $zip = Join-Path $OutputDir $filename
  if (Test-Path $zip) { Remove-Item $zip -Force }
  # 保留插件包顶层目录，匹配 install_zip 要求：<package>/manifest.json
  $stage = Join-Path $OutputDir (".stage-" + $slug)
  if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $stage | Out-Null
  $pkgDir = Join-Path $stage $slug
  Copy-Item $_.FullName $pkgDir -Recurse -Force
  Compress-Archive -Path $pkgDir -DestinationPath $zip -CompressionLevel Optimal
  Remove-Item $stage -Recurse -Force
  $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
  $size = (Get-Item $zip).Length
  $tag = "$TagPrefix-$slug-v$($m.version)"
  $rows += [pscustomobject]@{
    id = $slug; kind = 'node'; title = $m.title; description = $m.description
    author = 'official'; price = 0; official = $true; version = $m.version
    type_id = $m.type_id; sha256 = $hash; size = $size
    min_client_version = '0.2.12'; status = 'active'
    download_url = "$ReleaseBase/$tag/$filename"
  }
  Write-Output ("{0} {1} {2} bytes sha256={3}" -f $m.type_id,$filename,$size,$hash)
}
$catalog = Join-Path $OutputDir 'official-market-items.json'
$rows | ConvertTo-Json -Depth 8 | Set-Content $catalog -Encoding UTF8
Write-Output "CATALOG=$catalog"
