# アーキテクチャ設計書

**バージョン:** 1.0
**作成日:** 2026-05-16
**ステータス:** ドラフト
**関連文書:** requirements.md, scenarios.md

---

## 1. 設計方針

### 1.1 設計原則
- **単一責任の原則:** 各モジュールは1つの責務のみを持つ
- **依存方向の制約:** データ層 → ロジック層 → アプリ層（逆流禁止）
- **設定とコードの分離:** パラメータはYAML、ロジックはPythonコード
- **テスト可能性:** 各シナリオは入力データから決定的に出力を生成（純粋関数化）
- **自作優先:** バックテストエンジンは挙動を完全理解できる自作実装
- **再現性:** 乱数シード固定、データバージョン管理

### 1.2 技術スタック（確定版）

| レイヤ | 採用技術 |
|--------|---------|
| 言語 | Python 3.11+ |
| パッケージ管理 | uv |
| データストア | DuckDB |
| データ操作 | Polars（メイン）、pandas（補助） |
| データ取得 | yfinance |
| テクニカル指標 | pandas-ta |
| 設定管理 | YAML + Pydantic |
| ロギング | loguru |
| ダッシュボード | Streamlit |
| グラフ | Plotly |
| 探索分析 | Jupyter Notebook |
| テスト | pytest |
| 型チェック | mypy（重要モジュールのみ） |
| スケジューラ | cron（Linux） |
| 通知 | Slack Webhook |
| OS | Linux |

---

## 2. システム全体構成

### 2.1 レイヤ構成

```
┌─────────────────────────────────────────────┐
│  アプリ層（Application Layer）                 │
│  ・Streamlitダッシュボード                       │
│  ・CLI（バックテスト・日次バッチ実行）              │
│  ・通知（Slack）                                │
└─────────────────────────────────────────────┘
              ▲
              │
┌─────────────────────────────────────────────┐
│  ロジック層（Domain Layer）                     │
│  ・シナリオエンジン（S2/S3/S4/S6）              │
│  ・バックテストエンジン                          │
│  ・ポートフォリオマネージャ                       │
│  ・リスクマネージャ                              │
│  ・評価エンジン                                  │
└─────────────────────────────────────────────┘
              ▲
              │
┌─────────────────────────────────────────────┐
│  データ層（Data Layer）                         │
│  ・データ取得（yfinance）                        │
│  ・データ品質チェック                            │
│  ・DuckDB（OHLCV・決算・為替・メタデータ）       │
│  ・指標計算（pandas-ta）                         │
└─────────────────────────────────────────────┘
```

### 2.2 データフロー

**バックテストモード**
```
[yfinance API]
    ↓ 日次取得
[Raw Data: DuckDB]
    ↓ クレンジング・コーポレートアクション調整
[Cleaned OHLCV]
    ↓ テクニカル指標計算
[Features Table]
    ↓ シナリオ評価
[Signals Table]
    ↓ バックテスト実行（約定・PnL計算）
[Trades Table]
    ↓ ポートフォリオ集約
[Equity Curve]
    ↓ 評価指標算出
[Performance Report]
```

**日次運用モード**
```
[cron 毎日 6:00 JST]
    ↓
[データ取得（前日終値）]
    ↓
[指標計算]
    ↓
[各シナリオシグナル生成]
    ↓
[ポートフォリオ状態と照合・競合解決]
    ↓
[シグナル出力（CSV + Slack通知）]
    ↓
[人間が確認 → 楽天証券で手動発注]
    ↓
[約定結果を手動入力]
    ↓
[ポートフォリオ状態更新]
```

---

## 3. モジュール構成

### 3.1 ディレクトリ構造

