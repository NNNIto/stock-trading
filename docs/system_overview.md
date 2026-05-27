# stock-trading システム全体構成

**バージョン:** 1.0  
**作成日:** 2026-05-28  
**対象:** /home/natsuki/stock-trading

---

## 1. プロジェクト概要

| 項目 | 内容 |
|------|------|
| 目的 | 日米株式の中期スイング戦略を自動化 |
| 対象市場 | S&P500 (US) ※JP は現在非推奨 |
| 初期資本 | 3,000,000 JPY |
| 運用方式 | シグナル自動生成 + 手動発注（paper trading モード） |
| バックテスト期間 | 2019–2024（6年） |
| 推奨構成の成績 | Total Return +49.57% / CAGR +6.94% / Sharpe 0.63 / Max DD -15.14% |

---

## 2. ディレクトリ構成

```
stock-trading/
├── config/
│   ├── settings.yaml              # グローバル設定（資本・手数料・リスク・マクロフィルタ）
│   ├── scenarios/
│   │   ├── s2.yaml                # S2: 52週高値ブレイクアウト
│   │   ├── s3.yaml                # S3: 押し目買い（無効化済み）
│   │   ├── s4.yaml                # S4: 決算後ドリフト（PEAD）
│   │   └── s6.yaml                # S6: 平均回帰
│   └── universe/
│       ├── nikkei225.csv          # 日経225 銘柄リスト
│       └── sp500.csv              # S&P500 銘柄リスト
│
├── src/
│   ├── data/                      # データ取得・DB管理・指標計算
│   ├── scenarios/                 # シナリオ実装（エントリー/エグジット条件）
│   ├── backtest/                  # バックテストエンジン・WF分析
│   ├── portfolio/                 # ライブポジション管理・サイジング
│   ├── validation/                # 過学習検出・先読みバイアス検出
│   ├── notification/              # Slack 通知
│   ├── reporting/                 # Streamlit ダッシュボード・レポート生成
│   └── utils/                     # 設定ローダー・ロギング・カレンダー
│
├── scripts/
│   ├── data_update.py             # データ更新バッチ（毎日 06:00 JST, cron）
│   ├── daily_signals.py           # シグナル生成 + Slack 通知（毎日 06:15 JST, cron）
│   ├── run_backtest.py            # バックテスト実行 CLI
│   ├── walkforward.py             # Walk-forward 分析 CLI
│   └── setup_cron.sh              # cron 登録スクリプト
│
├── data/
│   └── trading.duckdb             # メイン DB
│
├── results/                       # バックテスト結果 JSON（Git 追跡）
├── logs/                          # 実行ログ
├── tests/                         # テストスイート（329件）
├── docs/                          # ドキュメント
├── notebooks/                     # Jupyter notebooks
├── pyproject.toml
└── .env                           # 環境変数（Git 除外）
```

---

## 3. データフロー

```
【STEP 1】データ取得（data_update.py / 06:00 JST）
  YFinance [primary]
    → 失敗時: Stooq [fallback]
    → JP 決算のみ: J-Quants [fallback]
  取得内容: OHLCV / 決算データ / USD/JPY FX レート
  保存先: DuckDB (ohlcv, earnings, fx_rates テーブル)

【STEP 2】指標計算（daily_signals.py 内）
  DB から直近 400 日分 OHLCV を読み込み
  → add_indicators_batch() で以下を計算:
    MA (20/50/200) + スロープ
    RSI (2/14)
    ATR (14)
    出来高比率 (vol_ratio_20)
    5日・6ヶ月リターン
    52週高値 (high_252d)

【STEP 3】決算情報付加（earnings.py）
  → is_earnings_day / eps_surprise_pct / next_report_date を各行に結合

【STEP 4】シグナル生成（daily_signals.py / 06:15 JST）
  各シナリオ × 全銘柄でエントリー判定
  → 同銘柄競合時は優先度順に解決: S4 > S2 > S3 > S6
  → マクロフィルタ適用（VIX / 日経 DD / FOMC・BOJ 日）
  → オープンポジションのエグジット判定
  → DuckDB signals テーブルに idempotent upsert
  → Slack 通知送信

【STEP 5】手動発注
  Slack でシグナルを確認 → 証券会社で注文
  → 約定後に PortfolioManager で結果入力（paper モード）
```

