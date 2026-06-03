"""Generate register_tasks.ps1 with inline XML for Windows Task Scheduler."""

import pathlib

xmls = {
    "DataUpdate": "scripts/windows/StockDataUpdate.xml",
    "DailySignals": "scripts/windows/StockDailySignals.xml",
    "EarningsJP": "scripts/windows/StockEarningsJP.xml",
    "EarningsUS": "scripts/windows/StockEarningsUS.xml",
}

lines = [
    "$scheduler = New-Object -ComObject Schedule.Service",
    "$scheduler.Connect()",
    "try { $scheduler.GetFolder('\\StockTrading') | Out-Null; Write-Host '[SKIP] folder exists' } catch { $scheduler.GetFolder('\\').CreateFolder('StockTrading') | Out-Null; Write-Host '[OK] folder created' }",
    "$folder = $scheduler.GetFolder('\\StockTrading')",
]

for name, path in xmls.items():
    content = pathlib.Path(path).read_text(encoding="utf-8")
    # escape for PowerShell here-string
    escaped = content.replace("`", "``").replace("$", "`$")
    lines.append(f'$xml_{name} = @"\n{escaped}\n"@')
    lines.append(f'$folder.RegisterTask("{name}", $xml_{name}, 6, $null, $null, 3) | Out-Null')
    lines.append(f'Write-Host "[OK] {name} registered"')

out = pathlib.Path("/mnt/c/stock-trading/scripts/windows/register_tasks.ps1")
out.write_text("\n".join(lines), encoding="utf-8")
print(f"Written: {out}")
