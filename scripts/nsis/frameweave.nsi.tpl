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
