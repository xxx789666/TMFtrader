---
name: GPU optimizer and backtest fix completed
description: Fixed max_drawdown backtest blocker + built GPU indicator precompute + parallel optimizer
type: project
---

## What was done (2026-04-11)

**Bug fixed: `risk/manager.py` line 155**
- Max drawdown check (`drawdown_pct > max_dd`) was not guarded by `_backtest_mode`
- After losing 6% in Jan-Feb 2024, ALL subsequent 24 months of trades were blocked
- Fix: wrap with `if self._peak_equity > 0 and not self._backtest_mode:`

**Why:** The circuit breaker daily reset was already in engine.py, but the global drawdown guard in manager.py silently blocked all trades from the 3rd month onward.

**New files created:**
- `core/gpu_indicators.py` — Numba JIT (CUDA when available) precomputes all 610k bars' indicators in 0.4s. Falls back to numpy. Exports `precompute_all(df)` and `snapshot_from_precomputed(i, indicators, ...)`
- `backtest/fast_engine.py` — BacktestEngine that reads from precomputed indicator arrays (O(1) lookup vs O(200) window scan), ~10x faster per simulation
- `scripts/optimize_strategy.py` (rewritten v3) — GPU precompute + multiprocessing.shared_memory + ProcessPoolExecutor(12 workers) + walk-forward validation (18mo train / 6mo test)

**Optimization results (2026-04-11):**
- 441 parameter combos completed in 6.4 minutes (12 workers)
- Best params: SL=1.75, Sig=0.66, Trail=0.5
- Applied to strategy/signals.py (both __init__ and else-branch targets)
- Test set (2025-07~2026-04): 130 trades, WR=42.3%, PF=0.871, DD=3.9%
- Full 2-year backtest: still -84.6% — strategy needs fundamental improvements beyond parameter tuning

**How to apply:** Walk-forward validated params are in `data/optimize_results/best_params_20260411_1318.json`
