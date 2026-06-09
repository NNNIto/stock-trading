#!/usr/bin/env bash
# stock-trading 自動実行 cron 設定スクリプト
# JST 06:00 → data_update.py（データ取得）
# JST 06:15 → daily_signals.py（シグナル生成 + Slack通知）
# JST 07:00（毎月1日）→ oos_report.py（OOS検証レポート）
#
# 使い方: bash scripts/setup_cron.sh
# 確認:   crontab -l | grep stock-trading

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$DIR/logs"
UV_BIN="$(command -v uv 2>/dev/null || echo "uv")"

mkdir -p "$LOG_DIR"

# .env を source して SLACK_WEBHOOK_URL を参照するラッパー
_slack_error() {
    local msg="$1"
    # shellcheck disable=SC1090
    source "$DIR/.env" 2>/dev/null || true
    if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
        curl -s -X POST "$SLACK_WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"text\":\"[stock-trading] $msg が失敗しました\"}" 2>/dev/null || true
    fi
}
export -f _slack_error 2>/dev/null || true

# システムタイムゾーンは JST (Asia/Tokyo) なので cron 時刻は JST で直接指定する
#   JST 06:00 Mon-Fri  → cron: 0  6 * * 1-5
#   JST 06:15 Mon-Fri  → cron: 15 6 * * 1-5
#   JST 07:00 毎月1日  → cron: 0  7 1 * *

# 既存エントリをいったん除去して再登録
crontab -l 2>/dev/null | grep -v "stock-trading" > /tmp/_cron_tmp || true

cat >> /tmp/_cron_tmp << EOF

# --- stock-trading ---
# JST 06:00 Mon-Fri: データ更新
0 6 * * 1-5 cd '$DIR' && '$UV_BIN' run python scripts/data_update.py >> $LOG_DIR/\$(date +\%Y-\%m-\%d).log 2>&1 || bash -c 'source $DIR/.env 2>/dev/null; [ -n "\${SLACK_WEBHOOK_URL:-}" ] && curl -s -X POST "\$SLACK_WEBHOOK_URL" -H "Content-Type: application/json" -d "{\"text\":\"[stock-trading] data_update.py が失敗しました\"}" || true'
# JST 06:15 Mon-Fri: シグナル生成 + Slack通知
15 6 * * 1-5 cd '$DIR' && '$UV_BIN' run python scripts/daily_signals.py >> $LOG_DIR/\$(date +\%Y-\%m-\%d).log 2>&1 || bash -c 'source $DIR/.env 2>/dev/null; [ -n "\${SLACK_WEBHOOK_URL:-}" ] && curl -s -X POST "\$SLACK_WEBHOOK_URL" -H "Content-Type: application/json" -d "{\"text\":\"[stock-trading] daily_signals.py が失敗しました\"}" || true'
# JST 07:00 毎月1日: OOS検証レポート
0 7 1 * * cd '$DIR' && '$UV_BIN' run python scripts/oos_report.py >> $LOG_DIR/oos_\$(date +\%Y-\%m).log 2>&1 || bash -c 'source $DIR/.env 2>/dev/null; [ -n "\${SLACK_WEBHOOK_URL:-}" ] && curl -s -X POST "\$SLACK_WEBHOOK_URL" -H "Content-Type: application/json" -d "{\"text\":\"[stock-trading] oos_report.py が失敗しました\"}" || true'
EOF

crontab /tmp/_cron_tmp
rm -f /tmp/_cron_tmp

echo "✅ cron 登録完了:"
crontab -l | grep stock-trading
