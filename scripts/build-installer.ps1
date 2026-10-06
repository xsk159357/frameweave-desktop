# build-installer.ps1 — 打包安装包（绕过 electron-builder app-builder 被杀的环境限制）
# 流程: vite build → electron-builder --dir (asar:false, 仅 win-unpacked) → 手写 NSIS 脚本 → makensis 编译
# 用法: pwsh scripts/build-installer.ps1   （产物 .tmp-build/FrameWeave-Setup-<ver>.exe）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pkg = Get-Content (Join-Path $root 'desktop/package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = $pkg.version
$mk = "C:/Users/Administrator/AppData/Local/electron-builder/Cache/nsis/nsis-3.0.4.1/makensis.exe"
$build = Join-Path $root '.tmp\installer-build'
$unpacked = Join-Path $build "win-unpacked"
$out = Join-Path $build ("FrameWeave-Setup-" + $ver + ".exe")
Write-Host "[build] 版本 $ver"

# 0) 强制重建后端，禁止复用旧 backend-dist
Write-Host "[build] 0/4 PyInstaller 后端重建"
& (Join-Path $PSScriptRoot "build-backend.ps1")
if ($LASTEXITCODE -ne 0) { throw "后端构建失败，停止安装包构建" }


# 1) 前端构建
Write-Host "[build] 1/4 vite build"
Push-Location (Join-Path $root 'web')
try { $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'; & npm.cmd run build 2>&1 | Out-Null; $c = $LASTEXITCODE; $ErrorActionPreference = $eap; if ($c -ne 0) { throw "vite build 失败" } } finally { Pop-Location }

# 1.5) 构建前端/后端契约检查
$required = @('/api/auth/email/send-code','/api/auth/register','/api/auth/password/reset','/api/auth/login','/api/system/build-info')
$server = Join-Path $root 'backend/app/server.py'
$serverText = Get-Content $server -Raw -Encoding UTF8
foreach ($route in $required) { if ($serverText -notmatch [regex]::Escape($route)) { throw "缺少后端认证路由: $route" } }
Write-Host '[build] auth contract OK'

# 2) electron-builder --dir (asar:false 只做 unpacked，规避 app-builder asar 被杀)
Write-Host "[build] 2/4 electron-builder --dir (asar:false)"
if (Test-Path $build) { Remove-Item $build -Recurse -Force }
Push-Location (Join-Path $root 'desktop')
try {
  $eap2 = $ErrorActionPreference; $ErrorActionPreference = 'Continue'; & npx.cmd electron-builder --dir --config.asar=false --config.directories.output="$build" 2>&1 | Out-Null; $c2 = $LASTEXITCODE; $ErrorActionPreference = $eap2
  if ($c2 -ne 0) { throw "electron-builder --dir 失败" }
} finally { Pop-Location }
if (-not (Test-Path (Join-Path $unpacked "FrameWeave.exe"))) { throw "win-unpacked 未生成" }

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

# 3) 生成 NSIS 脚本（模板注入版本/路径）
Write-Host "[build] 3/4 生成 NSIS 脚本"
$tpl = Get-Content (Join-Path $PSScriptRoot "nsis\frameweave.nsi.tpl") -Raw -Encoding UTF8
$nsi = $tpl.Replace('$FW_VER', $ver).Replace('$FW_OUT', $out.Replace("/", "\")).Replace('$FW_UNPACKED', $unpacked.Replace("/", "\"))
$nsiFile = Join-Path $build "frameweave.nsi"
Set-Content -Path $nsiFile -Value $nsi -Encoding UTF8

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