---

## 4. シナリオ仕様

### 4.1 シナリオ一覧

| ID | 名称 | 有効 | 対象市場 | バージョン |
|----|------|------|--------|----------|
| S2 | 52週高値ブレイクアウト | ✓ | **US のみ** | v1.3.0 |
| S3 | 押し目買い（RSI(2) Connors 型） | ✗ | — | v2.1.0（永久無効） |
| S4 | 決算後ドリフト（PEAD） | ✓ | JP + US | v1.4.0 |
| S6 | 平均回帰（短期反発） | ✓ | JP + US | v1.3.0 |

S3 は S6 と条件が重複するため永久無効化。

---

### 4.2 S2 — 52週高値ブレイクアウト

**エントリー条件（全て同時満たす）:**

1. `close > high_252d.shift(1)` — 前日の52週高値を上抜け
2. `vol_ratio_20 >= 2.0` — 出来高が20日平均の2倍以上
3. `ma_200_slope > 0` — 200MA が上向き（20日前比）
4. `close > ma_200` — 200MA より上方
5. 上記を2営業日連続で確認（`breakout_confirm_days=2`）

**エグジット条件（最初にマッチした理由で決済）:**

| 優先度 | 理由 | 条件 |
|------|------|------|
| 1 | STOP_LOSS | close ≤ entry_price × 0.90 |
| 2 | TRAILING_STOP | close ≤ peak_price × 0.85 |
| 3 | TREND_REVERSAL | close < ma_20 かつ ma_20_slope < 0 |
| 4 | TIME_EXIT | 180営業日経過 |

**パフォーマンス（v1.3.0, US 2019–2024）:**
- 勝率: 52.7%  /  平均PnL: +3.47%  /  Sharpe: 0.62  /  Max DD: -14.5%
- JP では avg -1.18% のため `enabled_markets: [US]` で完全無効化

---

### 4.3 S4 — 決算後ドリフト（PEAD）

**エントリー条件（全て同時満たす）:**

決算日（エントリー2日前）の確認:
1. `is_earnings_day == True`
2. ギャップアップ `(open - prev_close) / prev_close >= 0.03`
3. 当日リターン `(close - prev_close) / prev_close >= 0.03`
4. `vol_ratio_20 >= 2.0`
5. `eps_surprise_pct > 0`（EPS サプライズ正）

エントリー当日の確認:
6. `close > ma_200`
7. `ma_200_slope > 0`

**エグジット条件:**

| 優先度 | 理由 | 条件 |
|------|------|------|
| 1 | STOP_LOSS | close ≤ entry_price × 0.90 |
| 2 | TAKE_PROFIT | close ≥ entry_price × 1.15 |
| 3 | TRAILING_STOP | close ≤ peak_price × 0.85 |
| 4 | PRE_EARNINGS | 次回決算まで 5 営業日以内 |
| 5 | TIME_EXIT | 60 営業日経過 |

---

### 4.4 S6 — 平均回帰（短期反発）

**エントリー条件（全て同時満たす）:**

1. `ret_5d <= -0.10` — 過去5日で10%以上の下落
2. `close > ma_200` — 長期上昇トレンド中
3. `vol_ratio_20 >= 2.0` — 売り圧力のクライマックス
4. `rsi_2 < 10` — 極端な売られすぎ

> **注意:** `return_threshold` を -0.10 より緩めると勝率が 82.6% → 63.0% に急落。絶対に緩めない。

**エグジット条件:**

| 優先度 | 理由 | 条件 |
|------|------|------|
| 1 | TAKE_PROFIT | close ≥ entry_price × 1.03 **または** rsi_2 ≥ 70 |
| 2 | STOP_LOSS | close ≤ entry_price × 0.93 |
| 3 | TIME_EXIT | 10 営業日経過 |

---

## 5. モジュール詳細

### 5.1 src/data/

