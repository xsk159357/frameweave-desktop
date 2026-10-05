# FrameWeave release publishing script
# Usage:
#   pwsh scripts/publish.ps1                # pack + sign + assemble release
#   pwsh scripts/publish.ps1 -SkipPack      # reuse existing dist artifacts
#   pwsh scripts/publish.ps1 -SignOff       # pack without signing
param(
  [switch]$SkipPack,
  [switch]$SignOff
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$desktop = Join-Path $root 'desktop'
$dist = Join-Path $desktop 'dist'
$release = Join-Path $root 'release'

$pkg = Get-Content (Join-Path $desktop 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $pkg.version
Write-Host "[publish] version: $ver"

if (-not $SkipPack) {
  Write-Host "[publish] running electron-builder ..."
  Push-Location $desktop
  try { npx electron-builder --win nsis } finally { Pop-Location }
}
$installer = Join-Path $dist ("FrameWeave-Setup-" + $ver + ".exe")
if (-not (Test-Path $installer)) { throw "installer not found: $installer" }

$pfx = $env:FRAMEWEAVE_PFX
if (-not $pfx) { $pfx = Join-Path $root 'certs/frameweave-dev.pfx' }
$pfxPwd = $env:FRAMEWEAVE_PFX_PASSWORD
if (-not $pfxPwd) { $pfxPwd = 'FrameWeaveDev2026' }
$signtool = $env:FRAMEWEAVE_SIGNTOOL
if (-not $signtool) { $signtool = 'C:/Program Files (x86)/Windows Kits/10/bin/10.0.26100.0/x64/signtool.exe' }

Write-Host "[diag] FRAMEWEAVE_PFX=$env:FRAMEWEAVE_PFX"
Write-Host "[diag] FRAMEWEAVE_SIGNTOOL=$env:FRAMEWEAVE_SIGNTOOL"
Write-Host "[diag] FRAMEWEAVE_PFX_PASSWORD=$env:FRAMEWEAVE_PFX_PASSWORD"
Write-Host "[diag] signtool path: $signtool"
Write-Host "[diag] pfx path: $pfx (exists=$(Test-Path $pfx))"
Write-Host "[diag] pwd len: $($pfxPwd.Length)"
if (-not $SignOff) {
  if (Test-Path $pfx) {
    Write-Host "[publish] signing: $installer"
    $mainExe = Join-Path $dist 'win-unpacked/FrameWeave.exe'
    if (Test-Path $mainExe) {
      & $signtool sign /f $pfx /p $pfxPwd /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $mainExe
      if ($LASTEXITCODE -ne 0) { throw 'main exe signing failed' }
      Write-Host "  main exe signed"
    }
    & $signtool sign /f $pfx /p $pfxPwd /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $installer
    if ($LASTEXITCODE -ne 0) { throw 'installer signing failed' }
    Write-Host "  installer signed"
  } else {
    Write-Host "[publish] WARN: cert not found $pfx, skip signing"
  }
} else {
  Write-Host "[publish] signing skipped (-SignOff)"
}

$relDir = Join-Path $release $ver
New-Item -ItemType Directory -Force -Path $relDir | Out-Null
Copy-Item $installer (Join-Path $relDir 'FrameWeave-Setup.exe') -Force
$bmName = 'FrameWeave-Setup-' + $ver + '.exe.blockmap'
$bm = Join-Path $dist $bmName
if (Test-Path $bm) {
  Copy-Item $bm (Join-Path $relDir $bmName) -Force
  Write-Host "  blockmap: $bmName"
}
$ly = Join-Path $dist 'latest.yml'
if (Test-Path $ly) {
  $y = Get-Content $ly -Raw
  $y = $y -replace 'FrameWeave-Setup-d+.d+.d+.exe', 'FrameWeave-Setup.exe'
  Set-Content (Join-Path $relDir 'latest.yml') $y -Encoding UTF8
}
Write-Host "[publish] release dir: $relDir"
Get-ChildItem $relDir | ForEach-Object { Write-Host ("  " + $_.Name + " " + [math]::Round($_.Length/1MB, 1) + " MB") }
Write-Host "[publish] done. upload release/$ver to production update feed."
