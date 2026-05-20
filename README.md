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

## ドキュメント

- `requirements.md`: 要件定義書
- `docs/architecture.md`: アーキテクチャ設計書
- `docs/scenarios.md`: シナリオ仕様書
- `docs/implementation_guide.md`: 実装手順書