| ファイル | 主要クラス・関数 | 役割 |
|---------|---------------|------|
| `fetcher.py` | `FallbackDataSource`, `YFinanceSource`, `StooqSource`, `JQuantsSource` | マルチプロバイダーデータ取得。Primary + Fallback + クロスチェック設計 |
| `repository.py` | `Repository` | DuckDB ORM。全テーブルの upsert（idempotent） |
| `indicators.py` | `add_indicators()`, `add_indicators_batch()` | pandas-ta を使った技術指標計算。分割・配当調整を正規化 |
| `earnings.py` | `enrich_with_earnings()` | 決算情報を OHLCV に結合。バイナリサーチで次回決算日を付加 |
| `quality.py` | `run_batch_quality_check()` | 欠損・外れ値・OHLC 矛盾の検出。日次リターン >30% は除外 |
| `universe.py` | `load_universe()` | CSV から銘柄リスト読み込み。流動性フィルタ対応 |

### 5.2 src/scenarios/

| ファイル | 内容 |
|---------|------|
| `base.py` | `ScenarioBase` 抽象基底クラス。`generate_signals()` / `get_exit_signal()` / `is_enabled_for_market()` インタフェース定義 |
| `s2_breakout.py` | S2 実装 |
| `s3_pullback.py` | S3 実装（無効） |
| `s4_pead.py` | S4 実装 |
| `s6_reversion.py` | S6 実装 |

### 5.3 src/backtest/

| ファイル | 主要クラス | 役割 |
|---------|----------|------|
| `engine.py` | `BacktestEngine` | 日次ループシミュレータ。ポジション追跡・コンフリクト解決・サーキットブレーカー |
| `execution.py` | `execute_buy()`, `execute_sell()` | スリッページ・手数料・FX コストを含む約定価格計算 |
| `metrics.py` | `compute_metrics()` | CAGR / Sharpe / Sortino / Max DD / Bootstrap CI / 勝率 / Profit Factor |
| `walkforward.py` | `WalkForwardRunner` | 12ヶ月 train + 3ヶ月 val のローリング窓でグリッドサーチ。Degradation ratio 算出 |

**BacktestEngine 1日の処理:**
```
1. 当日 OHLCV + 指標をロード
2. BUY シグナル生成 → pending_entries キューに追加
3. オープンポジションの EXIT 判定
4. マクロフィルタ適用（VIX > 35 でエントリーブロック）
5. コンフリクト解決（優先度順）
6. BUY 実行 → Fill オブジェクト生成
7. SELL 実行 → Fill オブジェクト生成
8. ピーク価格更新（トレイリングストップ用）
9. 時価評価（equity curve 記録）
```

### 5.4 src/portfolio/

| ファイル | 役割 |
|---------|------|
| `manager.py` | ライブポジションの CRUD（DuckDB positions テーブル）。paper / live モード切り替え |
| `sizer.py` | `FixedFractionSizer`: デフォルト 15%/ポジション（最大 20%） |
| `risk.py` | 最大ポジション数 7 / セクター集中制限 3 / ポートフォリオ DD サーキットブレーカー -20% |

### 5.5 src/validation/

| ファイル | 役割 |
|---------|------|
| `sanity_checker.py` | バックテスト結果の過学習検出（取引数 ≥ 30 / Sharpe ≤ 3.0 / 勝率 ≤ 80%） |
| `lookahead_detector.py` | 先読みバイアス検出（ウォームアップ期間・NaN ハンドリング検査） |
| `parity_checker.py` | 日次シグナルと実績の突合（リアル vs バックテスト乖離検出） |
| `overfitting_monitor.py` | WF degradation ratio 監視（≥ 0.5 が robust 基準） |

---

## 6. DuckDB テーブル定義