```
stock-trading/
├── .gitignore
├── .env.example
├── README.md
├── pyproject.toml
├── requirements.md
├── docs/
│   ├── architecture.md          # 本ドキュメント
│   ├── scenarios.md
│   └── operations.md            # 運用手順書（今後）
├── config/
│   ├── settings.yaml            # グローバル設定
│   ├── scenarios/
│   │   ├── s2.yaml
│   │   ├── s3.yaml
│   │   ├── s4.yaml
│   │   └── s6.yaml
│   └── universe/
│       ├── nikkei225.csv        # 銘柄リスト
│       └── sp500.csv
├── src/
│   ├── __init__.py
│   ├── data/
│   │   ├── __init__.py
│   │   ├── fetcher.py           # yfinance ラッパー
│   │   ├── quality.py           # データ品質チェック
│   │   ├── repository.py        # DuckDB I/O
│   │   ├── universe.py          # 銘柄ユニバース管理
│   │   └── indicators.py        # テクニカル指標計算
│   ├── scenarios/
│   │   ├── __init__.py
│   │   ├── base.py              # シナリオ抽象クラス
│   │   ├── s2_breakout.py
│   │   ├── s3_pullback.py
│   │   ├── s4_pead.py
│   │   └── s6_reversion.py
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── engine.py            # バックテストエンジン本体
│   │   ├── execution.py         # 約定モデル（スリッページ・手数料）
│   │   ├── walkforward.py       # ウォークフォワード分析
│   │   └── metrics.py           # 評価指標算出
│   ├── portfolio/
│   │   ├── __init__.py
│   │   ├── manager.py           # ポートフォリオ状態管理
│   │   ├── sizer.py             # ポジションサイズ計算
│   │   └── risk.py              # リスクマネージャ
│   ├── reporting/
│   │   ├── __init__.py
│   │   ├── generator.py         # レポート生成
│   │   └── dashboard.py         # Streamlitアプリ
│   ├── notification/
│   │   ├── __init__.py
│   │   └── slack.py             # Slack通知
│   ├── validation/              # 品質保証 L3/L4（7-A節）
│   │   ├── __init__.py
│   │   ├── lookahead_detector.py
│   │   ├── metrics_validator.py
│   │   ├── execution_realism_checker.py
│   │   ├── survivorship_checker.py
│   │   ├── sanity_checker.py
│   │   └── overfitting_monitor.py
│   ├── agents/                  # 品質保証 L2/L3軽量運用（7-A節）
│   │   ├── code_reviewer.md
│   │   ├── finance_validator.md
│   │   └── review_checklist.md
│   └── utils/
│       ├── __init__.py
│       ├── config.py            # 設定読み込み（Pydantic）
│       ├── logger.py            # ロガー設定
│       └── calendar.py          # 営業日カレンダー
├── tests/
│   ├── data/
│   ├── scenarios/
│   ├── backtest/
│   ├── validation/
│   └── portfolio/
├── .pre-commit-config.yaml      # 静的品質ゲート L1（7-A節）
├── notebooks/
│   ├── 01_data_exploration.ipynb
│   ├── 02_scenario_analysis.ipynb
│   └── 03_backtest_results.ipynb
├── scripts/
│   ├── daily_signals.py         # 日次シグナル生成（cron用）
│   ├── data_update.py           # データ更新バッチ
│   ├── run_backtest.py          # バックテスト実行CLI
│   └── walkforward.py           # ウォークフォワード実行
└── data/                        # .gitignore対象
    ├── raw/
    ├── processed/
    └── trading.duckdb
```

### 3.2 主要モジュール責務

#### データ層（src/data/）

**fetcher.py（データソース抽象化レイヤ）**
- データソースを抽象インタフェース化し、複数プロバイダを切替・フォールバック可能にする
- `DataSource` 抽象クラス：`fetch_ohlcv()`, `fetch_fx()`, `fetch_earnings()`
- 実装プロバイダ：
  - `YFinanceSource`（プライマリ、無料、日米両対応）
  - `StooqSource`（フォールバック1、無料）
  - `JQuantsSource`（フォールバック2、日本株、要登録）
  - `AlphaVantageSource`（フォールバック3、米国株、要APIキー）
- フォールバック方針：
  - プライマリが3回リトライ失敗 → 次のソースに自動切替
  - 切替発生時はWARNINGログ＋Slack通知
  - ソースごとにデータ形式差異を正規化（共通スキーマに変換）
