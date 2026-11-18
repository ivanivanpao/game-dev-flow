# 用 PowerShell 啟動圖形化介面（跟 gui.bat 做一樣的事：用專案的 .venv 執行，自動截圖才會正常）
#
# 用法：
#   - 檔案總管裡對 gui.ps1 按右鍵 →「用 PowerShell 執行」
#   - 或在專案根目錄的終端機執行：.\gui.ps1
#     可以加參數，例如：.\gui.ps1 --port 8800 --no-browser
#   - 如果出現「因為這個系統上已停用指令碼執行」，改用（只對這一次執行放寬，不會改系統設定）：
#     powershell -ExecutionPolicy Bypass -File .\gui.ps1
#
# 關閉介面：在這個視窗按 Ctrl+C

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8     # 介面伺服器的訊息有中文，避免變亂碼
Set-Location -LiteralPath $PSScriptRoot                       # 不管從哪裡執行，都切到專案根目錄

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
    Write-Host '找不到 .venv。請先依 README 的「安裝」建立虛擬環境並安裝套件。' -ForegroundColor Red
    Read-Host '按 Enter 關閉'
    exit 1
}

& $py (Join-Path $PSScriptRoot 'gui\server.py') @args          # @args：把你給的參數原封不動交給 server.py
$code = $LASTEXITCODE
if ($code -ne 0) {
    # 出錯時停在這裡，讓你看得到錯誤訊息（右鍵執行時視窗才不會一閃就關掉）
    Read-Host "介面結束（回傳碼 $code），按 Enter 關閉"
}
exit $code