```sql
-- 価格データ（分割・配当調整済み）
CREATE TABLE ohlcv (
    symbol     VARCHAR NOT NULL,
    market     VARCHAR NOT NULL,   -- 'JP' | 'US'
    date       DATE    NOT NULL,
    open       DOUBLE  NOT NULL,
    high       DOUBLE  NOT NULL,
    low        DOUBLE  NOT NULL,
    close      DOUBLE  NOT NULL,
    adj_close  DOUBLE  NOT NULL,
    volume     BIGINT  NOT NULL,
    PRIMARY KEY (symbol, date)
);

-- 決算データ
CREATE TABLE earnings (
    symbol       VARCHAR NOT NULL,
    report_date  DATE,
    eps_actual   DOUBLE,
    eps_estimate DOUBLE,
    surprise_pct DOUBLE,
    PRIMARY KEY (symbol, report_date)
);

-- FX レート
CREATE TABLE fx_rates (
    pair  VARCHAR NOT NULL,   -- e.g., 'USDJPY'
    date  DATE    NOT NULL,
    rate  DOUBLE  NOT NULL,
    PRIMARY KEY (pair, date)
);

-- 銘柄マスタ
CREATE TABLE universe (
    symbol  VARCHAR PRIMARY KEY,
    market  VARCHAR NOT NULL,
    name    VARCHAR,
    sector  VARCHAR
);

-- 生成されたシグナル
CREATE TABLE signals (
    signal_id            VARCHAR PRIMARY KEY,
    generated_at         TIMESTAMP NOT NULL,
    scenario_id          VARCHAR NOT NULL,
    scenario_version     VARCHAR NOT NULL,
    symbol               VARCHAR NOT NULL,
    action               VARCHAR NOT NULL,   -- 'BUY'
    signal_date          DATE    NOT NULL,
    expected_entry_price DOUBLE,
    metadata             JSON,
    UNIQUE (symbol, signal_date, scenario_id, action)
);

-- ライブポジション（paper / live 共通）
CREATE TABLE positions (
    symbol         VARCHAR PRIMARY KEY,
    scenario_id    VARCHAR NOT NULL,
    market         VARCHAR NOT NULL,
    entry_date     DATE    NOT NULL,
    entry_price    DOUBLE  NOT NULL,
    quantity       INTEGER NOT NULL,
    current_price  DOUBLE,
    unrealized_pnl DOUBLE,
    stop_loss      DOUBLE,
    take_profit    DOUBLE,
    mode           VARCHAR DEFAULT 'paper',
    updated_at     TIMESTAMP NOT NULL
);

-- バックテスト完了トレード
CREATE TABLE trades (
    trade_id         VARCHAR PRIMARY KEY,
    scenario_id      VARCHAR,
    scenario_version VARCHAR,
    symbol           VARCHAR,
    market           VARCHAR,
    entry_date       DATE,
    entry_price      DOUBLE,
    exit_date        DATE,
    exit_price       DOUBLE,
    quantity         INTEGER,
    fees             DOUBLE,
    pnl              DOUBLE,
    pnl_pct          DOUBLE,
    holding_days     INTEGER,
    exit_reason      VARCHAR
);
```

---

## 7. scripts/ コマンドリファレンス

```bash
# データ更新（差分取得）
uv run python scripts/data_update.py --market US

# シグナル生成（dry-run）
uv run python scripts/daily_signals.py --dry-run

# バックテスト実行
uv run python scripts/run_backtest.py \
  --start 2019-01-01 --end 2024-12-31 \
  --market US \
  --out results/my_run.json

# Walk-forward 分析
uv run python scripts/walkforward.py --market US

# cron 登録
bash scripts/setup_cron.sh

# ダッシュボード起動
uv run streamlit run src/reporting/dashboard.py
```

---

## 8. テスト構成

```
tests/
├── data/          # fetcher / repository / indicators / earnings / quality / universe
├── scenarios/     # S2 / S3 / S4 / S6 / base（エントリー・エグジット条件のユニットテスト）
├── backtest/      # engine / execution / metrics / walkforward / snapshot（回帰テスト）
├── portfolio/     # manager / sizer / risk
├── validation/    # sanity / lookahead / parity
└── reporting/     # generator
```

```bash
uv run pytest                   # 全テスト実行（329件）
uv run pytest --cov=src         # カバレッジ付き（要件: ≥ 60%）
uv run pytest tests/scenarios/  # シナリオテストのみ
```

---

## 9. 設定パラメータ（config/settings.yaml 主要値）

