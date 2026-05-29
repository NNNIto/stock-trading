# Claude Code 実装手順書

日米株式自動売買システム `stock-trading` を Claude Code を使って完成させるための手順書です。  
リポジトリのルート (`stock-trading/`) で Claude Code を起動した状態で、各プロンプトをそのまま貼り付けてください。

-----

## 目次

1. [前提・基本情報](#前提基本情報)
1. [Phase 1 — 環境確認 & テスト修正](#phase-1--環境確認--テスト修正)
1. [Phase 2 — データ取得パイプライン強化](#phase-2--データ取得パイプライン強化)
1. [Phase 3 — agents/ の実装完成](#phase-3--agents-の実装完成)
1. [Phase 4 — Streamlit ダッシュボード完成](#phase-4--streamlit-ダッシュボード完成)
1. [Phase 5 — 自動化 & CI/CD](#phase-5--自動化--cicd)
1. [Phase 6 — デバッグ用テンプレート](#phase-6--デバッグ用テンプレート)
1. [実行順チェックリスト](#実行順チェックリスト)

-----

## 前提・基本情報

すべてのプロンプトで共通して有効なコンテキストです。  
Claude Code セッション開始時に一度伝えておくと以降省略できます。

```
このリポジトリは日米株式（日経225 + S&P500）を対象とした中期スイング自動売買システムです。
Python 3.12+、uv パッケージ管理。
主要ライブラリ: Polars, DuckDB, yfinance, jquants-api-client, pandas-ta, Pydantic, Streamlit
テスト要件: pytest カバレッジ 60%+、mypy strict=false、ruff
除外中: src/agents/（未完成）、src/reporting/dashboard.py（未完成）
```

-----

## Phase 1 — 環境確認 & テスト修正

### 1-A　コードベース全体の診断

**タイミング:** 最初に必ず実行する

```
# コンテキスト
このリポジトリは日米株式（日経225 + S&P500）を対象とした中期スイング自動売買システムです。
Python 3.12+、uv パッケージ管理、主要ライブラリは Polars・DuckDB・yfinance・jquants-api-client・pandas-ta・Pydantic・Streamlit です。

# タスク
以下をすべて調べてレポートしてください：

1. src/ 配下の全ファイルを読み、各モジュールの役割と実装状況（完成 / 未完成 / スタブ）を一覧にする
2. tests/ 配下を読み、テストカバレッジが不足しているモジュールを特定する
3. src/agents/ と src/reporting/dashboard.py が pyproject.toml でカバレッジ除外されている理由を推測し、実装上の欠落を列挙する
4. config/ の設定ファイルを読み、戦略パラメータ（RSI閾値・MA期間・損切りライン等）を整理する
5. 即座に修正すべきバグ・型エラー・未実装の TODO コメントをすべて列挙する

レポートは Markdown 形式で、優先度（HIGH / MED / LOW）付きで出力してください。
```

### 1-B　テスト修正・カバレッジ達成

**タイミング:** 1-A の診断結果を確認したあと

```
uv run pytest を実行し、失敗しているテストをすべて修正してください。

制約：
- テストの意図を変えずに、実装側のコードを修正することを優先する
- テスト自体が間違っている場合のみテストを修正し、その理由をコメントに残す
- カバレッジが 60% 未満になる場合は、不足しているテストケースを tests/ に追加する
- pyproject.toml の除外設定（src/agents/, src/reporting/dashboard.py）は変更しない

修正後に uv run pytest --cov=src を実行してカバレッジが 60% 以上であることを確認してください。
```

-----

## Phase 2 — データ取得パイプライン強化

### 2-A　data_update.py の堅牢化

**タイミング:** テストが通ったあと、初回データ取得の前

```
# コンテキスト
scripts/data_update.py は jquants-api-client（日本株）と yfinance（米国株）からデータを取得し DuckDB に保存します。
.env に JQUANTS_API_KEY が設定されています。

# タスク
scripts/data_update.py を読んで、以下の改善を加えてください：

1. API 呼び出しに retry デコレータを追加（最大3回・指数バックオフ）
2. 欠損値（NaN）の検出と Loguru によるログ出力を追加
3. 既に DuckDB に存在する日付のデータを重複挿入しないように upsert 処理を実装
4. 取得成功・失敗を summary として標準出力に表示する
5. --dry-run フラグを追加してデータ保存なしでテスト実行できるようにする

型ヒントを必ず付けて mypy が通るようにしてください。変更後に uv run mypy src/ scripts/ を確認すること。
```

-----

## Phase 3 — agents/ の実装完成

> **注意:** 3-A で設計を確認してから 3-B・3-C に進んでください。いきなりコードを書かせないこと。

### 3-A　agents/ の現状確認と実装計画

**タイミング:** agents/ 実装に着手する前に必ず実行

```
src/agents/ 配下のすべてのファイルを読み、以下を整理してください：

1. 各ファイル・クラス・関数の役割と実装状況（実装済み / スタブ / 未着手）
2. 未実装の関数のシグネチャと期待される動作を docs/architecture.md と docs/scenarios.md から推測して列挙
3. src/strategies/ からのシグナルをどのように受け取って注文に変換するかのデータフローを図示（ASCII アート可）
4. Slack 通知（SLACK_WEBHOOK_URL）をどのタイミングで送るべきか設計案を提示
5. 実装完了までの作業リストを優先度付きで出力

実装は提案だけで OK です。コードを書く前に設計を確認します。
```

### 3-B　Slack 通知機能の実装

**タイミング:** 3-A の設計を確認・承認したあと

```
# コンテキスト
.env に SLACK_WEBHOOK_URL が設定されています。
pydantic-settings を使って設定を読み込む構成です。
シグナルは src/strategies/ から生成され、銘柄コード・方向(BUY/SELL/HOLD)・価格・テクニカル指標値を持ちます。

# タスク
src/agents/ に Slack 通知機能を実装してください：

1. SlackNotifier クラスを作成（pydantic-settings で SLACK_WEBHOOK_URL を読み込む）
2. 以下のイベントに対応したメソッドを実装：
   - notify_signal(signal): BUY/SELL シグナル発生時（銘柄・価格・指標値・損切りライン含む）
   - notify_order_executed(order): 注文執行完了時
   - notify_daily_summary(summary): 日次サマリ（シグナル数・損益）
   - notify_error(error): エラー発生時
3. Slack の Block Kit 形式でリッチなメッセージを送信
4. 送信失敗時は Loguru でエラーログを出力し、例外を握りつぶさない

ファイル: src/agents/notifier.py
テスト: tests/agents/test_notifier.py（SLACK_WEBHOOK_URL を mock して実際には送信しない）

型ヒント必須。mypy と ruff を通してください。
```

### 3-C　注文実行エージェントの実装

**タイミング:** 3-B の Slack 通知が動作確認できたあと

```
# コンテキスト
src/strategies/ がシグナルを生成し、src/risk/ がポジションサイズを決定します。
現在 src/agents/ はカバレッジ除外されており、実装が不完全な状態です。
本番実装前に dry_run モードで動作確認したいです。

# タスク
src/agents/ の注文実行エージェントを以下の仕様で実装してください：

1. TradingAgent クラスを実装：
   - __init__(dry_run: bool = True): dry_run=True のときは実際に注文しない
   - run_daily(): データ更新 → 指標計算 → シグナル生成 → リスク評価 → 注文 → 通知の一連フロー
   - execute_order(signal, size): 注文実行（dry_run 時はログ出力のみ）

2. dry_run=True のとき：
   - 「[DRY RUN] BUY 7203 ¥3,850 × 100株」のように Loguru で INFO 出力
   - results/signals_YYYYMMDD.csv にシグナルを記録（これは dry_run でも実行）

3. エラーハンドリング：
   - 注文失敗時は Slack に notify_error() を送信して処理を継続
   - 致命的エラーのみ例外を再 raise する

ファイル: src/agents/trading_agent.py
テスト: tests/agents/test_trading_agent.py（dry_run=True で全テスト）
```

-----

## Phase 4 — Streamlit ダッシュボード完成

### 4-A　dashboard.py の完成

**タイミング:** agents/ が一通り動くようになったあと

```
# コンテキスト
- src/reporting/dashboard.py は現在未完成でカバレッジ除外中
- データは DuckDB に保存されており Polars で読み込む
- results/ に trades.csv・signals_YYYYMMDD.csv・equity_curve.csv が生成される
- グラフは Plotly を使用

# タスク
src/reporting/dashboard.py を以下のページ構成で完成させてください：

【サイドバー】
- 表示期間の日付レンジ選択
- 市場フィルター（全て / 日本株 / 米国株）

【メインページ - 4つのセクション】
1. KPI カード: 累計損益・勝率・最大ドローダウン・シャープ比（st.metric）
2. 資産推移: results/equity_curve.csv を Plotly 折れ線グラフで表示
3. シグナル一覧: 最新30件のシグナルを BUY(緑)/SELL(赤)/HOLD(灰) 色分けテーブル
4. 日次損益バーチャート: Plotly で正値=緑・負値=赤で表示

results/ が空の場合でもエラーにならず「データなし」と表示するようにしてください。

完成後: pyproject.toml の coverage.run.omit から dashboard.py を削除してテストを追加
```

-----

## Phase 5 — 自動化 & CI/CD

### 5-A　GitHub Actions の設定

**タイミング:** ペーパートレードで動作確認が取れたあと

```
GitHub Actions のワークフローファイルを2つ作成してください：

【1】.github/workflows/daily_update.yml
- スケジュール: 平日 JST 8:30（= UTC 23:30）と JST 23:30（= UTC 14:30）に実行
- ステップ:
  1. uv のインストール
  2. uv sync（本番依存のみ）
  3. uv run python scripts/data_update.py
  4. 失敗時に Slack 通知（SLACK_WEBHOOK_URL は GitHub Secrets から読み込む）
- secrets で管理: JQUANTS_API_KEY, SLACK_WEBHOOK_URL, ALPHA_VANTAGE_API_KEY

【2】.github/workflows/ci.yml
- トリガー: push / pull_request（main ブランチ）
- ステップ:
  1. uv sync --extra dev
  2. uv run ruff check .
  3. uv run mypy src/
  4. uv run pytest --cov=src --cov-fail-under=60

注意: DuckDB のデータは GitHub Actions では永続化できないため daily_update.yml は本番運用向きではありません。
README に「本番環境では常時稼働サーバー推奨」の注記を追加してください。
```

### 5-B　コード品質の一括改善

**タイミング:** いつでも（定期的に実行推奨）

```
src/ 配下全体のコード品質を改善してください：

1. uv run ruff check . --fix を実行してリント違反を自動修正
2. uv run mypy src/ を実行して型エラーをすべて解消
   - 型ヒントが抜けている関数にはすべて追加
   - Any 型は極力避け、具体的な型（Polars DataFrame 等）を使う
3. public メソッド・関数に Google スタイルの docstring を追加
   （引数・戻り値・例外を記述）
4. Loguru のログレベルを統一：
   - DEBUG: 詳細なデータ処理ステップ
   - INFO: シグナル生成・注文実行
   - WARNING: データ欠損・リトライ
   - ERROR: 例外・通知失敗

機能の変更は行わないでください。リファクタリングのみです。
変更後に uv run pytest が通ることを確認してください。
```

-----

## Phase 6 — デバッグ用テンプレート

### 6-A　エラー発生時

**タイミング:** エラーメッセージが出たとき。`[ここにエラーを貼る]` を実際のエラーに置き換えて使う。

```
# 発生したエラー
[ここにエラーメッセージを貼る]

このエラーを調査して修正してください。

1. エラーが発生しているファイルと行を特定する
2. 原因を日本語で説明する
3. 修正コードを提示する
4. 同種のバグが他の箇所にないか src/ 全体を確認する

修正後に uv run pytest を実行して既存テストが壊れていないことを確認してください。
```

### 6-B　シグナルがおかしいとき

**タイミング:** BUY/SELL シグナルが出ない・多すぎるなど意図と違うとき。`[問題の状況]` を書き換えて使う。

```
# 問題の状況
[例: BUY シグナルが1件も出ない / SELL シグナルが多すぎる]

src/strategies/ のシグナル生成ロジックを調査してください：

1. シグナル生成の条件式を src/strategies/ から読み出して整理
2. config/ の現在のパラメータ値（RSI閾値・MA期間等）を確認
3. results/ の直近シグナルデータを参照して実際に条件が満たされているか検証
4. 問題の原因を特定して、config/ のパラメータ調整案を複数提示
   （調整前後のシグナル件数の変化を予測すること）

コードを変更する前に「この変更で○○が改善されますが、△△のリスクがあります」と説明してください。
```

-----

## 実行順チェックリスト

```
[ ] 1-A  コードベース全体の診断
[ ] 1-B  テスト修正・カバレッジ 60% 達成
[ ] 2-A  data_update.py 堅牢化
[ ]      初回データ取得（uv run python scripts/data_update.py）
[ ] 3-A  agents/ 設計確認（コード変更なし）
[ ] 3-B  Slack 通知実装
[ ] 3-C  注文実行エージェント実装（dry_run=True で検証）
[ ] 4-A  Streamlit ダッシュボード完成
[ ]      ペーパートレード 1週間検証
[ ] 5-A  GitHub Actions 設定
[ ] 5-B  コード品質一括改善
[ ]      少額で本番デビュー
```