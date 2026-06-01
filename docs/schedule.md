# 運用スケジュール

**作成:** 2026-06-01  
**ステータス:** ペーパートレード稼働中

---

## 日次（平日毎日）

### 自動（cron）

| 時刻 | 処理 | ログ |
|------|------|------|
| 06:00 | JP + US OHLCV 更新 | `logs/data_update.log` |
| 06:15 | シグナル生成 → Slack 通知 | `logs/daily_signals.log` |

### 手動

| 時刻 | 作業 |
|------|------|
| 08:00 | Slack でシグナル確認 |
| 08:00〜09:00 | シグナルがあれば楽天証券で発注（翌営業日始値約定のため前日終値が目安） |
| 翌朝 | 約定結果を DB に手動入力（→「約定入力」参照） |

### ログ確認コマンド

```bash
tail -20 logs/data_update.log
tail -20 logs/daily_signals.log
```

### 異常パターンと対処

| 症状 | 原因 | 対処 |
|------|------|------|
| Slack に「DB空」エラー | data_update が失敗 | `uv run python scripts/data_update.py` を手動実行 |
| シグナルが連日 0 件 | 指数が 200MA 下（ログに `macro filter blocked` ） | 正常。市場回復を待つ |
| parity check FAIL | シナリオロジックに不整合 | ログを確認してバグ修正 |
| Slack 通知が来ない | SLACK_WEBHOOK_URL の問題 | `.env` の値を確認 |

---

## 週次（毎週月曜）

### 自動（cron）

| 時刻 | 処理 | ログ |
|------|------|------|
| 06:00 | JP + US OHLCV 更新（通常通り） | `logs/data_update.log` |
| 06:15 | シグナル生成 + **parity check** 自動実行 | `logs/daily_signals.log` |
| 06:30 | JP 決算データ bulk 更新（J-Quants） | `logs/earnings_jp.log` |
| 06:45 | US 決算データ更新（yfinance、150銘柄） | `logs/earnings_us.log` |

### 手動チェックリスト

```
[ ] parity check の結果確認（logs/daily_signals.log に PASS/FAIL が出る）
[ ] 週次レポート生成・確認（→「週次レポート」参照）
[ ] オープンポジションの含み損益確認
[ ] DD が -15% を超えていないか確認（-20% でサーキットブレーカー発動）
[ ] 先週のシグナル精度を振り返る（シグナル数・勝率）
```

### 週次レポート生成

```bash
uv run python -c "
from src.reporting.generator import generate_weekly_report
import json
r = generate_weekly_report()
print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
"
```

---

## 月次（毎月第1営業日）

### チェックリスト

```
[ ] 月次パフォーマンス集計
[ ] バックテスト再実行（IS期間のみ、OOS: 2025-01-01〜 は触らない）
[ ] WF degradation ratio 確認（基準: 0.5 以上）
[ ] Sharpe の推移確認（目標: 1.0）
[ ] 最大 DD 確認（閾値: -20%。超えていればポジション縮小 or 停止検討）
[ ] US earnings データの蓄積状況確認（S4 シグナル数が増えているか）
```

### バックテスト再実行

```bash
uv run python scripts/run_backtest.py --start 2019-01-01 --end 2024-12-31 --market US
```

### ポジション・パフォーマンス確認

```bash
uv run python -c "
from src.portfolio.manager import PortfolioManager
with PortfolioManager() as pm:
    pos = pm.get_open_positions(mode='paper')
    print(pos)
"
```

---

## 移行判断

### ペーパートレード → 本番（少額）の条件

以下をすべて満たしたタイミングで移行を検討する。

| 条件 | 基準 |
|------|------|
| ペーパートレード期間 | 2週間以上 |
| シグナル件数 | 5件以上で勝率 40% 以上 |
| システム異常なし | cron / Slack / DB すべて正常稼働 |
| Sharpe（US WF後） | 0.6 以上（現状 0.63 → 達成済み） |

### 本番移行後の注意

- 初期資金: 30万円（`capital_jpy: 3000000` の 10%）で開始
- 最大ポジション数: 設定通り 7 件まで
- サーキットブレーカー: ポートフォリオ DD -20% で全クローズ

---

## 約定入力（手動）

```bash
# エントリー
uv run python -c "
from src.portfolio.manager import PortfolioManager
from datetime import date
with PortfolioManager() as pm:
    pm.open_position(
        symbol='DELL',
        scenario_id='S2',
        market='US',
        entry_date=date.today(),
        entry_price=421.50,   # 実際の約定価格
        quantity=10,
        mode='paper',         # 本番移行後は 'live'
    )
"

# エグジット
uv run python -c "
from src.portfolio.manager import PortfolioManager
with PortfolioManager() as pm:
    pm.close_position('DELL')
"
```

---

## Sharpe 改善ロードマップ（ペーパートレード後）

| 優先度 | 施策 | 期待効果 |
|--------|------|----------|
| 高 | S4（PEAD）再バックテスト（US earnings 蓄積後） | シグナル数増加 |
| 中 | ユニバース拡大（250 → S&P500 全 500 銘柄） | シグナル数増加 |
| 低 | 新シナリオ追加（モメンタム継続など） | 分散効果 |

現状: Sharpe **0.63**（US WF後）→ 目標 **1.0**
