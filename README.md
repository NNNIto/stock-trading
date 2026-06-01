# stock-trading

日米株式投資自動化システム。日経225+S&P500を対象とした中期スイング戦略。

## セットアップ

```bash
# uv インストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存関係インストール
uv sync --extra dev

# 環境変数設定
cp .env.example .env
# .env を編集して各種キーを設定
```

## 主要コマンド

```bash
# データ更新
uv run python scripts/data_update.py

# テスト実行
uv run pytest

# 型チェック
uv run mypy src/

# リント
uv run ruff check .
```

## GitHub Actions

| ワークフロー | トリガー | 内容 |
|---|---|---|
| `ci.yml` | push / PR → main | ruff・mypy・pytest（カバレッジ 60%+） |
| `daily_update.yml` | 平日 JST 08:30 / 23:30 | データ更新バッチ、失敗時 Slack 通知 |

GitHub Secrets に以下を登録してください：`JQUANTS_API_KEY`, `SLACK_WEBHOOK_URL`, `ALPHA_VANTAGE_API_KEY`

> **本番環境では常時稼働サーバーを推奨**
> GitHub Actions の Runner は毎回クリーンな環境で起動するため、DuckDB のデータは実行間で保持されません。
> `daily_update.yml` はデータ取得の動作確認用です。継続運用には Linux サーバー上での cron（`scripts/setup_cron.sh`）を使用してください。

## ドキュメント

- `requirements.md`: 要件定義書
- `docs/architecture.md`: アーキテクチャ設計書
- `docs/scenarios.md`: シナリオ仕様書
- `docs/implementation_guide.md`: 実装手順書
