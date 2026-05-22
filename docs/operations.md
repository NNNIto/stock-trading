# 運用手順書

**バージョン:** 1.0  
**作成日:** 2026-05-23  
**ステータス:** Phase 4 完成時点

---

## 1. 日次オペレーション

### 1.1 cron 設定（Linux）

```bash
# crontab -e で以下を追加
# 毎日 06:01 JST: データ更新
1 6 * * 1-5  cd /home/sys1/stock-trading && uv run python scripts/data_update.py >> logs/data_update.log 2>&1

# 毎日 06:05 JST: シグナル生成
5 6 * * 1-5  cd /home/sys1/stock-trading && uv run python scripts/daily_signals.py >> logs/daily_signals.log 2>&1
```

> タイムゾーン確認: `timedatectl | grep "Time zone"` で Asia/Tokyo を確認すること

### 1.2 日次作業フロー

| 時刻 | 担当 | 内容 |
|------|------|------|
| 06:01 | cron | データ更新 (`data_update.py`) |
| 06:05 | cron | シグナル生成 (`daily_signals.py`) |
| 06:10 | Slack | 新規シグナル・エグジット推奨通知受信 |
| 08:00 | 人間 | Slack 確認 → 楽天証券で手動発注 |
| 09:00 | 人間 | 日本株: 寄付前後に発注確認 |
| 23:30 | 人間 | 米国株: 夜間発注 |
| 翌朝 | 人間 | 約定結果を Streamlit ダッシュボードから手動入力 |

### 1.3 シグナル確認手順

1. Slack 通知を確認
2. ダッシュボード起動: `uv run streamlit run src/reporting/dashboard.py`
3. 「シグナル」ページで本日のシグナルを確認
4. 「ポジション」ページで現在のポジションと含み損益を確認
5. エグジット推奨がある場合: 楽天証券で当日中に成行売り

### 1.4 約定結果の手動入力（CLI）

```bash
# ポジション追加（エントリー）
# PortfolioManager を使って追加（暫定: REPL or スクリプト）
uv run python -c "
from src.portfolio.manager import PortfolioManager
from datetime import date
with PortfolioManager() as pm:
    pm.open_position('AAPL', 'S2', 'US', date.today(), 178.5, 84, mode='paper')
"

# ポジション決済
uv run python -c "
from src.portfolio.manager import PortfolioManager
with PortfolioManager() as pm:
    pm.close_position('AAPL')
"
```

---

## 2. 週次オペレーション

### 2.1 週次チェックリスト（毎週月曜朝）

- [ ] ダッシュボードで先週のパフォーマンスを確認
- [ ] パリティ検証ログを確認（自動: 月曜日に `daily_signals.py` が実行）
- [ ] 週次レポート確認: `results/report_weekly_*.json`
- [ ] ドローダウンが -15% 超なら戦略修正トリガーを検討

```bash
# 週次レポート手動生成
uv run python -c "
from src.reporting.generator import generate_weekly_report
import json
r = generate_weekly_report()
print(json.dumps(r['overall'], indent=2, ensure_ascii=False))
"
```

### 2.2 パリティ検証の確認

```bash
# 手動実行
uv run python scripts/daily_signals.py --parity --dry-run
```

不一致が検出された場合:
1. ログを確認: `logs/daily_signals.log`
2. 発生したシナリオとシンボルを特定
3. `generate_signals` の実装と `add_indicators` の出力を照合
4. バグ修正後にスナップショットテストを更新: `uv run pytest tests/backtest/test_snapshot.py --snapshot-update`

---

## 3. 月次オペレーション

### 3.1 月末チェックリスト

- [ ] 月次レポート生成
- [ ] シナリオ別パフォーマンスのレビュー
- [ ] 修正トリガー条件確認（requirements.md 2.5）
- [ ] 過去バックテストとの予実比較

```bash
# 月次レポート生成
uv run python -c "
from src.reporting.generator import generate_monthly_report
r = generate_monthly_report()
print(f'取引数: {r[\"overall\"][\"trade_count\"]}')
"
```

### 3.2 修正トリガー条件（requirements.md 2.5）

以下のいずれかに該当する場合、シナリオ修正を検討する:

| 条件 | 閾値 |
|------|------|
| 20日連続シャープ < 0.5 | アラート済み |
| 30日 DD 継続 | アラート済み |
| 月次連続マイナス 3 ヶ月以上 | 要検討 |

---

## 4. 四半期オペレーション

### 4.1 四半期末チェックリスト

- [ ] バックテスト再実行（インサンプル全期間）
- [ ] ウォークフォワード分析再実行
- [ ] パラメータ見直し（scenarios.md 候補から選定）
- [ ] 変更する場合は Git コミット必須（理由・改善幅を記載）

```bash
# バックテスト再実行（インサンプル期間）
uv run python scripts/run_backtest.py --start 2018-01-01 --end 2024-12-31

# ウォークフォワード再実行
uv run python scripts/walkforward.py
```

---

## 5. 障害対応

### 5.1 データ取得失敗（yfinance エラー）

```bash
# ログ確認
tail -100 logs/data_update.log

# フォールバック: Stooq から取得（STOOQ_API_KEY 要設定）
export STOOQ_API_KEY=your_key
uv run python scripts/data_update.py --market JP

# 当日スキップ: 翌日の data_update.py 実行時に差分取得される
```

### 5.2 シグナル生成スクリプトのクラッシュ

```bash
# 手動再実行（--dry-run で確認後）
uv run python scripts/daily_signals.py --dry-run
uv run python scripts/daily_signals.py  # 問題なければ本番実行
```

### 5.3 ダッシュボード起動エラー

```bash
# Streamlit キャッシュクリア
uv run streamlit cache clear
uv run streamlit run src/reporting/dashboard.py
```

### 5.4 サーキットブレーカー発動

1. Slack 通知を確認（ポートフォリオ DD 閾値超過）
2. 新規エントリーは自動停止済み
3. 既存ポジションのエグジットは通常通り継続
4. 状況を確認し手動で復旧判断

---

## 6. L2 コードレビュー運用

**重要:** コードレビューは必ず実装セッションとは **別セッション** で実施すること。

### 6.1 レビュートリガー

以下の変更を加えた場合に L2 レビューを実施:
- `src/scenarios/` の変更
- `src/backtest/engine.py` の変更
- `src/validation/` の変更
- パラメータの大幅変更（YAML 更新）

### 6.2 レビュー手順

1. 新セッションを開始（前の実装セッションを閉じる）
2. `src/agents/code_reviewer.md` のプロンプトをコンテキストに設定
3. `src/agents/review_checklist.md` のチェックリストに沿って確認
4. 検出した問題は `src/agents/review_checklist.md` の「レビュー実施記録」に記録

---

## 7. ログファイル一覧

| ファイル | 内容 | ローテーション |
|---------|------|-------------|
| `logs/data_update.log` | データ更新バッチ | 日次（loguru 自動） |
| `logs/daily_signals.log` | シグナル生成 | 日次 |
| `logs/app.log` | アプリケーション全般 | 日次 |

---

## 8. 環境変数

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `SLACK_WEBHOOK_URL` | 推奨 | Slack 通知 URL |
| `STOOQ_API_KEY` | 任意 | Stooq フォールバック |

設定方法:
```bash
cp .env.example .env
# .env を編集して値を設定
```

---

## 改訂履歴

| バージョン | 日付 | 変更内容 |
|----------|------|---------|
| 1.0 | 2026-05-23 | 初版作成（Phase 4 完成時点） |