- データソース整合性チェック：
  - 同一銘柄・同一日付で複数ソースの値が大きく乖離（例：終値±2%超）した
    場合は警告（どちらかが異常値の可能性）
- レート制限対応（リトライ・バックオフ）
- 入力：銘柄リスト、期間
- 出力：Polars DataFrame（プロバイダ非依存の共通スキーマ）
- 設定：`config/settings.yaml` の `data.sources` でプライマリ・フォールバック順を定義

**quality.py**
- 欠損値検出
- 外れ値検出（前日比+/-50%超など）
- 整合性チェック（high >= low、close ∈ [low, high]）
- 出力：品質レポート＋クレンジング済データ

**repository.py**
- DuckDBへの読み書き
- テーブル定義・マイグレーション
- クエリビルダー
- 出力：型付きデータ

**indicators.py**
- 移動平均、RSI、ATR、出来高平均等を計算
- pandas-taラッパー
- 入力：OHLCV DataFrame
- 出力：指標列を追加したDataFrame

**universe.py**
- 日経225、S&P500銘柄リスト管理
- 構成銘柄変更履歴（生存者バイアス回避）

#### ロジック層（src/scenarios/）

**base.py**
```python
class ScenarioBase(ABC):
    @abstractmethod
    def generate_signals(self, data: pl.DataFrame) -> pl.DataFrame:
        """指標付きデータから売買シグナルを生成"""
        pass

    @abstractmethod
    def get_exit_signal(self, position: Position, current_data: pl.DataFrame) -> bool:
        """既存ポジションのエグジット判定"""
        pass
```

各シナリオ（s2_breakout.py等）はこれを継承し、YAMLからパラメータをロード。

#### ロジック層（src/backtest/）

**engine.py**
- 日次ループでシナリオを実行
- シグナル→約定→PnL更新の流れを管理
- ベクトル化可能な処理はPolarsで高速化

**execution.py**
- 約定モデル：翌営業日始値約定が基本
- スリッページ：0.2%（売買コスト共通設定から取得）
- 手数料：往復0.1%
- 為替コスト：米国株のみ片道0.5%（買付・売却の両替それぞれに発生、往復で実質1.0%）

**walkforward.py**
- ローリングウィンドウで学習・検証
- 学習期間でパラメータ最適化（gridsearch）
- 検証期間で評価
- アウトオブサンプル期間は別途実行

**metrics.py**
- CAGR、シャープ、ソルティノ、最大DD、勝率等を算出
- ブートストラップで信頼区間
- ベンチマーク比較

#### ロジック層（src/portfolio/）

**manager.py**
- 現在ポジション・キャッシュ・エクスポージャー管理
- ポジションYAMLへ永続化（手動発注後に更新）

**sizer.py**
- MVP段階：元本×15%固定
- 将来：案2（シナリオ別）、案3（ATRベース）

**risk.py**
- ポートフォリオDDチェック
- セクター集中チェック
- マクロイベントフィルタ（VIX、日経下落率）

#### アプリ層

**reporting/dashboard.py**
- Streamlitダッシュボード
- 現在ポジション、シナリオ別パフォーマンス、予実比較

**scripts/daily_signals.py**
- cronから毎日朝6時に実行
- データ更新 → シグナル生成 → Slack通知

---

## 4. データベース設計（DuckDB）

### 4.1 テーブル一覧

| テーブル | 用途 |
|----------|------|
| ohlcv | 日次OHLCV履歴 |
| earnings | 決算データ |
| fx_rates | 為替レート |
| universe | 銘柄マスター |
| sector_map | 銘柄-セクター対応 |
| signals | シナリオ生成シグナル履歴 |
| trades | バックテスト・実運用の取引履歴 |
| positions | 現在ポジション（運用用） |
| equity_curve | エクイティカーブ履歴 |

### 4.2 主要テーブル定義

**ohlcv**
```sql
CREATE TABLE ohlcv (
    symbol VARCHAR NOT NULL,
    market VARCHAR NOT NULL,           -- 'JP' or 'US'
    date DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    adj_close DOUBLE NOT NULL,         -- 配当・分割調整済
    volume BIGINT NOT NULL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX idx_ohlcv_date ON ohlcv(date);
```

