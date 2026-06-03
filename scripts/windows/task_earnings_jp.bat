@echo off
REM Weekly JP earnings update — runs inside WSL2
REM Triggered by Windows Task Scheduler at 06:30 JST on Mondays
wsl --distribution Ubuntu --user sys1 --exec /bin/bash -c "cd /home/sys1/stock-trading && /home/sys1/.local/bin/uv run python scripts/data_update.py --bulk-earnings >> /home/sys1/stock-trading/logs/earnings_jp.log 2>&1"
