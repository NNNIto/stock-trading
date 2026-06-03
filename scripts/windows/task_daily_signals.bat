@echo off
REM Daily signal generation — runs inside WSL2
REM Triggered by Windows Task Scheduler at 06:15 JST on weekdays
wsl --distribution Ubuntu --user sys1 --exec /bin/bash -c "cd /home/sys1/stock-trading && /home/sys1/.local/bin/uv run python scripts/daily_signals.py >> /home/sys1/stock-trading/logs/daily_signals.log 2>&1"
