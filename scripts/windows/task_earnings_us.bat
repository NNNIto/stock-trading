@echo off
REM Weekly US earnings update — runs inside WSL2
REM Triggered by Windows Task Scheduler at 06:45 JST on Mondays
wsl --distribution Ubuntu --user sys1 --exec /bin/bash -c "cd /home/sys1/stock-trading && /home/sys1/.local/bin/uv run python scripts/data_update.py --market US --with-earnings >> /home/sys1/stock-trading/logs/earnings_us.log 2>&1"
