# stock-trading システム仕様書

**S&P500 中期スイング自動売買システム**
`v1.5.0` | `Python 3.12` | `DuckDB` | ペーパートレード中 | 2026-06-04 更新

---

## 目次

- [システム概要](#システム概要)
- [パフォーマンス実績](#パフォーマンス実績)
- [ディレクトリ構成](#ディレクトリ構成)
- [データフロー](#データフロー)
- [データベース設計](#データベース設計duckdb)
- [シナリオ一覧](#シナリオ一覧)
- [S2: 52週高値ブレイクアウト](#s2--52週高値ブレイクアウト)
- [S4: 決算後ドリフト（PEAD）](#s4--決算後ドリフトpead)
- [S6: 平均回帰](#s6--平均回帰短期反発)
- [リスク管理](#リスク管理)
- [マクロフィルタ](#マクロフィルタ)
- [自動化・スケジューリング](#自動化スケジューリング)
- [Slack 通知](#slack-通知)
- [バックテスト](#バックテスト)
- [品質検証](#品質検証)
- [日次・週次運用](#日次週次運用)
- [改善ロードマップ](#改善ロードマップsharpe-063--10)
- [セットアップ](#セットアップ)

---

## システム概要

S&P500（米国株）を対象とした中期スイング戦略の自動化システム。複数のシグナルエンジン（ブレイクアウト・決算後ドリフト・平均回帰）を並行運用し、自動シグナル生成 + Slack 通知 + 手動発注の半自動フローで運用します。

| 項目 | 詳細 |
|------|------|
| 対象市場 | 米国株 S&P500（流動性上位 150 銘柄）※日経 225 は過学習のため現在非推奨 |
| 運用方式 | 自動シグナル生成 → Slack 通知 → **手動発注**（楽天証券）※本番移行後は API 統合予定 |
| 初期資本 | **300 万円**（ペーパートレード中）※本番移行時は 30 万円（10%）から段階的に開始 |
| 戦略タイプ | S2 ブレイクアウト、S4 PEAD、S6 平均回帰の3戦略を**優先度順**で統合運用 |

---

## パフォーマンス実績

バックテスト期間: **2019-01-01 〜 2024-12-31**（米国株）

| 指標 | 値 | 備考 |
|------|----|------|
| Total Return | **+49.6%** | 6年累積 |
| CAGR | **+6.94%** | 年率換算 |
| Sharpe | **0.63** | CI: -0.06〜1.26 |
| Max DD | **-15.1%** | 最大ドローダウン |
| Win Rate | **56.1%** | 全 123 件 |
| Profit Factor | **1.92** | — |
| 取引数/年 | **~20** | 平均シグナル |
| OOS Sharpe | **1.35** | 2023-2024 |

### 年次パフォーマンス

| 年 | リターン | Sharpe | 備考 |
|----|---------|--------|------|
| 2019 | +18.2% | 0.88 | — |
| 2020 | −5.3% | -0.42 | COVID |
| 2021 | +28.5% | 1.15 | — |
| 2022 | −18.1% | -0.65 | — |
| 2023 | +42.1% | 1.76 | OOS ✓ |
| 2024 | +15.2% | 1.12 | OOS ✓ |

### シナリオ別貢献度

| シナリオ | 取引数 | 勝率 | 平均 PnL | Sharpe | PnL 貢献 |
|---------|--------|------|---------|--------|---------|
| S2 52週高値ブレイクアウト | 55件 | 52.7% | +3.47% | 0.62 | 70% |
| S4 決算後ドリフト | 38件 | 47% | +0.68% | 0.12 | 13% |
| S6 平均回帰 | 30件 | 70% | +1.56% | 0.48 | 18% |

### Walk-Forward 検証

> ⚠️ **軽度過学習** — degradation_ratio 0.331（robust 基準: 0.5 以上）

| 指標 | IS（学習期間） | OOS（検証期間） |
|------|--------------|--------------|
| Sharpe 中央値 | 0.348 | 0.105 |
| 期間設定 | 12ヶ月 train + 3ヶ月 val（ローリング、20窓） | |
| OOS 判定開始 | 2025-01-01 | |

---

## ディレクトリ構成

```
stock-trading/
├── config/
│   ├── settings.yaml              # グローバル設定（資本・リスク・コスト）
│   └── scenarios/
│       ├── s2.yaml                # 52週高値ブレイクアウト
│       ├── s3.yaml                # 押し目買い（永久無効）
│       ├── s4.yaml                # 決算後ドリフト (PEAD)
│       └── s6.yaml                # 平均回帰
│
├── src/
│   ├── data/                      # データ取得・DB管理・指標計算
│   │   ├── fetcher.py             #   YFinance / Stooq / J-Quants
│   │   ├── repository.py          #   DuckDB ORM (idempotent upsert)
│   │   ├── indicators.py          #   pandas-ta 技術指標バッチ計算
│   │   ├── earnings.py            #   決算情報付加
│   │   ├── quality.py             #   データ品質チェック
│   │   └── universe.py            #   銘柄リスト管理
│   ├── scenarios/                 # シグナル生成（Strategy パターン）
│   ├── backtest/                  # バックテストエンジン・WF分析
│   │   ├── engine.py              #   日次シミュレーション・マクロフィルタ
│   │   ├── execution.py           #   約定価格計算（スリッページ・コスト）
│   │   ├── metrics.py             #   CAGR/Sharpe/Sortino/MaxDD 計算
│   │   └── walkforward.py         #   Walk-Forward グリッドサーチ
│   ├── portfolio/                 # ライブポジション管理
│   │   ├── manager.py             #   CRUD（paper/live モード）
│   │   ├── sizer.py               #   FixedFractionSizer（デフォルト15%）
│   │   └── risk.py                #   ポジション数・セクター・DD制限
│   ├── validation/                # バックテスト品質検証
│   ├── notification/slack.py      # Slack Block Kit 通知
│   ├── reporting/dashboard.py     # Streamlit ダッシュボード
│   └── agents/trading_agent.py   # オーケストレーター（dry_run対応）
│
├── scripts/
│   ├── data_update.py             # データ更新バッチ（06:00 JST）
│   ├── daily_signals.py           # シグナル生成 + 通知（06:15 JST）
│   ├── run_backtest.py            # バックテスト CLI
│   └── windows/                   # Windows タスクスケジューラ設定
│
├── data/trading.duckdb            # メイン OLAP DB（オンプロセス）
├── results/                       # バックテスト結果 JSON
├── tests/                         # テストスイート（~329件）
├── .github/workflows/             # CI / 自動データ更新
└── docs/                          # ドキュメント
```

---

## データフロー

### 毎日 06:00 JST — データ更新

1. **データ取得** — YFinance で前日終値 + FX レート取得。失敗時は Stooq → J-Quants にフォールバック
2. **品質チェック** — 欠損・外れ値・OHLC 矛盾を検出。日次リターン >30% は除外
3. **DB 保存** — DuckDB に idempotent upsert（再実行安全）

### 毎日 06:15 JST — シグナル生成

1. **OHLCV 読み込み** — DB から直近 400 日分のデータ取得
2. **指標計算** — MA(20/50/200) + スロープ、RSI(2/14)、ATR、vol_ratio、52週高値を一括計算
3. **決算情報付加** — earnings.py で EPS サプライズ・決算日を OHLCV に結合
4. **シグナル生成** — S2 / S4 / S6 × 全銘柄でエントリー判定。同銘柄競合時は優先度 S4 > S2 > S6 で解決
5. **マクロフィルタ** — VIX > 35 / 指数 MA200 下 / FOMC 日 でエントリーブロック
6. **DB 保存 + Slack 通知** — signals テーブルに idempotent upsert。新規シグナルを Slack に配信

### 手動発注フロー

1. **Slack 確認** — 銘柄・シナリオ・価格・指標詳細を確認
2. **証券会社で発注** — 楽天証券で注文（翌営業日始値約定が目安）
3. **PortfolioManager に入力** — 約定結果を DB に記録してポジション追跡開始

---

## データベース設計（DuckDB）

オンプロセス OLAP DB。セットアップ不要で SQL を完全サポート。ファイルパス: `data/trading.duckdb`

| テーブル | 主キー | 用途 |
|---------|--------|------|
| `ohlcv` | (symbol, date) | 分割・配当調整済み OHLCV（US / JP） |
| `earnings` | (symbol, report_date) | EPS 実績・予想・サプライズ率 |
| `fx_rates` | (pair, date) | USDJPY レート |
| `signals` | UNIQUE(symbol, signal_date, scenario_id, action) | 生成シグナル（idempotent） |
| `positions` | symbol | ライブポジション（paper / live モード） |
| `trades` | trade_id | 完了トレード（バックテスト・本番） |

### コスト設定（config/settings.yaml）

| 項目 | 値 | 説明 |
|------|-----|------|
| スリッページ | 0.2%（片道） | execution.py で約定価格に加算 |
| 手数料 | 0.1%（片道） | 往復で 0.2% |
| FX コスト | 0.5%（片道） | USD 建て注文時 |

---

## シナリオ一覧

| ID | 名称 | 状態 | 対象市場 | 勝率 | 平均 PnL | 優先度 |
|----|------|------|---------|------|---------|--------|
| S2 | 52週高値ブレイクアウト | ✅ 有効 | US | 52.7% | +3.47% | 2 |
| S3 | 押し目買い（RSI-2） | ❌ 永久無効 | — | — | — | — |
| S4 | 決算後ドリフト（PEAD） | ✅ 有効 | US | 47% | +0.68% | 1（最高） |
| S6 | 平均回帰（短期反発） | ✅ 有効 | US | 70% | +1.56% | 3 |

> ℹ️ **S3 無効化の理由:** S6 と条件が重複し、統計的にエッジなし。
>
> ⚠️ **JP 市場:** WF degradation_ratio -0.704（基準 0.5 未達）のため全シナリオ非推奨。

---

## S2 — 52週高値ブレイクアウト

`v1.3.0` | US のみ | 中期トレンドフォロー

### エントリー条件（全て同時成立）

```python
1. close > high_252d.shift(1)     # 前日の52週高値を上抜け
2. vol_ratio_20 >= 2.0            # 出来高が20日平均の2倍以上
3. ma_200_slope > 0               # 200MA が上向き（20日前比）
4. close > ma_200                 # 200MA より上方
5. 上記を 2 営業日連続確認         # フェイクブレイク対策
```

### エグジット条件（最初にマッチ）

| 条件 | ルール |
|------|--------|
| STOP_LOSS | `close ≤ entry × 0.90` |
| TRAILING_STOP | `close ≤ peak × 0.85` |
| TREND_REVERSAL | `close < MA20` かつ `slope < 0` |
| TIME_EXIT | 180 営業日経過 |

### 主要パラメータ

| パラメータ | 値 | 備考 |
|-----------|-----|------|
| high_lookback_days | 252 | 52週（営業日） |
| volume_multiplier | 2.0 | v1.2→1.3 で勝率 46→53% に改善した重要パラメータ |
| breakout_confirm_days | 2 | フェイクブレイク対策 |
| stop_loss_pct | -10% | |
| trailing_stop_pct | -15%（高値から） | |
| time_exit_days | 180 | |

---

## S4 — 決算後ドリフト（PEAD）

`v1.5.0` | US のみ | 最優先シナリオ

### エントリー条件

決算日を T 日とした場合、以下をすべて確認:

```python
# T 日（決算当日）
is_earnings_day == True
(open - prev_close) / prev_close >= 0.03    # ギャップアップ +3%
(close - prev_close) / prev_close >= 0.03   # 当日リターン +3%
vol_ratio_20 >= 2.0
eps_surprise_pct > 0                         # EPS サプライズ 正

# T+2 日（エントリー当日）
close > ma_200
ma_200_slope > 0
```

### エグジット条件

| 条件 | ルール |
|------|--------|
| STOP_LOSS | `close ≤ entry × 0.90` |
| TAKE_PROFIT | `close ≥ entry × 1.15` |
| TRAILING_STOP | `close ≤ peak × 0.85` |
| PRE_EARNINGS | 次回決算まで 5 営業日以内 |
| TIME_EXIT | 60 営業日経過 |

---

## S6 — 平均回帰（短期反発）

`v1.4.0` | US のみ | 勝率 70%

> ⚠️ **重要:** `return_threshold` を `-0.10` から `-0.07` に緩めると勝率が **82.6% → 63.0%** に急落。絶対に変更禁止。

### エントリー条件（全て同時成立）

```python
1. ret_5d <= -0.10       # 過去5日で -10% 以上の下落 ← 変更禁止
2. close > ma_200        # 長期上昇トレンド中
3. vol_ratio_20 >= 2.0   # 売り圧力のクライマックス
4. rsi_2 < 10            # 極端な売られすぎ
```

### エグジット条件

| 条件 | ルール |
|------|--------|
| TAKE_PROFIT | `close ≥ entry × 1.03` または `rsi_2 ≥ 70` |
| STOP_LOSS | `close ≤ entry × 0.93` |
| TIME_EXIT | 10 営業日経過 |

---

## リスク管理

### ポジションサイジング

- デフォルト: 資本の 15% / ポジション
- 最大: 資本の 20% / ポジション
- 最大ポジション数: 7

### 集中リスク制限

- 同一セクター: 最大 3 ポジション
- DD サーキットブレーカー: -20%
- VIX フィルタ: 35 超でエントリー停止

---

## マクロフィルタ

以下の条件を満たす場合、新規エントリーを自動ブロック:

| フィルタ | 閾値 | 対象市場 |
|---------|------|---------|
| VIX 恐怖指数 | > 35 | US |
| ポートフォリオ DD | < -20% | 全市場 |
| 指数 MA200 下方 | — | 全市場 |
| 日経 ドローダウン | < -10% | JP（現在非推奨） |
| FOMC / BOJ 開催日 | — | ブラックアウト |

---

## 自動化・スケジューリング

### GitHub Actions（CI / データ更新）

**ci.yml** — push / PR → main で自動実行
- ruff lint
- mypy 型チェック
- pytest（カバレッジ 60%+）

**daily_update.yml** — 平日 JST 08:30 / 23:30 で実行
```
UTC 23:30 日〜木 → JST 翌朝 08:30
UTC 14:30 月〜金 → JST 当日 23:30
```

> ⚠️ GitHub Actions ランナーはクリーンな環境で起動するため、DuckDB データは実行間で保持されません。本番環境には Linux サーバー上の cron を推奨。

### 本番 cron（Linux サーバー）

```bash
# cron 登録
bash scripts/setup_cron.sh

# 登録内容
0  6 * * 1-5  uv run python scripts/data_update.py
15 6 * * 1-5  uv run python scripts/daily_signals.py
```

### Windows タスクスケジューラ

`scripts/windows/` 配下のスクリプトを使用:

```
scripts/windows/
├── setup_task_scheduler.ps1    # PowerShell でタスク一括登録
├── gen_register.py             # XML からタスク生成
├── StockDailySignals.xml       # シグナル生成タスク定義
├── StockDataUpdate.xml         # データ更新タスク定義
├── StockEarningsJP.xml         # 日本株決算タスク定義
└── StockEarningsUS.xml         # 米国株決算タスク定義
```

---

## Slack 通知

### 設定方法

```bash
# .env ファイルに追記
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../W...
```

### 通知タイプ一覧

| 通知種別 | 発信元 | 内容 |
|---------|--------|------|
| 新規シグナル | daily_signals.py | 銘柄・シナリオ・指標詳細（Block Kit） |
| エグジット推奨 | daily_signals.py | エグジット理由・含み損益（Block Kit） |
| 日次サマリー | daily_signals.py | シグナル数・保有数・評価額 |
| サーキットブレーカー | BacktestEngine | ポートフォリオ DD -20% アラート |
| 注文記録 | TradingAgent | 銘柄・株数・単価・投入額・モード |
| エラー通知 | 各種スクリプト | テキスト（GitHub Actions から） |

---

## バックテスト

### 実行コマンド

```bash
# フル期間バックテスト
uv run python scripts/run_backtest.py \
  --start 2019-01-01 --end 2024-12-31 \
  --market US \
  --out results/my_run.json

# Walk-Forward 分析
uv run python scripts/walkforward.py --market US

# Streamlit ダッシュボード
uv run streamlit run src/reporting/dashboard.py
```

### BacktestEngine — 1日の処理フロー

1. **OHLCV + 指標ロード** — 当日のデータを DuckDB から取得
2. **BUY シグナル生成** — 各シナリオ × 全銘柄 → pending_entries キュー
3. **EXIT 判定** — オープンポジションのエグジット理由を確認
4. **マクロフィルタ + コンフリクト解決** — VIX / DD チェック。同銘柄競合は S4 > S2 > S6 で優先度解決
5. **BUY / SELL 実行** — スリッページ・手数料・FX コストを含む Fill オブジェクト生成
6. **時価評価** — equity curve 更新。ピーク価格更新（トレイリングストップ用）

---

## 品質検証

| モジュール | 検証内容 | 基準 |
|-----------|---------|------|
| `sanity_checker.py` | 過学習検出 | 取引数 ≥ 30 / Sharpe ≤ 3.0 / 勝率 ≤ 80% |
| `lookahead_detector.py` | 先読みバイアス検出 | ウォームアップ期間・NaN ハンドリング検査 |
| `parity_checker.py` | シグナル乖離検出 | 日次シグナル vs バックテストの整合性（月曜自動実行） |
| `overfitting_monitor.py` | WF degradation 監視 | degradation_ratio ≥ 0.5 で robust 判定 |

### テストスイート

```bash
uv run pytest                    # 全テスト（~329件）
uv run pytest --cov=src          # カバレッジ付き（要求: 60%+）
uv run pytest tests/scenarios/   # シナリオテストのみ
```

---

## 日次・週次運用

### 日次フロー

| 時刻 (JST) | 作業 | 手動 / 自動 |
|-----------|------|-----------|
| 06:00 | data_update.py 実行 → DB 更新 | 自動 |
| 06:15 | daily_signals.py 実行 → Slack 通知 | 自動 |
| 08:00〜09:00 | Slack でシグナル確認 → 楽天証券で発注 | 手動 |
| 翌営業日 | 約定結果を PortfolioManager に入力 | 手動 |

### 週次チェックリスト

- [ ] parity check の結果確認（logs/daily_signals.log）
- [ ] オープンポジションの含み損益確認
- [ ] DD が -20% を超えていないか確認
- [ ] 先週のシグナル精度振り返り

### 月次チェックリスト

- [ ] 月次パフォーマンス集計
- [ ] バックテスト再実行（IS 期間）
- [ ] WF degradation ratio 確認（基準: 0.5 以上）
- [ ] 最大 DD 確認（閾値: -20%）

### ペーパートレード → 本番の移行条件

- [ ] ペーパートレード期間: 2 週間以上
- [ ] シグナル件数: 5 件以上で勝率 40% 以上
- [ ] システム異常なし（cron / Slack / DB 全て正常）
- [ ] Sharpe ≥ 0.6（現状 0.63 → 達成済）
- [ ] 初期資金 30 万円（300 万の 10%）から段階的に開始

---

## 改善ロードマップ（Sharpe 0.63 → 1.0）

| フェーズ | 施策 | 期待効果 | 時期 | 状態 |
|--------|------|---------|------|------|
| A | Universe 拡大（150 → 200 銘柄） | シグナル +35%、Sharpe +0.15 | 6/16〜 | 予定 |
| B | S6 RSI 緩和（oversold 10 → 15） | S6 シグナル +60%、品質維持 | 6/20〜 | 予定 |
| C | S4 EPS サプライズ強化（0% → 5%） | S4 品質向上（avg PnL +0.68% → +2%） | 6/25〜 | 予定 |
| D | 少額本番デビュー（30 万円） | 実績検証 | 7 月〜 | 検討中 |

### 各施策の変更箇所

```yaml
# Phase A: Universe 拡大
# config/settings.yaml
universe_filter:
  us_top_n: 200    # 150 → 200

# Phase B: S6 RSI 緩和
# config/scenarios/s6.yaml
parameters:
  rsi_oversold: 15    # 10 → 15

# Phase C: S4 EPS サプライズ強化
# config/scenarios/s4.yaml
parameters:
  surprise_threshold_pct: 0.05    # 0.0 → 0.05
```

---

## セットアップ

### 必須環境変数（.env）

```bash
# Slack 通知（推奨）
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# J-Quants（JP 決算データ、任意）
JQUANTS_API_KEY=...

# Stooq（フォールバック、任意）
STOOQ_API_KEY=...
```

### 初期セットアップ手順

```bash
# 1. リポジトリクローン
git clone https://github.com/NNNIto/stock-trading.git
cd stock-trading

# 2. uv インストール（未インストール時）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. 依存関係インストール
uv sync --extra dev

# 4. 環境変数設定
cp .env.example .env
# .env を編集して各種キーを設定

# 5. テスト実行
uv run pytest --cov=src

# 6. cron 登録（Linux 本番環境）
bash scripts/setup_cron.sh
```

### 手動ポジション入力（約定後）

```python
uv run python -c "
from datetime import date
from src.portfolio.manager import PortfolioManager
with PortfolioManager() as pm:
    pm.open_position('AAPL', 'S2', 'US', date.today(), 178.5, 84, mode='paper')
"
```

### トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| Slack に「DB 空」エラー | data_update が失敗 | `uv run python scripts/data_update.py` を手動実行 |
| シグナルが連日 0 件 | 指数が 200MA 下（マクロフィルタ） | 正常。市場回復を待つ |
| parity check FAIL | シナリオロジックに不整合 | ログ確認してバグ修正 |
| Slack 通知が来ない | SLACK_WEBHOOK_URL の問題 | .env の値を確認 |
| カバレッジ < 60% | 新規パスが増加 | テストケース追加 |

---

*stock-trading システム仕様書 | 最終更新: 2026-06-04 | v1.5.0*
