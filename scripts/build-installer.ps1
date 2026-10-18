# build-installer.ps1 — 打包安装包（绕过 electron-builder app-builder 被杀的环境限制）
# 流程: [build-backend] → [vite build] → electron-builder --dir (asar:true) → 手写 NSIS 脚本 → makensis 编译
# 用法:
#   pwsh scripts/build-installer.ps1            # 增量：后端/前端有变更才重建对应部分；都无变更则只重打安装包
#   pwsh scripts/build-installer.ps1 -Full      # 全量重建（后端+前端+electron+NSIS）
#   pwsh scripts/build-installer.ps1 -SkipBackend   # 跳过后端打包（后端产物直接复用，仅清单重估）
#   pwsh scripts/build-installer.ps1 -SkipWeb       # 跳过 vite build（web/dist 直接复用）
param(
  [switch]$Full,          # 强制全量
  [switch]$SkipBackend,   # 跳过后端打包步骤
  [switch]$SkipWeb        # 跳过前端 vite build
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pkg = Get-Content (Join-Path $root 'desktop/package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $pkg.version
$mk = "C:/Users/Administrator/AppData/Local/electron-builder/Cache/nsis/nsis-3.0.4.1/makensis.exe"
# P2: 编译入口统一走 templates 使用的 Bin\makensis.exe（若存在优先，避免双重 NSIS 实例差异）
if (Test-Path "C:/Users/Administrator/AppData/Local/electron-builder/Cache/nsis/nsis-3.0.4.1/Bin/makensis.exe") {
  $mk = "C:/Users/Administrator/AppData/Local/electron-builder/Cache/nsis/nsis-3.0.4.1/Bin/makensis.exe"
}
$build = Join-Path $root '.tmp\installer-build'
$unpacked = Join-Path $build "win-unpacked"
$out = Join-Path $build ("FrameWeave-Setup-" + $ver + ".exe")
Write-Host "[build] 版本 $ver (Full=$Full SkipBackend=$SkipBackend SkipWeb=$SkipWeb)"

# ---- 前端变更检测：web/src 最近修改 vs web/dist 产物 ----
function Get-NewestWebSource {
  $web = Join-Path $root 'web'
  $latest = Get-Date '1970-01-01'
  foreach ($p in @((Join-Path $web 'src'), (Join-Path $web 'vite.config.ts'), (Join-Path $web 'index.html'), (Join-Path $web 'package.json'))) {
    if (Test-Path $p) {
      $files = if ((Get-Item $p).PSIsContainer) { Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch 'node_modules|\.work' } } else { @(Get-Item $p) }
      foreach ($f in $files) { if ($f.LastWriteTime -gt $latest) { $latest = $f.LastWriteTime } }
    }
  }
  return $latest
}
$distHtml = Join-Path $root 'web\dist\index.html'
$webChanged = $Full
if (-not $SkipWeb -and -not $webChanged) {
  if (Test-Path $distHtml) {
    $srcLatest = Get-NewestWebSource
    $distTime = (Get-Item $distHtml).LastWriteTime
    $webChanged = ($srcLatest -gt $distTime)
    if (-not $webChanged) { Write-Host "[build] 增量：前端 src 无变更（最新 $srcLatest <= dist $distTime），跳过 vite build" }
  } else { $webChanged = $true }
}

# 0) 后端（存在 SkipBackend 时跳过；否则交由 build-backend.ps1 自行增量判定）
if ($SkipBackend) {
  Write-Host "[build] -SkipBackend: 跳过后端打包（复用现有 backend-dist）"
} else {
  Write-Host "[build] 0/4 build-backend.ps1（内部增量判定）"
  $backendArgs = @()
  if ($Full) { $backendArgs += '-Full' }
  & (Join-Path $PSScriptRoot "build-backend.ps1") @backendArgs
  if ($LASTEXITCODE -ne 0) { throw "后端构建失败，停止安装包构建" }
}

# 1) 前端构建（有变更才跑）
if ($webChanged) {
  Write-Host "[build] 1/4 vite build"
  Push-Location (Join-Path $root 'web')
  try { $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'; & npm.cmd run build 2>&1 | Out-Null; $c = $LASTEXITCODE; $ErrorActionPreference = $eap; if ($c -ne 0) { throw "vite build 失败" } } finally { Pop-Location }
} else {
  Write-Host "[build] 1/4 vite build 跳过（web/dist 为最新，复用）"
}

# 1.5) 构建前端/后端契约检查（始终执行，防遗漏）
$required = @('/api/auth/email/send-code','/api/auth/register','/api/auth/password/reset','/api/auth/login','/api/system/build-info')
$server = Join-Path $root 'backend/app/server.py'
$serverText = Get-Content $server -Raw -Encoding UTF8
foreach ($route in $required) { if ($serverText -notmatch [regex]::Escape($route)) { throw "缺少后端认证路由: $route" } }
Write-Host '[build] auth contract OK'

# 2) electron-builder --dir (asar:true 打包加固 + 完整性清单)
# 增量优化：web 变更或后端变更或首次构建 → 全量 electron-builder --dir；
# 否则复用既有 win-unpacked（仅重嵌 integrity.json + app-update.yml），跳过 electron-builder 全量重打包。
$backendExe = Join-Path $root 'backend\packaging\dist\backend-dist\frameweave-backend.exe'
$needElectron = $Full -or $webChanged
if (-not $needElectron) {
  # 后端 exe 比 win-unpacked 内后端新 → 需重打包 electron（backend-dist 会进 asar）
  $beTime = if (Test-Path $backendExe) { (Get-Item $backendExe).LastWriteTime } else { Get-Date '1970-01-01' }
  $unpackedExe = Join-Path $unpacked 'resources\backend-dist\frameweave-backend.exe'
  $upTime = if (Test-Path $unpackedExe) { (Get-Item $unpackedExe).LastWriteTime } else { Get-Date '1970-01-01' }
  if ($beTime -gt $upTime) { $needElectron = $true }
}
if ($needElectron) {
  Write-Host "[build] 2/4 electron-builder --dir (asar:true + integrity)"
  if (Test-Path $build) { Remove-Item $build -Recurse -Force }
  Push-Location (Join-Path $root 'desktop')
  try {
    $eap2 = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & node (Join-Path $root 'desktop/scripts/embed-integrity.cjs') 2>&1 | Out-Host   # P2: 生成 integrity.json（web/dist 已由步骤 1 构建）
    if ($LASTEXITCODE -ne 0) { throw "integrity 清单生成失败" }
    & npx.cmd electron-builder --dir --config.directories.output="$build" 2>&1 | Out-Null; $c2 = $LASTEXITCODE; $ErrorActionPreference = $eap2
    if ($c2 -ne 0) { throw "electron-builder --dir 失败" }
  } finally { Pop-Location }
  if (-not (Test-Path (Join-Path $unpacked "FrameWeave.exe"))) { throw "win-unpacked 未生成" }
} else {
  Write-Host "[build] 2/4 electron-builder --dir 跳过（web/后端均无变更，复用 win-unpacked）"
  if (-not (Test-Path (Join-Path $unpacked "FrameWeave.exe"))) { throw "无既有 win-unpacked，请先执行 -Full 或全量构建" }
  # 复用路径：web/dist 与后端均未变 → asar 内 integrity.json 哈希仍有效，无需重嵌。
  # （embed-integrity.cjs 只在 electron-builder 重打包时运行，与 asar 内清单保持一致）
}

# electron-builder 的 --dir + 手写 NSIS 路径不会自动生成 app-update.yml。
# electron-updater 在 GitHub provider 模式下启动时从 resources/app-update.yml 读取更新源。
$resourcesDir = Join-Path $unpacked "resources"
$appUpdateYml = Join-Path $resourcesDir "app-update.yml"
$appUpdate = @(
  'provider: github'
  'owner: xsk159357'
  'repo: frameweave-desktop'
  'releaseType: release'
) -join "`r`n"
Set-Content -Path $appUpdateYml -Value $appUpdate -Encoding UTF8
if (-not (Test-Path $appUpdateYml)) { throw "app-update.yml 未生成" }
Write-Host "[build] app-update.yml 已写入: $appUpdateYml"

# 3) 生成 NSIS 脚本（模板注入版本/路径/品牌资产）
Write-Host "[build] 3/4 生成 NSIS 脚本"
$tpl = Get-Content (Join-Path $PSScriptRoot "nsis\frameweave.nsi.tpl") -Raw -Encoding UTF8
$buildDir = (Join-Path $root 'desktop\build')
# 品牌资产路径注入（generate-installer-assets.py 产出；@TOKEN@ 占位符避免 PowerShell ${} 插值歧义）
$tpl = $tpl.Replace('$FW_VER', $ver)
$tpl = $tpl.Replace('$FW_OUT', $out.Replace("/", "\"))
$tpl = $tpl.Replace('$FW_UNPACKED', $unpacked.Replace("/", "\"))
$tpl = $tpl.Replace('@FW_ICON@', (Join-Path $buildDir 'installerIcon.ico'))
$tpl = $tpl.Replace('@FW_UNICON@', (Join-Path $buildDir 'uninstallerIcon.ico'))
$tpl = $tpl.Replace('@FW_HEADER_BMP@', (Join-Path $buildDir 'installerHeader.bmp'))
$tpl = $tpl.Replace('@FW_SIDEBAR_BMP@', (Join-Path $buildDir 'installerSidebar.bmp'))
$nsiFile = Join-Path $build "frameweave.nsi"
Set-Content -Path $nsiFile -Value $tpl -Encoding UTF8

# 4) makensis 编译
Write-Host "[build] 4/4 makensis 编译（压缩大文件，需几分钟）"
Push-Location $build
try {
  # NativeCommandError 防护：makensis 压缩大文件时 stderr 有正常输出，
  # EAP=Stop 下会被包装成终止异常（假失败 exit=1）。与其他段一致：
  # 临时降为 Continue，以 $LASTEXITCODE 为准判断真实成败。
  $eap3 = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $mk /V2 $nsiFile 2>&1 | Out-Host
  $c3 = $LASTEXITCODE
  $ErrorActionPreference = $eap3
  if ($c3 -ne 0) { throw "makensis 失败 (exit=$c3)" }
} finally { Pop-Location }
if (-not (Test-Path $out)) { throw "安装包未生成" }
Write-Host "[build] ✅ 安装包: $out ($([math]::Round((Get-Item $out).Length/1MB,1)) MB)"