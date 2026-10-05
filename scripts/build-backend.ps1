$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'backend/.venv/Scripts/python.exe'
$spec = Join-Path $root 'backend/packaging/frameweave-backend.spec'
$outDir = Join-Path $root 'backend/packaging/dist/backend-dist'
$exe = Join-Path $outDir 'frameweave-backend.exe'
if (-not (Test-Path $python)) { throw "Missing backend Python: $python" }
if (-not (Test-Path $spec)) { throw "Missing PyInstaller spec: $spec" }
$started = Get-Date
Write-Host '[backend] rebuilding PyInstaller backend'
Push-Location (Join-Path $root 'backend/packaging')
try {
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $python -m PyInstaller --clean --noconfirm $spec 2>&1 | Out-Host
  $code = $LASTEXITCODE
  $ErrorActionPreference = $saved
  if ($code -ne 0) { throw 'PyInstaller backend build failed' }
} finally { Pop-Location }
if (-not (Test-Path $exe)) { throw "Backend EXE not generated: $exe" }
$item = Get-Item $exe
if ($item.Length -lt 10MB) { throw "Backend EXE is unexpectedly small: $($item.Length) bytes" }
if ($item.LastWriteTime -lt $started.AddSeconds(-2)) { throw 'Backend EXE was not updated by this build' }
$mb = [math]::Round($item.Length / 1MB, 1)
Write-Host ('[backend] OK {0} ({1} MB)' -f $exe, $mb)