| 設定 | 値 | 備考 |
|------|-----|------|
| 初期資本 | 3,000,000 JPY | |
| スリッページ | 0.2%（片道） | |
| 手数料 | 0.1%（往復） | |
| FX コスト | 0.5%（US 片道） | |
| 1ポジション上限 | 15%（最大 20%） | FixedFraction |
| 最大ポジション数 | 7 | |
| セクター集中上限 | 3 | |
| サーキットブレーカー | DD -20% | 新規エントリー停止 |
| VIX フィルタ | > 35 で全市場ブロック | |
| 日経 DD フィルタ | < -10% で JP ブロック | |
| WF 学習窓 | 12ヶ月 | |
| WF 検証窓 | 3ヶ月 | |

---

## 10. バックテスト結果サマリー（確定パラメータ）

| 構成 | Total Return | CAGR | Sharpe | Max DD | 備考 |
|------|-------------|------|--------|--------|------|
| **US のみ（推奨）** | +49.57% | **+6.94%** | **0.63** | -15.14% | S2 + S4 + S6 |
| JP のみ（S2 無効） | +6.12% | +1.00% | 0.37 | -3.69% | S4 + S6 のみ |
| JP + US 合算 | +34.66% | +5.09% | 0.34 | -15.48% | US の希薄化が発生 |

**Walk-forward 分析結果:**

| 市場 | val Sharpe 中央値 | Degradation | 判定 |
|------|-----------------|-------------|------|
| US | +0.105 | 0.331 | 軽度過学習（許容範囲） |
| JP | -0.189 | -0.704 | エッジなし（運用非推奨） |

---

## 11. 主要な設計判断

| 項目 | 決定内容 | 理由 |
|------|---------|------|
| DB | DuckDB | In-process OLAP、SQL 完全対応、CSV/Parquet native |
| シナリオ設計 | Strategy パターン（ABC） | 新規追加・テストが独立 |
| バックテスト方式 | 日次ループシミュレーション | ポジション追跡精度、実挙動への近似 |
| パラメータ最適化 | グリッドサーチ + WF | 再現性・解釈性を Bayesian より優先 |
| シグナル保存 | Idempotent upsert | 再実行安全性（cron 二重実行対応） |
| JP 市場 | 現在非推奨 | WF val Sharpe -0.189（汎化エッジなし） |
| S2 volume=2.0x | WF グリッド外だが採用 | 全期間で明確有効（勝率 46.3% → 52.7%） |
| breakout_confirm=2 | WF 推奨より保守的 | confirm=1 は全期間で壊滅的（-19.4%） |

---

## 12. 環境変数

| 変数 | 必須 | 用途 |
|------|------|------|
| `SLACK_WEBHOOK_URL` | 推奨 | シグナル・エグジット・エラー通知 |
| `STOOQ_API_KEY` | 任意 | yfinance フォールバック |
| `JQUANTS_API_KEY` | 任意 | JP 決算データ（J-Quants 有料） |

設定: `cp .env.example .env` → `.env` を編集

---

## 13. 日次運用フロー

```
06:00 [cron] data_update.py    — 前日終値 + FX 取得
06:15 [cron] daily_signals.py  — シグナル生成 + Slack 通知

06:15〜08:00 [手動] Slack でシグナル確認
08:00〜09:00 [手動] 楽天証券で発注（日本株）
23:30〜      [手動] 楽天証券で発注（米国株）

約定後 [手動] PortfolioManager で約定結果を入力:
  uv run python -c "
  from src.portfolio.manager import PortfolioManager
  from datetime import date
  with PortfolioManager() as pm:
      pm.open_position('AAPL', 'S2', 'US', date.today(), 178.5, 84, mode='paper')
  "
```

詳細は `docs/operations.md` を参照。

---

## 14. 関連ドキュメント

| ファイル | 内容 |
|---------|------|
| `docs/architecture.md` | アーキテクチャ設計書（詳細） |
| `docs/scenarios.md` | シナリオ仕様書（パラメータグリッド含む） |
| `docs/implementation_guide.md` | 実装手順書 |
| `docs/operations.md` | 運用手順・トラブルシューティング |
| `docs/todo_manual_setup.txt` | 手動セットアップ残作業 |
| `requirements.md` | 要件定義書 |
