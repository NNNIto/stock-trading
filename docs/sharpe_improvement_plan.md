# Sharpe改善計画

**作成:** 2026-06-03  
**現状:** Sharpe 0.60 → 目標 1.0

---

## 現在のバックテスト実績（2019-2024, US専用）

| 指標 | 値 | 目標 |
|------|-----|------|
| Sharpe | 0.60 (CI: -0.06〜1.26) | 1.0 |
| CAGR | 6.62% | 15-20% |
| MaxDD | -15.93% | -20%以内 ✓ |
| Win率 | 56.1% | - |
| 取引数 | 123件 / 6年 (20件/年) | 50件/年+ |

**期間別:**
- IS (2019-2022): Sharpe 0.35
- OOS (2023-2024): Sharpe 1.35 ✓（目標達成）

**シナリオ別貢献:**
- S2 (52週高値ブレイク): 55件, win55%, avg+3.67% → PnLの70%
- S4 (PEAD): 38件, win47%, avg+0.68% → PnLの13%（earnings導入後）
- S6 (平均回帰): 30件, win70%, avg+1.56% → PnLの18%

---

## 改善ロードマップ（ペーパートレード終了後に実施）

### 優先度1: Universe拡大（150→200銘柄）

**実施内容:** `config/settings.yaml` の `us_top_n: 150 → 200`

**根拠:**
- 現在20件/年の取引数は統計的に不十分（Sharpe CI下限がマイナス）
- 50件以上で安定した推定が可能
- 追加50銘柄は流動性上位200位以内のSP500構成銘柄（SPGI, ADI, HLT等）

**推定効果:**
- 取引数: 20件/年 → 27件/年 (+35%)
- Sharpe改善: 0.60 → ~0.75（統計的信頼性向上分）

**必要条件:** WF (2019-2024, US, 20窓) で degradation_ratio ≥ 0.5 を確認

---

### 優先度2: S6 RSI閾値緩和（10→15）

**実施内容:** `config/scenarios/s6.yaml` の `rsi_oversold: 10 → 15`

**根拠:**
- S6は5件/年と極端に少ない（RSI-2 < 10は年に数回しか発生しない）
- RSI-2 < 15に緩和 + return_threshold -0.07% に引き締めで品質を維持
- 70%のwin率は維持可能と推定

**推定効果:**
- S6取引数: 5件/年 → 8-10件/年
- 小幅なwin率低下（70% → 60%）は avg_pnlの改善で補完

**必要条件:** WF で新パラメータの degradation_ratio ≥ 0.5 確認

---

### 優先度3: S4 EPSサプライズ閾値強化（0%→5%）

**実施内容:** `config/scenarios/s4.yaml` の `surprise_threshold_pct: 0.0 → 0.05`

**根拠:**
- S4 avg PnL が 0.68% と低い（S2の3.67%、S6の1.56%に劣る）
- 2023年にも avg -2.11% で underperform
- EPS surprise > 5% に絞ることで高確信度のPEADシグナルに限定

**推定効果:**
- 取引数: 38件/6年 → ~20件/6年（減少）
- avg PnL: 0.68% → ~2.0%（品質向上）

**必要条件:** WF で S4単体の degradation_ratio ≥ 0.5 確認

---

## 現在実行中のWF（バックグラウンド）

```
scripts/walkforward.py --market US --is-start 2019-01-01 --is-end 2024-12-31
出力: results/walkforward_us_with_earnings_20260603.json
推定完了: ~10:00 JST（約60分）
```

WF結果の確認コマンド:
```bash
uv run python -c "
import json
d = json.load(open('results/walkforward_us_with_earnings_20260603.json'))
print('degradation_ratio:', d['degradation_ratio'])
print('is_robust:', d['is_robust'])
print('median_val_sharpe:', d['median_val_sharpe'])
"
```

---

## 実施スケジュール

| フェーズ | 時期 | 内容 |
|---------|------|------|
| 現在 | 〜6/15 | ペーパートレード監視（コード変更なし） |
| Phase A | 6/16〜 | Universe拡大 + WF再実行（優先度1） |
| Phase B | 6/20〜 | S6調整 + WF（優先度2） |
| Phase C | 6/25〜 | S4強化 + WF（優先度3） |
| Phase D | 7月〜 | 少額本番デビュー検討 |