**signals**
```sql
CREATE TABLE signals (
    signal_id VARCHAR PRIMARY KEY,
    generated_at TIMESTAMP NOT NULL,
    scenario_id VARCHAR NOT NULL,      -- 'S2', 'S3', 'S4', 'S6'
    scenario_version VARCHAR NOT NULL, -- Git commit hash
    symbol VARCHAR NOT NULL,
    action VARCHAR NOT NULL,           -- 'BUY' or 'SELL'
    signal_date DATE NOT NULL,
    expected_entry_price DOUBLE,
    metadata JSON                       -- 各シナリオの判断根拠
);
```

**trades**
```sql
CREATE TABLE trades (
    trade_id VARCHAR PRIMARY KEY,
    mode VARCHAR NOT NULL,             -- 'backtest', 'paper', 'live'
    scenario_id VARCHAR NOT NULL,
    scenario_version VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    entry_date DATE NOT NULL,
    entry_price DOUBLE NOT NULL,
    exit_date DATE,
    exit_price DOUBLE,
    quantity INTEGER NOT NULL,
    fees DOUBLE NOT NULL,
    pnl DOUBLE,
    pnl_pct DOUBLE,
    holding_days INTEGER,
    exit_reason VARCHAR,               -- 'stop_loss', 'take_profit', 'time_exit', etc.
    notes VARCHAR
);
```

**positions**
```sql
CREATE TABLE positions (
    symbol VARCHAR PRIMARY KEY,
    scenario_id VARCHAR NOT NULL,
    entry_date DATE NOT NULL,
    entry_price DOUBLE NOT NULL,
    quantity INTEGER NOT NULL,
    current_value DOUBLE,
    unrealized_pnl DOUBLE,
    stop_loss DOUBLE,
    take_profit DOUBLE,
    updated_at TIMESTAMP NOT NULL
);
```

### 4.3 データ管理方針
- DuckDBファイルは `data/trading.duckdb`
- Git管理外（容量大、再取得可能）
- バックアップ：週次でローカルディスクの別ディレクトリへコピー
- 履歴データは10年保持

---

## 5. 設定ファイル設計

### 5.1 グローバル設定 `config/settings.yaml`

```yaml
project:
  name: stock-trading
  capital_jpy: 3000000  # 想定元本

execution:
  slippage_pct: 0.002
  commission_pct: 0.001
  fx_cost_pct: 0.005    # 米国株のみ、片道（両替ごとに適用、往復で実質1.0%）

risk:
  max_position_pct: 0.20       # 1銘柄最大20%
  default_position_pct: 0.15   # MVP固定サイズ
  max_positions: 7
  max_sector_concentration: 3
  portfolio_dd_circuit_breaker: -0.20

macro_filters:
  vix_threshold: 35
  nikkei_drawdown_threshold: -0.10
  fomc_pause_days: 1
  boj_pause_days: 1

backtest:
  learning_window_months: 12
  validation_window_months: 3
  walkforward_step_months: 3
  out_of_sample_start: "2025-01-01"
  random_seed: 42

data:
  start_date: "2018-01-01"
  universe:
    jp: nikkei225
    us: sp500
  sources:
    primary: yfinance
    fallback_order:
      - stooq
      - jquants      # 日本株フォールバック（要登録）
      - alphavantage  # 米国株フォールバック（要APIキー）
    retry_attempts: 3
    cross_check_tolerance_pct: 0.02  # ソース間乖離許容（終値±2%）
    cross_check_enabled: true

notification:
  slack_webhook_env: SLACK_WEBHOOK_URL
  alert_on:
    - new_signal
    - stop_loss_triggered
    - circuit_breaker
```

### 5.2 シナリオ設定 `config/scenarios/s2.yaml`（例）

