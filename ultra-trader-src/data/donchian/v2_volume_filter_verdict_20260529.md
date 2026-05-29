# Donchian v2 — Volume-Confirmation Filter 判決 (2026-05-29)

## TL;DR

**量能確認濾網（vol_mult）對 Donchian on TMF 5m 無效、甚至有害。**
自動 gate 報告（`robustness_report_20260529_1758.md`）因為按 wf_score 選了 `vol_mult=0.0`（濾網關），
OOS 數字與 v1 完全相同（longonly PF 1.151 / biside 1.082）。本文件補上自動報告沒記到的關鍵事實：
**直接比對 filtered vs unfiltered，濾網系統性壓低 PF，PF>1.2 在任何 vol_mult 下都搆不到。**

---

## 背景

v1 診斷（`diag_donchian_grid.py`）發現純結構性 Donchian 的 PF 天花板 ~1.08–1.15、整片曲面 sub-gate，
Perturb 不穩是「無 PF 分離度 → 排名雜訊」的假象。v1 報告（昨晚）建議三條 v2 路：
1. 拉長 entry_n 40-60 — 經診斷判為**反指標**（entry_n 越大越差），未採用
2. 改 exit/trailing — 邊際，未採用
3. **加 volume spike 濾網** — 唯一數據面有上檔的選項 → 本次 v2 實作

v2 做法：`DonchianStrategy` 加 `vol_mult` 參數（進場突破 bar 要求 `volume_ratio >= vol_mult`，
0.0=off）。選 volume 而非 ATR/EMA/ADX 以維持與 live BreakoutTrend 的差異化（後者不用 volume）。
grid 加 `vol_mult ∈ {0.0, 1.2, 1.5, 2.0}`，並 trim v1 死維（cooldown 固定 3、砍 entry_n=30、砍 exit_k=5）。
96 combos/variant × 4 runs（MXF/TXF × longonly/biside）。

---

## 關鍵數據（`diag_vol_mult.py`，test_n>=100 合格組）

### 各 vol_mult 的 test_pf — 單調下降（濾網有害）

| grid | vol_mult=0.0 (off) | 1.2 | 1.5 | 2.0 |
|---|---|---|---|---|
| MXF longonly | **0.843** (max 1.032) | 0.772 | 0.805 | — |
| TXF longonly | **0.857** (max 1.085) | 0.759 | 0.633 | — |
| MXF biside | **0.933** (max 1.057) | 0.879 | 0.879 | 0.790 |
| TXF biside | **0.940** (max 0.991) | 0.853 | 0.792 | 0.766 |

(中位 test_pf；off 在每個 grid 都是最高)

### PF>1.2：**0 組 / 4 grids**（v1 沒有、加 vol_mult 也沒有）

### 配對 Δ（同 base 5 參數，best filtered − unfiltered 的 test_pf）

| grid | filtered 勝出 base 數 | best Δ | median Δ |
|---|---|---|---|
| MXF longonly | 6 / 24 | +0.063 (0.769→0.832) | **−0.103** |
| TXF longonly | 8 / 24 | +0.063 (0.730→0.793) | **−0.144** |
| MXF biside | 4 / 24 | +0.055 (0.910→0.965) | **−0.029** |
| TXF biside | 2 / 24 | +0.037 (0.889→0.926) | **−0.079** |

濾網僅在少數**低分** base 小幅補救（最佳也只把 ~0.77 拉到 ~0.83），中位數四個全負。
最佳 filtered PF（任何 grid）= 0.832(long) / 0.971(biside)，**連 unfiltered 的天花板都沒摸到，更遠離 1.2**。

---

## 結論

**Type A FAIL（雙重確認）。** 純結構性 Donchian breakout 在 TMF 5m 沒有可部署的 edge：
- PF 天花板 ~1.08–1.15、整片曲面 sub-gate（v1 已證）
- 量能確認不僅沒把 PF 推過 1.2，反而系統性壓低（v2 證）—— 砍掉的「低量突破」其實含有效訊號

機械式調 trigger（N 長度、量能門檻）救不了它。|r| vs BreakoutTrend = 0.066（差異化乾淨，避開 Type B），
但低相關的前提是策略本身要有 edge —— 沒 edge 的低相關 = 沒用。

## 下一步（超出本 session「只做 Donchian」範圍，待 user 決定）

依設計文件 §9 Type A 路徑 → **C1.b momentum**（如 ROC / 動能排序，非 EMA 以保差異化）。
這是另一支策略，不在 Donchian 開發範圍內。Donchian 線到此為止。

### 對照（同份 TMF OOS）
- vwap_fade 0.797 / connors_rsi2 0.764 / or_fade 0.646 — 三 MR 全 FAIL
- Donchian v1 1.151 / v2(best filtered) ≤0.97 — 首支 trend-following，仍 FAIL
- BreakoutTrend 1.513（live 基準）— 目前唯一過關的 edge

*產生：2026-05-29，本 session 自動分析*
