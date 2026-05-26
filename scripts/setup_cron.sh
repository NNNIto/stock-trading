#!/usr/bin/env bash
# setup_cron.sh — 日次バッチの crontab 設定
#
# 使い方:
#   bash scripts/setup_cron.sh          # 登録
#   bash scripts/setup_cron.sh --remove # 削除
#
# スケジュール (JST):
#   06:00  data_update.py  — 米国前日終値 + 為替取得
#   06:15  daily_signals.py — シグナル生成 + Slack通知
#
# 前提:
#   - WSL2 / Linux で cron が動作していること (`service cron start`)
#   - SLACK_WEBHOOK_URL が /home/natsuki/stock-trading/.env に設定済み
#   - uv が PATH に含まれていること

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$REPO_DIR/logs"
PYTHON="uv run python"

mkdir -p "$LOG_DIR"

DATA_UPDATE_JOB="0 6 * * 1-5 cd $REPO_DIR && $PYTHON scripts/data_update.py --market US >> $LOG_DIR/data_update.log 2>&1"
DAILY_SIGNALS_JOB="15 6 * * 1-5 cd $REPO_DIR && $PYTHON scripts/daily_signals.py >> $LOG_DIR/daily_signals.log 2>&1"

remove_jobs() {
    crontab -l 2>/dev/null \
        | grep -v "data_update.py" \
        | grep -v "daily_signals.py" \
        | crontab -
    echo "cron jobs removed."
}

if [[ "${1:-}" == "--remove" ]]; then
    remove_jobs
    exit 0
fi

# 既存エントリを除去してから再登録（冪等）
(
    crontab -l 2>/dev/null \
        | grep -v "data_update.py" \
        | grep -v "daily_signals.py"
    echo "$DATA_UPDATE_JOB"
    echo "$DAILY_SIGNALS_JOB"
) | crontab -

echo "cron jobs registered:"
crontab -l | grep -E "data_update|daily_signals"
echo ""
echo "ログ確認: tail -f $LOG_DIR/daily_signals.log"
echo "手動テスト: cd $REPO_DIR && uv run python scripts/daily_signals.py --dry-run"
