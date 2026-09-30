# Local release feed server (simulate electron-updater remote fetch)
param([int]$Port = 8790)
$root = Split-Path -Parent $PSScriptRoot
$release = Join-Path $root 'release'
if (-not (Test-Path $release)) { throw 'release dir missing, run publish.ps1 first' }
Write-Host "[serve] feed: http://127.0.0.1:$Port/ (dir: $release)"
Write-Host "[serve] set FRAMEWEAVE_UPDATE_URL=http://127.0.0.1:$Port/<version> in client"
python -m http.server $Port --directory $release