```yaml
scenario_id: S2
name: "52週高値ブレイクアウト"
enabled: true
version: "1.0.0"

parameters:
  high_lookback_days: 252
  volume_multiplier: 1.5
  trend_ma_days: 200
  stop_loss_pct: -0.08
  trailing_stop_pct: -0.15
  time_exit_days: 180

change_log:
  - version: "1.0.0"
    date: "2026-05-16"
    reason: "初期パラメータ設定（推奨値採用）"
```

---

## 6. 命名規則・コーディング規約

### 6.1 命名規則
- ファイル：`snake_case.py`
- クラス：`PascalCase`
- 関数・変数：`snake_case`
- 定数：`UPPER_SNAKE_CASE`
- プライベート：先頭アンダースコア `_method`

### 6.2 型ヒント
- 全公開関数に型ヒント必須
- 重要モジュール（scenarios, backtest）はmypy通過必須
- DataFrame型は `pl.DataFrame` または `pd.DataFrame` を明示

### 6.3 ログ規約
- loguruを使用
- ログレベル：DEBUG（詳細）、INFO（運用イベント）、WARNING（注意）、ERROR（要対応）
- シグナル生成・売買判断は必ずINFO以上で記録
- ログファイル：`logs/YYYY-MM-DD.log`（日次ローテーション）

### 6.4 エラーハンドリング
- データ取得失敗：3回リトライ後アラート
- シグナル生成失敗：該当シナリオのみスキップ、他は継続
- ポートフォリオ更新失敗：処理停止、要手動確認

---

## 7. テスト戦略

### 7.1 テスト範囲
| 対象 | テストレベル | 必須度 |
|------|-----------|--------|
| シナリオロジック | 単体テスト | 必須 |
| バックテストエンジン | 単体＋結合テスト | 必須 |
| データクレンジング | 単体テスト | 必須 |
| ポートフォリオマネージャ | 単体テスト | 必須 |
| ダッシュボード | 手動テスト | 任意 |

### 7.2 シナリオテストの考え方
- 既知のチャートパターンを再現したサンプルデータでシグナル生成をテスト
- 例：S2なら「明らかに52週高値ブレイクしたデータ」でBUYシグナルが出ることを確認
- スナップショットテスト：バックテスト結果のハッシュ値を保存し、リファクタ時に変化検知

### 7.3 カバレッジ目標
- 全体：60%以上
- シナリオロジック：90%以上
- バックテストエンジン：80%以上

---

## 7-A. 品質保証・検証アーキテクチャ

### 7-A.1 設計思想

最優先事項「過学習回避」と「バックテスト精度」を、人間の目視レビューだけに
依存せず、機械的・自動的に担保する。実装エージェントの盲点（自己レビューの
限界）を、独立した検証層でカバーする。

品質保証は4レイヤ構成。検出可能なものはプログラム化し、判断を要するものは
軽量なエージェント運用（専用プロンプト＋チェックリスト＋別セッション）とする。

### 7-A.2 4レイヤ構成

| レイヤ | 名称 | 実装形態 | 主目的 |
|--------|------|---------|--------|
| L1 | 静的品質ゲート | プログラム（pre-commit/CI） | lint・型・テスト・カバレッジの機械チェック |
| L2 | コードレビューエージェント | 軽量運用（別セッション） | 設計の歪み・規約違反・バグの批判的レビュー |
| L3 | 金融ロジック検証 | プログラム＋軽量運用 | 先読みバイアス等の金融特有の罠検出 |
| L4 | バックテスト健全性検証 | プログラム | 過学習・結果が良すぎる兆候の統計検出 |

### 7-A.3 L1: 静的品質ゲート（プログラム）

pre-commitフックおよびCI相当のスクリプトで機械的に実行。

- `ruff`：lint＋フォーマット
- `mypy`：型チェック（scenarios, backtest, validation は必須通過）
- `pytest`：全テスト実行
- カバレッジ閾値：全体60%、シナリオ90%、バックテスト80%未満で失敗
- 設定ファイル：`.pre-commit-config.yaml`、`pyproject.toml`（tool設定）

これらを通過しないコードはコミット不可。

