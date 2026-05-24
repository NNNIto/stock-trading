# 作業進捗レポート — 2026-05-24

## 完了した作業

### 戦略リデザイン (A1 + A3)
- **S3 (Pullback/RSI) を無効化** — 6年間で一貫して損失 (-72K) のため `config/scenarios/s3.yaml` を `enabled: false` に変更
- **S6 パラメータ緩和** — `return_threshold: -0.10 → -0.07`, `rsi_oversold: 10 → 15`、グリッドサーチ範囲も更新
- **バックテスト結果** (`results/backtest_redesign_20260524.json`):
  - 期間: 2019-01-01 〜 2024-12-31、資本: 300万円、銘柄数: 250 (JP100 + US150)
  - Sharpe: **0.164** (目標 1.0 に対し未達)、最大DD: -33.6%、トレード数: 187
  - S2 が 2021年に -735K (フェイクブレイクアウト多発) — 指数 200MA フィルターでも防げなかった

### マクロフィルター追加
- `src/backtest/engine.py`: `MacroFilter.is_market_blocked()` でJP(^N225) / US(SPY) の 200MA 判定
- `scripts/run_backtest.py` / `scripts/walkforward.py`: `_build_index_ma_filter()` と `_resolve_symbols()` を追加

### ユニバースフィルター
- `config/settings.yaml`: `universe_filter` セクション追加 (JP top100, US top150, 3年ルックバック)
- `src/data/repository.py`: `query_liquid_symbols()` — 日次売買代金(終値×出来高)上位N銘柄を返す
- `src/data/universe.py`: `get_liquid_symbols()` のラッパー関数

### J-Quants 連携 (S4 PEAD 用)
- `.env`: `JQUANTS_API_KEY` を設定済み (gitignore 済み、絶対コミット禁止)
- `.env.example`: プレースホルダーのみ (空値)
- `src/data/fetcher.py`: `JQuantsSource.fetch_earnings()` 実装、JP ルーティング追加
- `scripts/data_update.py`: `load_dotenv()` 追加

### テスト追加
- `tests/backtest/test_engine.py`: MacroFilter の市場別ブロックテスト 2件
- `tests/data/test_repository.py`: `query_liquid_symbols` の上位N取得・先読み防止テスト 2件

---

## 未解決の課題 (次回優先対応)

### 🔴 最重要: JP 決算データ取得 (J-Quants 429 問題)

**症状**: `earnings` テーブルに 21行・5銘柄しかない。225銘柄への一括取得で全件 429 Too Many Requests

**根本原因**: J-Quants 無料プランのレートリミット。`time.sleep(2.0)` を入れても、直前の実行で消費した quota が回復しきっていない状態で再実行すると即 429

**推奨対策** (次回実装):
```
オプションA: bulk endpoint に切り替え
  - J-Quants v2 の /v2/fins/statements を使い、全銘柄の財務データを1回の呼び出しで取得
  - jquantsapi の get_fin_statements() は日次で全社一括ダウンロード可能
  - 実装: JQuantsSource.fetch_earnings_bulk() → upsert → 1日1回 cron でOK

オプションB: sleep を大幅延長 (10〜30秒) + 再開機能
  - 225銘柄 × 30s = 1.9時間かかる
  - 中断時に再開できるよう「取得済みスキップ」ロジックが必要

推奨: オプションA (bulk) — 実装が簡単かつ確実
```

**S4 (PEAD) への影響**: 決算データが揃わない限り S4 はトレード 0 件のまま。Sharpe 改善には S4 が必要不可欠

### 🔴 最重要: Sharpe 比 0.164 → 目標 1.0

**ボトルネック**:
1. **S2 2021年問題**: 2021年は指数 200MA 上 (強気相場) なのにフェイクブレイクアウトで -735K
   - 対策案: ボリューム確認 (ブレイクアウト当日出来高が直近平均の1.5倍以上) を条件追加
   - 対策案: 52週高値タッチ後 **3日連続** クローズで確定 (現状は当日判定)
2. **S4 トレード 0**: 決算データ待ち (上記)
3. **グリッドサーチ未完**: ウォークフォワードが DuckDB ロック競合で未実行

### 🟡 要対応: ウォークフォワード未完了

**症状**: DuckDB のファイルロック競合で `walkforward.py` が失敗
```
_duckdb.IOException: Could not set lock on file "data/trading.duckdb"
```

**対策**: 決算取得完了後に逐次実行する
```bash
uv run python scripts/walkforward.py \
  --is-start 2019-01-01 --is-end 2024-12-31 \
  --out results/walkforward_redesign_20260524.json
```

### 🟡 要対応: US 決算データ

- US 銘柄は yfinance からの取得 (S&P500 主要銘柄)
- 未実装: `FallbackDataSource.fetch_earnings()` の US 分岐は現状 `_primary.fetch_earnings()` に fallback (yfinance が earnings を返すかどうか未確認)
- 確認・実装が必要

### 🟢 後回し可: デプロイ・運用

- `scripts/daily_signals.py` の cron 設定
- Streamlit ダッシュボードの earnings セクション
- 7205.T が上場廃止の可能性 (yfinance で `possibly delisted` 警告)

---

## 次回セッション開始時の手順

```bash
# 1. 決算 bulk 取得 (J-Quants get_fin_statements) を実装・実行
#    → src/data/fetcher.py の JQuantsSource に fetch_earnings_bulk() を追加
#    → scripts/data_update.py に --bulk-earnings オプションを追加

# 2. DBの状態確認
python3 -c "
from src.data.repository import Repository
with Repository() as repo:
    r = repo._conn.execute('SELECT COUNT(*), COUNT(DISTINCT symbol) FROM earnings').fetchone()
    print(f'earnings: {r[0]} rows, {r[1]} symbols')
"

# 3. ウォークフォワード実行
uv run python scripts/walkforward.py \
  --is-start 2019-01-01 --is-end 2024-12-31 \
  --out results/walkforward_redesign_20260524.json

# 4. S2 ブレイクアウト確認ロジック強化を検討
```

---

## DB 現状スナップショット (2026-05-24)

| テーブル | 行数 | 備考 |
|---|---|---|
| ohlcv | 〜300万行 (JP225+US500) | 2019-01-01 〜 2026-05-24 |
| earnings | **21行 / 5銘柄** | J-Quants 429 により取得失敗中 |
| symbols | 〜725行 | JP225 + S&P500 |

## コミット済みハッシュ
- 最新: `e2c49cf` (test: add reporting tests, fix lint, commit untracked files)
- 未コミット: `config/settings.yaml`, `scripts/data_update.py`, `scripts/walkforward.py`, `src/data/fetcher.py`, `results/backtest_redesign_20260524.json`
