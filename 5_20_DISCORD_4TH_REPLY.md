# Discord 第 4 次回覆（分段、各段獨立可貼）

## 段 1：bisect 結果 — setter 不是 culprit

照你建議跑完 12-round bisect、120s/round、累加註冊 13 個 setter（tick_fop, order, session_down quote, event, init, msg, quote, bidask_fop_v1, bidask_stk_v1, quote_fop_v1, quote_stk_v1, tick_stk_v1, api.session_down top-level）。

**全部 12 rounds SURVIVED 120s**。setter combination 不是 culprit。

quota 26 分鐘跑下來只漲不到 1MB（用 cached contracts）→ 也排除「contract refetch race」。

## 段 2：環境因子

- Python: venv 3.12.13 (system 3.10.12 不用)
- glibc: 2.35 (Ubuntu 22.04)
- VPS: GCP 2 CPU / 1.9 GB RAM (45% used / 926MB available)
- 無 cgroup / container 限制
- shioaji 1.3.3 (latest per release page)

## 段 3：simulation 對照已驗證

paper 模式（simulation=True）跑連續 4 天 24h 完全穩定。同一份 broker.py、同一個 callback body、唯一差別是 simulation flag。

→ 你的「跟 production 帳號 order/deal flow 有關」推測 100% 正確。

## 段 4：min-verify v2 vs 完整 process 還剩什麼差異

min-verify + bisect 都不撞、剩這幾個沒驗證的東西在我們完整 process 裡：

1. `api.kbars()` 抓歷史 K 棒 2729 bars 暖機（live mode 才跑、paper 也跑但無 callback invoke 後續）
2. `api.list_positions(account)` 失敗 → SDK 回 `StatusCode: 401, Detail: Token doesn't have permission`
3. 多執行緒：engine_loop daemon thread / heartbeat_monitor thread / data_collector thread × 3
4. data_collector 每 60s fetch VIX (yfinance) / P/C (TAIFEX) / spot (TWSE) — 不碰 SDK、但增 GC pressure

## 段 5：問 expert

1. `list_positions` 401 之後 SDK 內部 state 是否會壞？我們客服 ticket 還沒升等、但 paper 模式同樣 401 也沒事
2. `api.kbars()` 在 simulation=False 跟 simulation=True 內部走不同 code path？
3. 多執行緒從不同 thread 呼叫 `api.list_positions()` / `api.margin()` 是否要 lock？doc 完全沒提
4. 建議下一步 bisect 加什麼動作？warmup kbars or list_positions 或 multi-thread invoke？

附 ip:580ec2 那個 GPF 已連續重現 2 次、表示固定 C++ code path 撞 use-after-free。下一輪 bisect 可以加 `api.kbars()` + `api.list_positions()` 在 13 個 setter 之後看是否觸發。
