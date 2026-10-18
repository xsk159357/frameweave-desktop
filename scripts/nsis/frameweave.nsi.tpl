Unicode true
SetCompressor /SOLID lzma
Name "FrameWeave $FW_VER"
OutFile "$FW_OUT"
InstallDir "$LOCALAPPDATA\Programs\FrameWeave"
RequestExecutionLevel user
XPStyle on
ShowInstDetails hide
ShowUninstDetails hide

!include "MUI2.nsh"
!include "FileFunc.nsh"

; ---- 品牌资产（generate-installer-assets.py 产出，随源码入库，路径由 build-installer.ps1 注入）----
; 安装器/卸载器 exe 图标（流光取景框）：
; 必须用 MUI_ICON/MUI_UNICON 宏定义（而非裸 Icon 指令）——MUI2 的 MUI_INTERFACE
; 在首个 MUI_LANGUAGE 处插入 `Icon "${MUI_ICON}"`，未定义时回落到 NSIS 默认图标，
; 因此这里必须先 define，后于 MUI_LANGUAGE 出现的裸 Icon 会被默认值覆盖。
!define MUI_ICON "@FW_ICON@"
!define MUI_UNICON "@FW_UNICON@"

; ---- 头部横幅 150x57：白底留标题文字，右缘深空 + 小 logo ----
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "@FW_HEADER_BMP@"
!define MUI_HEADERIMAGE_UNBITMAP "@FW_HEADER_BMP@"
!define MUI_HEADERIMAGE_BITMAP_NOSTRETCH
!define MUI_HEADERIMAGE_UNBITMAP_NOSTRETCH

; ---- 欢迎/完成/卸载侧栏 164x314：深空 + 流光 logo ----
!define MUI_WELCOMEFINISHPAGE_BITMAP "@FW_SIDEBAR_BMP@"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "@FW_SIDEBAR_BMP@"

; wizard pages (skipped in /S silent mode)
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Var appUpdated

Function .onInit
  ; electron-updater quitAndInstall passes --updated (launch after install) and --force-run
  ${GetParameters} $R0
  ${GetOptions} $R0 "--updated" $R1
  IfErrors +2
    StrCpy $appUpdated "1"
FunctionEnd

Section "install" SecMain
  SetOutPath "$INSTDIR"
  ; inject all win-unpacked files (expanded by makensis at compile time, absolute path)
  File /r "$FW_UNPACKED\*"
  ; register install location (electron-updater upgrade detection)
  WriteRegStr HKCU "Software\com.frameweave.app" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FrameWeave" "DisplayName" "FrameWeave"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FrameWeave" "DisplayVersion" "$FW_VER"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FrameWeave" "Publisher" "FrameWeave"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FrameWeave" "UninstallString" '"$INSTDIR\Uninstall FrameWeave.exe"'
  CreateDirectory "$SMPROGRAMS\FrameWeave"
  CreateShortcut "$SMPROGRAMS\FrameWeave\FrameWeave.lnk" "$INSTDIR\FrameWeave.exe"
  CreateShortcut "$DESKTOP\FrameWeave.lnk" "$INSTDIR\FrameWeave.exe"
  WriteUninstaller "$INSTDIR\Uninstall FrameWeave.exe"
SectionEnd

Section "post"
  ${If} $appUpdated == "1"
    ExecShell "" "$INSTDIR\FrameWeave.exe"
  ${EndIf}
SectionEnd

; ---- uninstall ----
Section "Uninstall"
  Delete "$INSTDIR\Uninstall FrameWeave.exe"
  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\FrameWeave\FrameWeave.lnk"
  RMDir "$SMPROGRAMS\FrameWeave"
  Delete "$DESKTOP\FrameWeave.lnk"
  DeleteRegKey HKCU "Software\com.frameweave.app"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\FrameWeave"
SectionEnd
