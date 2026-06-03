# Setup Windows Task Scheduler for stock-trading
# Run this script in PowerShell as Administrator:
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\setup_task_scheduler.ps1

$ErrorActionPreference = "Stop"

# ── 1. Copy .bat files to C:\stock-trading\scripts\windows ──────────────────
$winDest = "C:\stock-trading\scripts\windows"
if (-not (Test-Path $winDest)) {
    New-Item -ItemType Directory -Path $winDest -Force | Out-Null
}

$wslScriptDir = "\\wsl.localhost\Ubuntu\home\sys1\stock-trading\scripts\windows"
Copy-Item "$wslScriptDir\task_data_update.bat"  "$winDest\" -Force
Copy-Item "$wslScriptDir\task_daily_signals.bat" "$winDest\" -Force
Copy-Item "$wslScriptDir\task_earnings_jp.bat"  "$winDest\" -Force
Copy-Item "$wslScriptDir\task_earnings_us.bat"  "$winDest\" -Force
Write-Host "[OK] .bat files copied to $winDest"

# ── 2. Create task folder ────────────────────────────────────────────────────
$scheduler = New-Object -ComObject Schedule.Service
$scheduler.Connect()
$root = $scheduler.GetFolder("\")
try { $root.GetFolder("StockTrading") } catch {
    $root.CreateFolder("StockTrading") | Out-Null
    Write-Host "[OK] Task folder StockTrading created"
}

# ── 3. Register tasks from XML ───────────────────────────────────────────────
$xmlFiles = @(
    @{ Name = "DataUpdate";   File = "StockDataUpdate.xml" },
    @{ Name = "DailySignals"; File = "StockDailySignals.xml" },
    @{ Name = "EarningsJP";   File = "StockEarningsJP.xml" },
    @{ Name = "EarningsUS";   File = "StockEarningsUS.xml" }
)

foreach ($item in $xmlFiles) {
    $xmlPath = "$wslScriptDir\$($item.File)"
    $xmlContent = Get-Content $xmlPath -Raw
    $folder = $scheduler.GetFolder("\StockTrading")
    $folder.RegisterTask($item.Name, $xmlContent, 6, $null, $null, 3) | Out-Null
    Write-Host "[OK] Registered: StockTrading\$($item.Name)"
}

Write-Host ""
Write-Host "All tasks registered. Verify in Task Scheduler under \StockTrading\"
