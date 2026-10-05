# verify-vm.ps1 — 虚拟机安装验证（无边框 + 全量修复版）
# 用法:
#   pwsh scripts/verify-vm.ps1 -Installer "release/0.2.9/FrameWeave-Setup-0.2.9.exe" -GuestPass <VM密码>
# 依赖: VMware Workstation 正在运行；vmrun 可用；VM 有 base-clean 快照可回滚（无则 -SkipRevert）
param(
  [string]$Installer = "",
  [string]$Vmx = "D:/镜像/Windows 10 x64.vmx",
  [string]$Vmrun = "D:/VMware-17.5.1 安装包+秘钥/vmrun.exe",
  [string]$GuestUser = "Administrator",
  [string]$GuestPass = "",
  [switch]$SkipRevert
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Installer) { throw "缺少 -Installer 参数" }
$Installer = (Join-Path $root $Installer)
if (-not (Test-Path $Installer)) { throw "安装包不存在: $Installer" }
if (-not (Test-Path $Vmx)) { throw "VM 不存在: $Vmx" }
if (-not (Test-Path $Vmrun)) { throw "vmrun 不存在: $Vmrun" }
if (-not $GuestPass) { throw "需要 -GuestPass（VM 登录密码）" }
$g = "guest:" + $GuestUser + ":" + $GuestPass
Write-Host "[vm] 开始虚拟机验证（VMware 需已运行）"

# 0) 删除旧版：优先回滚干净基线快照；无快照则卸载旧版（Uninstall + 目录 + 注册表）
if (-not $SkipRevert) {
  $snaps = & $Vmrun listSnapshots $Vmx 2>&1
  if ($snaps -match "base-clean") {
    & $Vmrun revertToSnapshot $Vmx "base-clean" 2>&1 | Out-Host
    Write-Host "[vm] 已回滚 base-clean 快照（旧版已清除）"
    Start-Sleep -Seconds 5
  } else {
    Write-Host "[vm] 无 base-clean 快照 → 卸载 Guest 内旧版 FrameWeave"
    & $Vmrun -gu $GuestUser -gp $GuestPass copyFileFromHostToGuest $Vmx (Join-Path $root ".tmp\vm-uninstall.ps1") "C:\fw-uninstall.ps1" 2>&1 | Out-Host
    $ures = & $Vmrun -gu $GuestUser -gp $GuestPass runProgramInGuest $Vmx -interactive "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File C:\fw-uninstall.ps1" 2>&1
    $ures | ForEach-Object { Write-Host "   [vm:guest] " $_ }
    Start-Sleep -Seconds 5
  }
}

# 1) 复制安装包
Write-Host "[vm] 1/5 复制安装包到 Guest"
& $Vmrun -gu $GuestUser -gp $GuestPass copyFileFromHostToGuest $Vmx $Installer "C:\fw-setup.exe" 2>&1 | Out-Host

# 2) 静默安装
Write-Host "[vm] 2/5 静默安装 (NSIS /S)"
& $Vmrun -gu $GuestUser -gp $GuestPass runProgramInGuest $Vmx -interactive "C:\fw-setup.exe" "/S" 2>&1 | Out-Host
Start-Sleep -Seconds 20

# 3) 启动应用
Write-Host "[vm] 3/5 启动 FrameWeave"
& $Vmrun -gu $GuestUser -gp $GuestPass runProgramInGuest $Vmx -interactive "C:\Program Files\FrameWeave\FrameWeave.exe" "" 2>&1 | Out-Host
Start-Sleep -Seconds 25

# 4) 推送校验脚本并执行
Write-Host "[vm] 4/5 执行 Guest 内校验（进程/后端/无边框窗口风格）"
& $Vmrun -gu $GuestUser -gp $GuestPass copyFileFromHostToGuest $Vmx (Join-Path $root ".tmp\vm-check.ps1") "C:\fw-check.ps1" 2>&1 | Out-Host
$res = & $Vmrun -gu $GuestUser -gp $GuestPass runProgramInGuest $Vmx -interactive "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File C:\fw-check.ps1" 2>&1
$res | ForEach-Object { Write-Host "   " $_ }

# 5) 截图
Write-Host "[vm] 5/5 截图"
$shot = Join-Path (Join-Path $root ".tmp") "vm-verify.png"
& $Vmrun -gu $GuestUser -gp $GuestPass captureScreen $Vmx $shot 2>&1 | Out-Host
Write-Host "[vm] 截图: $shot"

Write-Host "[vm] 完成。核对: WS_CAPTION=False(无边框) / health:True / 截图右上原生控件与顶栏同色"
