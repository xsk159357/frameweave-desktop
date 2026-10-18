# build-backend.ps1 — 后端打包（pyarmor 混淆 + PyInstaller + 完整性清单）
# 用法:
#   pwsh scripts/build-backend.ps1            # 增量：后端源码变更才重建，否则复用产物
#   pwsh scripts/build-backend.ps1 -Full      # 强制全量重建（混淆+PyInstaller+清单）
#   pwsh scripts/build-backend.ps1 -Skip      # 跳过混淆/打包，仅重生成 integrity.manifest
param(
  [switch]$Full,    # 强制全量重建
  [switch]$Skip     # 跳过混淆与 PyInstaller，仅重新生成完整性清单
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'backend/.venv/Scripts/python.exe'
$pyarmor = Join-Path $root 'backend/.venv/Scripts/pyarmor.exe'
$spec = Join-Path $root 'backend/packaging/frameweave-backend.spec'
$outDir = Join-Path $root 'backend/packaging/dist/backend-dist'
$exe = Join-Path $outDir 'frameweave-backend.exe'
if (-not (Test-Path $python)) { throw "Missing backend Python: $python" }
if (-not (Test-Path $spec)) { throw "Missing PyInstaller spec: $spec" }
$started = Get-Date

# ---- 增量检测：后端源码（app/ + main.py + spec + 依赖 lock）最近修改 vs 现有 exe ----
function Get-NewestBackendSource {
  $rootDir = (Join-Path $root 'backend')
  $paths = @(
    (Join-Path $rootDir 'app'),
    (Join-Path $rootDir 'main.py'),
    (Join-Path $rootDir 'requirements.txt'),
    (Join-Path $rootDir 'packaging\frameweave-backend.spec'),
    (Join-Path $rootDir 'packaging\build_seed.py'),
    (Join-Path $rootDir 'user_nodes')
  )
  $latest = Get-Date '1970-01-01'
  foreach ($p in $paths) {
    if (Test-Path $p) {
      $files = if ((Get-Item $p).PSIsContainer) { Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '__pycache__|\.pyc$|\.work|node_modules|\.venv' } } else { @(Get-Item $p) }
      foreach ($f in $files) { if ($f.LastWriteTime -gt $latest) { $latest = $f.LastWriteTime } }
    }
  }
  return $latest
}

$needRebuild = $Full
if (-not $Skip -and -not $needRebuild) {
  if (Test-Path $exe) {
    $srcLatest = Get-NewestBackendSource
    $exeTime = (Get-Item $exe).LastWriteTime
    $needRebuild = ($srcLatest -gt $exeTime)
    if (-not $needRebuild) { Write-Host "[backend] 增量：后端源码无变更（最新 $srcLatest <= exe $exeTime），复用产物" }
  } else {
    $needRebuild = $true   # 无产物 → 必须全量
  }
}

# ---- 混淆 + PyInstaller（仅需要重建时执行） ----
if ($Skip) {
  Write-Host '[backend] -Skip: 跳过混淆与打包，仅重新生成完整性清单'
} elseif ($needRebuild) {
  Write-Host '[backend] 重新生成 PyArmor 混淆树 (.obf_main) + PyInstaller'
  Push-Location (Join-Path $root 'backend')
  try {
    $obfDir = Join-Path $root 'backend/.obf'
    $obfMain = Join-Path $root 'backend/.obf_main'
    Remove-Item -Recurse -Force $obfDir, $obfMain -ErrorAction SilentlyContinue
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $pyarmor gen -r --mix-str --exclude "*.work.*" --exclude "__pycache__" -O $obfDir app 2>&1 | Out-Host
    $c = $LASTEXITCODE
    if ($c -ne 0) { throw "pyarmor gen app 失败 (exit=$c)" }
    Copy-Item -Recurse $obfDir $obfMain
    & $pyarmor gen --mix-str -O $obfMain main.py 2>&1 | Out-Host
    $c = $LASTEXITCODE
    if ($c -ne 0) { throw "pyarmor gen main 失败 (exit=$c)" }
    $ErrorActionPreference = $saved
  } finally { Pop-Location }

  # t6（M7 供应链缺口根治）：构建内置基础节点种子树（build_seed.py，PyInstaller
  # spec 的 datas 引用 packaging/build/builtin_basics → 产物 _internal/builtin_basics/）。
  # 种子树逐包官方锚验签，任一包验签失败即中止（不绕过签名）。
  Write-Host '[backend] t6: 生成内置基础节点种子（build_seed.py）'
  Push-Location (Join-Path $root 'backend')
  try {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $python packaging/build_seed.py 2>&1 | Out-Host
    $c = $LASTEXITCODE
    $ErrorActionPreference = $saved
    if ($c -ne 0) { throw "build_seed.py 失败 (exit=$c)" }
  } finally { Pop-Location }

  Push-Location (Join-Path $root 'backend/packaging')
  try {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $python -m PyInstaller --noconfirm $spec 2>&1 | Out-Host
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
} else {
  Write-Host '[backend] 增量：复用既有产物（未强制 Full，无源码变更）'
}

# P3/t3 ②：打包完成后生成签名完整性清单（integrity.manifest，随安装包分发）
# 注意：完整性清单必须在混淆后/每次构建后重新生成（对应当前 dist 产物哈希）
Write-Host '[backend] P3: 生成完整性清单 integrity.manifest'
Push-Location (Join-Path $root 'backend')
try {
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $python packaging/build_manifest.py 2>&1 | Out-Host
  $code = $LASTEXITCODE
  $ErrorActionPreference = $saved
  if ($code -ne 0) { throw 'build_manifest.py 失败' }
} finally { Pop-Location }
$mf = Join-Path $outDir 'integrity.manifest'
if (-not (Test-Path $mf)) { throw "完整性清单未生成: $mf" }
Write-Host "[backend] ✅ 完整性清单: $mf ($((Get-Item $mf).Length) bytes)"