### 7-A.4 L3: 金融ロジック検証（プログラム＋軽量運用）

`src/validation/` 配下に検出ロジックをプログラムとして実装。

**lookahead_detector.py（先読みバイアス検出）**
- バックテストで未来データを参照していないかを検査
- 手法：各時点tのシグナル計算が、t以降のデータに依存しないことを検証
  （データを意図的にt時点で打ち切って再計算し、結果が一致するか）
- バックテストで最も致命的なバグであり、必ず自動検出する

**metrics_validator.py（指標計算の正しさ検証）**
- シャープレシオ等の計算式を、既知の入力と理論値で突き合わせ
- 例：固定リターン系列に対する理論シャープ値と一致するか
- 「テストと実装が同じ思い込み」を避けるため、独立した検証データを使用

**execution_realism_checker.py（約定現実性チェック）**
- 当日終値シグナル→当日約定になっていないか（翌営業日始値が原則）
- 取引コスト（手数料・スリッページ・為替）が漏れなく計上されているか
- 流動性フィルタが適用されているか

**survivorship_checker.py（生存者バイアス検出）**
- ユニバースに上場廃止銘柄が含まれているか
- バックテスト期間中の構成銘柄変更が反映されているか

**parity_checker.py（バックテスト・ライブ一貫性検証）**
- 同一日付・同一データに対し、バックテストエンジンとライブの
  日次シグナル生成パイプラインが同一シグナルを出すことを検証
- 手法：過去のある期間について、バックテストが生成したシグナルと、
  daily_signals.py を当時のデータ断面で再実行した結果を突き合わせ
- 不一致は「バックテストとライブで実装が乖離している」致命的バグの兆候
- ペーパートレード開始前（Phase 5前）に必ず通過させる
- 運用中も週次で直近シグナルのパリティを自動チェック

軽量運用部分：上記で機械検出しきれない設計レベルの妥当性は、
`src/agents/finance_validator.md` のプロンプトで別セッションのClaude Codeに
レビューさせる。

### 7-A.5 L4: バックテスト健全性検証（プログラム）

`src/validation/sanity_checker.py` および `overfitting_monitor.py`。

**過学習・異常検出**
- シャープレシオ異常高（例：> 3.0）→ 警告
- 勝率異常高（例：> 80%）→ 警告
- 最大DDがほぼゼロ → 警告（現実離れ）
- 取引回数 < 30 → 統計的信頼性不足として棄却

**パラメータ感応度分析**
- 推奨パラメータ周辺で値を微小変動させ、結果が激変しないか検査
- 激変する＝過学習の兆候として警告
- 感応度ヒートマップをレポート出力

**学習・検証・アウトオブサンプルの性能差検出**
- 学習期間とアウトオブサンプル期間でシャープレシオが大きく劣化していないか
- 劣化率が閾値（例：50%超劣化）を超えたら過学習と判定し採用却下

### 7-A.6 L2: コードレビューエージェント（軽量運用）

常駐プログラムではなく、別のClaude Codeセッションで実行する運用。
実装したセッションとは分離することで自己レビューの盲点を回避。

- `src/agents/code_reviewer.md`：レビュー用システムプロンプト
- `src/agents/review_checklist.md`：レビュー観点リスト
- 運用：Phase 3以降、重要モジュール（backtest, scenarios, validation）の
  実装完了時に実施。Phase 1〜2では過剰なため任意

### 7-A.7 ディレクトリ追加

```
src/
├── validation/                  # L3, L4（プログラム）
│   ├── __init__.py
│   ├── lookahead_detector.py
│   ├── metrics_validator.py
│   ├── execution_realism_checker.py
│   ├── survivorship_checker.py
│   ├── parity_checker.py        # バックテスト・ライブ一貫性検証
│   ├── sanity_checker.py
│   └── overfitting_monitor.py
└── agents/                      # L2, L3の軽量運用部分
    ├── code_reviewer.md
    ├── finance_validator.md
    └── review_checklist.md
```

### 7-A.8 検証の実行タイミング

