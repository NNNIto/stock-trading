@echo off
REM Daily data update — runs inside WSL2
REM Triggered by Windows Task Scheduler at 06:00 JST on weekdays
wsl --distribution Ubuntu --user sys1 --exec /bin/bash -c "cd /home/sys1/stock-trading && /home/sys1/.local/bin/uv run python scripts/data_update.py >> /home/sys1/stock-trading/logs/data_update.log 2>&1"
