@echo off
chcp 65001 >nul
rem 雙擊這個檔案啟動圖形化介面（用專案的 .venv 執行，自動截圖才會正常）
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 找不到 .venv。請先依 README 的「安裝」建立虛擬環境並安裝套件。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" gui\server.py %*
pause