| 検証 | 実行タイミング |
|------|-------------|
| L1 静的ゲート | 全コミット時（pre-commit） |
| L3 金融ロジック検証 | バックテスト実行前に自動実行（必須通過） |
| L3 パリティ検証 | ペーパートレード開始前に必須通過＋運用中は週次 |
| L4 健全性検証 | バックテスト実行後に自動実行 |
| L2 コードレビュー | Phase 3以降、重要モジュール完成時 |

L3・L4はバックテストパイプラインに組み込み、検証失敗時はバックテスト結果を
「信頼できない」とマークし、採用判断の対象から除外する。

---

## 8. 運用シーケンス

### 8.1 日次運用フロー

```
06:00 JST  cron起動
    ↓
06:01      data_update.py 実行
              ・前営業日終値取得（米国市場閉場後）
              ・データ品質チェック
              ・DuckDB更新
    ↓
06:05      daily_signals.py 実行
              ・各シナリオでシグナル生成
              ・既存ポジションのエグジット判定
              ・競合解決
              ・マクロフィルタ適用
    ↓
06:10      Slack通知
              ・新規シグナル一覧
              ・エグジット推奨銘柄
              ・ポートフォリオサマリー
    ↓
08:00頃    人間が確認 → 楽天証券で手動発注（日本株：9:00寄付、米国株：23:30）
    ↓
夜         約定結果を手動入力（CLI or Streamlit）
              ・positionsテーブル更新
              ・tradesテーブル追記
```

### 8.2 週次・月次フロー

**週末**
- Streamlitダッシュボードでポートフォリオ全体レビュー
- 予実乖離分析
- 必要に応じてシナリオ修正トリガー確認

**月末**
- シナリオ別パフォーマンスレビュー
- 修正候補ピックアップ（実行は四半期単位）

**四半期末**
- パラメータ再検討
- バックテスト再実行（必要時）
- 採用パラメータ変更（要Gitコミット）

---

## 9. 拡張ポイント

### 9.1 新シナリオ追加手順
1. `src/scenarios/sN_xxx.py` を新規作成、`ScenarioBase` を継承
2. `config/scenarios/sN.yaml` を作成
3. テストコード追加
4. バックテスト実行・評価
5. 採用判断後、`config/settings.yaml` で有効化

### 9.2 ポジションサイザの進化（案1→2→3）
- `src/portfolio/sizer.py` をストラテジーパターンで実装
- 設定で切り替え可能：`sizer_strategy: fixed | per_scenario | atr_based`

### 9.3 自動発注への進化
将来証券会社APIを使う場合、`src/execution/broker.py` を追加し、現在の「手動発注前提」と互換性を保つ。

---

## 10. 制約事項・既知のリスク

### 10.1 技術的制約
- yfinanceは非公式APIで突然停止リスクがあるが、データソース抽象化レイヤ
  （3.2 fetcher.py）でStooq/J-Quants/Alpha Vantageへ自動フォールバックする設計で緩和
- 決算データ精度はyfinanceでは限定的、必要に応じて別ソース検討
- 日米市場のタイムゾーン差により、日次バッチのタイミング設計が複雑

### 10.2 運用上の制約
- 手動発注前提のため、寄り付き約定の遅れ・スリッページが発生
- 平日朝のシグナル確認を人間が行う必要あり

### 10.3 設計上の妥協
- バックテストエンジンは自作のため、商用FW相当の機能（複雑な約定モデル、複雑な税制）は非対応
- 必要になった時点で機能追加

---

## 11. 改訂履歴

| バージョン | 日付 | 変更内容 |
|----------|------|---------|
| 1.0 | 2026-05-16 | 初版作成 |
| 1.1 | 2026-05-16 | 品質保証・検証アーキテクチャ（7-A節、4レイヤ）を追加 |
| 1.2 | 2026-05-16 | データソース冗長化（fetcher.py抽象化）、バックテスト・ライブ一貫性検証（parity_checker）を追加 |
| 1.3 | 2026-05-16 | 整合性レビュー反映：為替コストの定義（片道、往復実質1.0%）を明確化 |
