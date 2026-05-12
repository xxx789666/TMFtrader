---
name: optimizer_findings
description: BreakoutTrendStrategy 各版本優化結果與當前最佳參數（持續更新）
type: project
---

## 策略版本演進（BreakoutTrendStrategy，TMF 5分K，2024-01-01~2026-04-11，28個月）

初始資金 200,000 TWD，風控模式 tmf_3x（最多3口）

| 版本 | n | WR | PF | net | RR | N6 |
|------|---|----|----|-----|-----|-----|
| v3 基準 | 128 | 56.3% | 1.796 | +157,250 | 1.398 | 7/28 |
| v4（trail 1.0→1.2, ec 25→30） | 121 | 57.0% | 1.917 | +179,750 | 1.444 | 8/28 |
| v4b（rsi_bear 48→46） | 119 | 57.1% | 1.949 | +181,130 | 1.461 | 8/28 |
| **v5（expand 1.08→1.18, pb 0.30→0.20, dig→10）** | **96** | **60.4%** | **2.480** | **+203,430** | **1.625** | **8/28** |

## v5 最佳參數（2026-04-13 確認）

```python
# 相對 v4b 改動的參數
expand_ratio    = 1.18   # ← v4b=1.08，要求更強突破擴張（核心改動）
pullback_ema_gap = 0.20  # ← v4b=0.30，Mode B 回調更貼近 EMA20
min_di_gap      = 10.0   # ← v4b=5.0，方向清晰度要求更嚴（可選，效果小）

# 固定不動（v4b 已確認最佳）
trail_trigger_atr = 1.2
trail_dist_atr    = 1.25
early_cut_bars    = 30
early_cut_loss_atr = 1.5
max_loss_twd      = 4000.0
momentum_rsi_bear = 46.0
momentum_rsi_bull = 52.0
```

**為什麼有效：**
- expand_ratio 1.08→1.18：過濾「勉強突破」，只在 ATR 顯著放大時進場
- pullback_ema_gap 0.30→0.20：Mode B 回調更精確，等 EMA5 更貼 EMA20 才進
- 交易量減少（119→96），但每筆品質更高 → WR 和 RR 同時上升

## 優化歷程快照

- v3 → v4：trail_trigger 1.0→1.2（RR↑）、ec_bars 25→30（net↑），共同嚴格改善
- v4 → v4b：rsi_bear 48→46，嚴格改善（所有指標同向）
- v4b → v5：expand_ratio + pullback_ema_gap 組合掃描，71 個組合嚴格優於 v4b
- scale-out 架構（1口早出2口追蹤）測試：無效，RR 降至 0.59，net 降至 +13~14萬

## 已確認無效的方向

- min_vol_ratio 提高（1.0→1.1+）：net 和 RR 都下降
- EMA200 margin_atr：無效果
- scale-out 分批出場（1/3口早出）：RR 崩潰至 0.58，不可用
- squeeze_ratio 加嚴（0.90→0.80）：n 大減至 36，N6 掉至 4/28（樣本不足）

## 下一步建議

v5 已達成目標（RR≥1.5 ✅、net≥+100% ✅、WR不降 ✅），可生成完整回測報告。
若需要進一步提升，可探索：
- afternoon_min_adx 搭配 expand_ratio 聯合細化
- trail_dist_atr 在 v5 基礎上重新掃描（當前 1.25）
