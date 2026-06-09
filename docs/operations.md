# 運用手順書

**バージョン:** 2.0
**作成日:** 2026-06-09
**対象バージョン:** v1.7.0（ペーパートレード中）

---

## 目次

1. [システム概要](#1-システム概要)
2. [日次オペレーション](#2-日次オペレーション)
3. [週次オペレーション](#3-週次オペレーション（月曜朝）)
4. [月次オペレーション](#4-月次オペレーション（毎月1日）)
5. [Phase D 移行判断](#5-phase-d-移行判断)
6. [トラブルシューティング](#6-トラブルシューティング)
7. [環境変数・設定](#7-環境変数設定)
8. [ログファイル一覧](#8-ログファイル一覧)

---

## 1. システム概要

| 項目 | 内容 |
|------|------|
| 自動実行方式 | Linux cron (JST) または GitHub Actions |
| JST 06:00 (月〜金) | `data_update.py` — OHLCV・決算データ取得 |
| JST 06:15 (月〜金) | `daily_signals.py` — シグナル生成 + Slack通知 |
| JST 07:00 (毎月1日) | `oos_report.py` — OOS検証レポート生成 |
| DB | DuckDB: `data/trading.duckdb` |
| ログ | `logs/YYYY-MM-DD.log`（日付ごとに出力） |
| 通知 | Slack Webhook (`SLACK_WEBHOOK_URL`) |
| 運用資金 | 仮想 300万円（ペーパートレード） |
| 最大ポジション数 | 7件 |

### 自動実行設定の確認

```bash
# cron 登録確認
crontab -l | grep stock-trading

# cron 未登録の場合はセットアップ
bash /home/natsuki/stock-trading/scripts/setup_cron.sh
```

---

## 2. 日次オペレーション

### 2.1 朝の Slack 通知確認

`daily_signals.py` は JST 06:15 以降に以下の順序で Slack メッセージを送信する。

| メッセージ | 内容 | 送信条件 |
|-----------|------|---------|
| モーニングステータス | シグナル件数・VIX・SPY 200MA上下・保有ポジション一覧 | 毎日必ず送信 |
| シグナルサマリー | 当日の新規 BUY シグナル一覧 | シグナルが 1 件以上の場合 |
| 個別シグナル詳細カード | 銘柄ごとのエントリー理由・ポジションサイズ・リスク情報 | シグナル件数分 |
| エグジット通知 | 保有ポジションの決済推奨（ストップロス/利確/保有日数超過） | 決済シグナルがある場合 |

**確認ポイント:**

- シグナルあり → 個別詳細カードで銘柄・シナリオ・エントリー価格・数量を確認
- シグナルなし（`0 raw signals before resolution`）→ 正常。特に対応不要
- エラーメッセージが届いた場合 → [6. トラブルシューティング](#6-トラブルシューティング) を参照

### 2.2 ログ確認

```bash
# 当日ログの末尾を確認（最終行に done が出ていれば正常）
tail -30 /home/natsuki/stock-trading/logs/$(date +%Y-%m-%d).log

# 正常終了の例:
# 2026-06-08 21:23:14 | INFO | daily_signals: done — 0 new, 0 exits

# エラーのみ抽出
grep -E "ERROR|WARNING" /home/natsuki/stock-trading/logs/$(date +%Y-%m-%d).log
```

**ログの見方:**

```
YYYY-MM-DD HH:MM:SS | レベル | モジュール:行番号 | メッセージ
```

- `signal: S6 AAPL (US) ret_5d=-0.123 rsi_2=5.2 ...` — シグナル発生（シナリオ・銘柄・指標値）
- `approved: S6 AAPL (US) close=$185.00 ...` — コンフリクト解決後の承認済みシグナル
- `macro_filter: SPY on YYYY-MM-DD → above 200MA` — マクロフィルター状態
- `daily_signals: done — N new, M exits` — 完了サマリ

### 2.3 エラー時の手動再実行

```bash
cd /home/natsuki/stock-trading

# 1. まずドライランで確認（DB書き込みなし・Slack通知なし）
uv run python scripts/daily_signals.py --dry-run

# 2. 問題なければ本番実行（日付は最新DBデータに自動設定される）
uv run python scripts/daily_signals.py

# 3. 特定日付を指定する場合
uv run python scripts/daily_signals.py --date 2026-06-09

# データ取得が先に失敗していた場合はデータ更新から実行
uv run python scripts/data_update.py
uv run python scripts/daily_signals.py
```

> **注意:** `daily_signals.py` はシグナルの重複書き込みを防ぐ冪等処理が入っているため、
> 同一日に複数回実行しても2回目以降は `signals for YYYY-MM-DD already exist — skip` となりスキップされる。

---

## 3. 週次オペレーション（月曜朝）

### 3.1 先週シグナルの勝敗確認

シグナルは DuckDB の `signals` テーブルに記録される。以下のクエリで先週分を確認する。

```bash
cd /home/natsuki/stock-trading

# 先週のシグナル一覧を確認
uv run python -c "
import duckdb
from datetime import date, timedelta

conn = duckdb.connect('data/trading.duckdb')
monday = date.today() - timedelta(days=date.today().weekday())
last_mon = monday - timedelta(weeks=1)
last_fri = monday - timedelta(days=1)

rows = conn.execute('''
    SELECT signal_date, scenario_id, symbol, action, expected_entry_price
    FROM signals
    WHERE signal_date BETWEEN ? AND ?
    ORDER BY signal_date, scenario_id
''', [last_mon.isoformat(), last_fri.isoformat()]).fetchall()
for r in rows:
    print(r)
conn.close()
"
```

```bash
# 保有中・決済済みポジション一覧
uv run python -c "
import duckdb
conn = duckdb.connect('data/trading.duckdb')
print('=== オープンポジション ===')
rows = conn.execute('''
    SELECT symbol, scenario_id, entry_date, entry_price, quantity, current_price
    FROM positions
    WHERE mode = 'paper' AND status = 'open'
    ORDER BY entry_date
''').fetchall()
for r in rows:
    print(r)
print()
print('=== 直近20件の決済済みトレード ===')
rows = conn.execute('''
    SELECT symbol, scenario_id, entry_date, exit_date,
           entry_price, exit_price, pnl, exit_reason
    FROM trades
    WHERE mode = 'paper'
    ORDER BY exit_date DESC LIMIT 20
''').fetchall()
for r in rows:
    print(r)
conn.close()
"
```

### 3.2 ペーパートレード実績の集計

```bash
cd /home/natsuki/stock-trading

uv run python -c "
import duckdb, json
conn = duckdb.connect('data/trading.duckdb')

# シナリオ別集計
rows = conn.execute('''
    SELECT scenario_id,
           COUNT(*) as trades,
           ROUND(AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) as win_pct,
           ROUND(SUM(pnl), 0) as total_pnl
    FROM trades
    WHERE mode = 'paper' AND exit_date IS NOT NULL
    GROUP BY scenario_id
    ORDER BY scenario_id
''').fetchall()
print('シナリオ | 件数 | 勝率 | 累積損益')
for r in rows:
    print(f'  {r[0]}: {r[1]}件, 勝率{r[2]}%, 損益{r[3]:.0f}円')
conn.close()
"
```

### 3.3 パリティ検証（月曜日は自動実行）

`daily_signals.py` は月曜日に自動でパリティチェック（過去30日のバックテスト/リアルタイム一致性検証）を実行する。
Slack に "パリティ検証FAIL" メッセージが届いた場合は以下を確認する。

```bash
# 手動でパリティチェックを実行（DB書き込みなし）
cd /home/natsuki/stock-trading
uv run python scripts/daily_signals.py --parity --dry-run

# ログからパリティ結果を確認
grep -E "parity|PASS|FAIL" /home/natsuki/stock-trading/logs/$(date +%Y-%m-%d).log
```

---

## 4. 月次オペレーション（毎月1日）

### 4.1 OOS レポートの確認

毎月1日 JST 07:00 に `oos_report.py` が自動実行される。
レポートは `reports/oos_YYYY-MM.json` に保存され、内容は Slack にも投稿される。

**Slack 投稿内容の例:**

```
=== OOS検証レポート (2025-01-01 〜 2026-06-01) ===

全体
  トレード数   : 12件
  勝率         : 50.0%
  CAGR         : +8.5%
  Sharpe       : 0.58
  最大DD       : -8.2%
  PF           : 1.45

戦略別
  S2: 勝率 55% / CAGR +6.2% / PF 1.62
  S6: 勝率 45% / CAGR +2.3% / PF 1.28

バックテスト比較
  Sharpe  IS 0.63 → OOS 0.58  ✅ 許容範囲
  勝率    IS 56.1% → OOS 50.0%  ✅ 許容範囲
  最大DD  IS -15.9% → OOS -8.2%  ✅ 改善
```

```bash
# OOS レポートの手動実行
cd /home/natsuki/stock-trading
uv run python scripts/oos_report.py --slack

# 特定の開始日を指定する場合
uv run python scripts/oos_report.py --start 2025-01-01 --slack

# JSON レポートの確認
cat /home/natsuki/stock-trading/reports/oos_$(date +%Y-%m).json | python3 -m json.tool

# OOS ログの確認
cat /home/natsuki/stock-trading/logs/oos_$(date +%Y-%m).log
```

### 4.2 WF ロバストネス指標の確認

```bash
# ウォークフォワード結果の確認
cat /home/natsuki/stock-trading/results/walkforward_20260608_172227.json | python3 -m json.tool | grep -E "sharpe|win_rate|robustness"
```

### 4.3 月次チェックリスト

- [ ] `reports/oos_YYYY-MM.json` が生成されていること
- [ ] Sharpe OOS が IS の 95% 以上（`✅ 許容範囲` または `✅ 改善`）
- [ ] 最大ドローダウンが -25% 以内（サーキットブレーカー: -20%）
- [ ] 勝率が 40% 以上（Phase D 移行条件）
- [ ] トレード件数が増加傾向にあること

---

## 5. Phase D 移行判断

### 5.1 現在の移行条件達成状況（2026-06-09 時点）

| 条件 | 目標値 | 現状 | 状態 |
|------|--------|------|------|
| Sharpe (IS バックテスト) | ≥ 0.6 | 0.63 | 達成 |
| ペーパートレード期間 | 2 週間以上 | 運用中 | 達成 |
| シグナル 5 件以上で勝率 | ≥ 40% | トレード実績蓄積中 | **未達** |

### 5.2 移行判断チェックリスト

月次OOSレポートで以下をすべて確認してから移行を判断する。

- [ ] ペーパートレードで 5 件以上のクローズドトレードがある
- [ ] 勝率が 40% 以上（OOS レポートの `win_rate`）
- [ ] 最大ドローダウンが -20% 未満
- [ ] Sharpe OOS ≥ 0.5（IS の約 80% 以上）
- [ ] 直近 1 ヶ月でサーキットブレーカー発動なし

### 5.3 Phase D（少額本番）移行手順

**初期資金: 30万円（全体資金の10%相当）**

```bash
# 1. 設定ファイルを本番用に更新
#    config/settings.yaml の capital_jpy を 300000 に変更
#    mode を 'paper' → 'live' に変更（PortfolioManager の呼び出し箇所）

# 2. .env に本番用 API キーを設定
#    JQUANTS_API_KEY=<本番キー>
#    SLACK_WEBHOOK_URL=<本番通知先>

# 3. DB を初期化（ペーパートレードデータは別DBに退避）
cp data/trading.duckdb data/trading_paper_backup_$(date +%Y%m%d).duckdb

# 4. cron を再確認
crontab -l | grep stock-trading

# 5. ドライランで動作確認
uv run python scripts/daily_signals.py --dry-run

# 6. 証券口座の API 接続確認（楽天証券等）
```

> **注意:** Phase D 移行後も最初の 1 ヶ月は日次でログ・損益を手動確認すること。

---

## 6. トラブルシューティング

### 6.1 cron が動いていない

```bash
# cron 登録確認
crontab -l | grep stock-trading

# 登録されていない場合は再セットアップ
bash /home/natsuki/stock-trading/scripts/setup_cron.sh

# タイムゾーン確認（JST であること）
timedatectl | grep "Time zone"
# → "Time zone: Asia/Tokyo (JST, +0900)" であること

# cron サービスの状態確認
systemctl status cron 2>/dev/null || service cron status
```

### 6.2 Slack 通知が来ない

```bash
# 1. 環境変数の確認
cd /home/natsuki/stock-trading
grep SLACK_WEBHOOK_URL .env

# 2. Webhook URL の疎通テスト
source .env
curl -s -X POST "$SLACK_WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d '{"text": "疎通テスト from stock-trading"}' && echo "OK"

# 3. ログで通知の状態を確認
grep -E "slack|notification" /home/natsuki/stock-trading/logs/$(date +%Y-%m-%d).log | tail -20
# → "SLACK_WEBHOOK_URL not set — notification skipped" が出る場合は .env が読まれていない
```

### 6.3 DuckDB ロックエラー

別プロセスが `data/trading.duckdb` を掴んでいる場合に発生する。

```bash
# ロックを持っているプロセスを確認
lsof /home/natsuki/stock-trading/data/trading.duckdb 2>/dev/null
fuser /home/natsuki/stock-trading/data/trading.duckdb 2>/dev/null

# プロセス ID が判明した場合は終了（強制終了は最終手段）
kill <PID>
# または
kill -9 <PID>

# DuckDB の WAL ファイルが残っている場合は削除
ls /home/natsuki/stock-trading/data/trading.duckdb*
rm -f /home/natsuki/stock-trading/data/trading.duckdb.wal
```

### 6.4 DB が空 / データが取得されていない

```bash
# DBの最終更新日を確認
uv run python -c "
import duckdb
conn = duckdb.connect('/home/natsuki/stock-trading/data/trading.duckdb')
print(conn.execute('SELECT MAX(date) FROM ohlcv').fetchone())
conn.close()
"

# データ更新を手動実行
cd /home/natsuki/stock-trading
uv run python scripts/data_update.py

# 更新後にシグナル生成を手動実行
uv run python scripts/daily_signals.py
```

### 6.5 `daily_signals: DB空` エラー

```
daily_signals (YYYY-MM-DD): DB空 — data_update.py を先に実行
```

`data_update.py` が未実行または失敗している。[6.4](#64-db-が空--データが取得されていない) を参照。

### 6.6 GitHub Actions が失敗している

```bash
# ブラウザで Actions のログを確認
# https://github.com/<owner>/stock-trading/actions

# または gh CLI で確認
gh run list --limit 5
gh run view <run_id>
```

> GitHub Actions はランナーがクリーンな環境で起動するため、DuckDB のデータは実行間で保持されない。
> 本番運用は Linux サーバー上の cron を使用すること。

### 6.7 パリティ検証 FAIL

```bash
# 不一致シナリオとシンボルをログから確認
grep -E "FAIL|mismatch" /home/natsuki/stock-trading/logs/$(date +%Y-%m-%d).log

# 手動でパリティチェックを実行して詳細を確認
cd /home/natsuki/stock-trading
uv run python scripts/daily_signals.py --parity --dry-run

# テストを実行して原因を特定
uv run pytest tests/ -v -k "parity"
```

---

## 7. 環境変数・設定

### 7.1 環境変数

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `SLACK_WEBHOOK_URL` | 推奨 | Slack Incoming Webhook URL |
| `JQUANTS_API_KEY` | 任意 | J-Quants API（日本株決算データ） |

```bash
# .env の設定例
cp .env.example .env
# .env を編集
cat .env
# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
# JQUANTS_API_KEY=your_key_here
```

### 7.2 主要設定ファイル

| ファイル | 内容 |
|---------|------|
| `config/settings.yaml` | 資金・リスク・ユニバース設定 |
| `config/scenarios/s2.yaml` | S2（ブレイクアウト）パラメータ |
| `config/scenarios/s4.yaml` | S4（PEAD）パラメータ |
| `config/scenarios/s6.yaml` | S6（リバーション）パラメータ |

```bash
# 現在のリスク設定確認
grep -A10 "^risk:" /home/natsuki/stock-trading/config/settings.yaml
```

---

## 8. ログファイル一覧

| ファイルパス | 内容 | 備考 |
|------------|------|------|
| `logs/YYYY-MM-DD.log` | 日次バッチ全ログ（data_update + daily_signals） | 日付ごとに生成 |
| `logs/oos_YYYY-MM.log` | 月次 OOS レポートの実行ログ | 毎月1日生成 |
| `reports/oos_YYYY-MM.json` | OOS 検証レポート（JSON） | 毎月1日生成 |
| `results/backtest_*.json` | バックテスト結果 | 手動実行時 |

```bash
# 最近のログファイル一覧
ls -lt /home/natsuki/stock-trading/logs/ | head -10

# 特定日のエラー・警告のみ確認
grep -E "^.{19} \| (ERROR|WARNING)" /home/natsuki/stock-trading/logs/YYYY-MM-DD.log

# 過去のシグナル発生ログをまとめて確認
grep "approved:" /home/natsuki/stock-trading/logs/*.log
```

---

## 改訂履歴

| バージョン | 日付 | 変更内容 |
|----------|------|---------|
| 2.0 | 2026-06-09 | v1.7.0 対応で全面改訂（cron/setup_cron.sh・oos_report.py・Phase D移行基準・実際のログ形式を反映） |
| 1.0 | 2026-05-23 | 初版作成（Phase 4 完成時点） |
