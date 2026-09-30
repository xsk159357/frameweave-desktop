; 安装/升级/卸载前自动结束运行中的旧版应用与本地后端。
; 覆盖 electron-builder 默认 CHECK_APP_RUNNING（taskkill 重试后仍提示"无法关闭"卡死升级）。
!macro customCheckAppRunning
  DetailPrint "Closing running FrameWeave..."
  ; 结束 Electron 主进程（含其子进程树）
  nsExec::ExecToLog 'taskkill /f /t /im "${APP_EXECUTABLE_FILENAME}"'
  ; 结束本地 Python 后端（可能残留孤儿进程锁文件）
  nsExec::ExecToLog 'taskkill /f /t /im "frameweave-backend.exe"'
  ; 等待文件句柄释放（避免"in use"覆盖失败）
  Sleep 600
!macroend
