# 5/20 Discord 第 3 次追問 — Shioaji 1.3.3 live mode GPF/terminate

> 給 user 直接 copy 貼到 Sinopac Shioaji Discord 的訊息底稿。
> 已包含完整 stderr、已試過的 fix、min-verify 對照。

---

## 5/20 update：兩輪 fix 都失敗、求 expert 第 3 次協助

### 補做的事（依照你第 2 次建議）

跑 `_min_verify_live.py`（main thread only、noop callback、`simulation=False`、subscribe TMFR1 tick、5 分鐘 sleep）→ **完整 5 分鐘無 crash**。確認 SDK 本身不是元兇、bug 在我們完整 process 內某處。

### 已試過的 2 輪 fix（都失敗）

**Fix 1**：把 callback function 存 `self._sdk_*_cb_ref` 強 ref 防 GC。

→ process 死法從 `pybind11::error_already_set + std::terminate` 變成 kernel `general protection fault`。timing 沒變（~60-90 秒）。

**Fix 2**：除 `set_order_callback` + `set_on_tick_fop_v1_callback`、額外註冊 noop 到 10 個 slot：
```
api.quote.set_event_callback
api.quote.set_session_down_callback
api.quote.set_init_callback
api.quote.set_msg_callback
api.quote.set_quote_callback
api.quote.set_on_bidask_fop_v1_callback
api.quote.set_on_bidask_stk_v1_callback
api.quote.set_on_quote_fop_v1_callback
api.quote.set_on_quote_stk_v1_callback
api.quote.set_on_tick_stk_v1_callback
api.set_session_down_callback
```

→ process 還是 die、活更短（10 秒）。Journal: GPF at fixed ip `0x580ec2`。

### 完整 die 訊息（log + journal）

**terminate pattern**（早上）：
```
terminate called after throwing an instance of 'pybind11::error_already_set'
  what():  TypeError: TradingEngine._engine_loop() takes 1 positional argument but 5 were given
terminate called after throwing an instance of 'pybind11::error_already_set'
  what():  TypeError: 'dict' object is not callable
terminate called after throwing an instance of 'pybind11::error_already_set'
  what():  TypeError: 'tuple' object is not callable
```

→ 3 個獨立 process 死訊各不同 type、共通是「4 args 想 invoke 一個 obj、obj 不是 callable」。「5 args bound method、bound self + 4 args」對應 event callback signature `(resp_code, event_code, info, event)`。

**GPF pattern**（下午、加了 fix 後）：
```
May 20 09:26:36 kernel: traps: python3.12[633624] general protection fault ip:580ec2 sp:7d037a098c90 error:0 in python3.12[420000+2ce000]
May 20 09:33:45 kernel: traps: python3.12[634289] general protection fault ip:580ec2 sp:73dd2b7fdc90 error:0 in python3.12[420000+2ce000]
```

→ 固定 `ip:580ec2` 表示固定 code path 撞 use-after-free。早上某些 die 也是 `segfault at 0xa` / `segfault at 0x1`（無效記憶體位置 dereference）。

### min-verify vs 完整 process 差異

| 項目 | min-verify (5 min OK) | 完整 process (60-90s die) |
|---|---|---|
| simulation | False | False |
| login + activate_ca | ✅ | ✅ |
| fetch_contracts | ✅ | ✅ |
| set_order_callback | noop | _order_cb (mutate self._pending_deals) |
| set_on_tick_fop_v1_callback | noop | on_tick → 轉 Tick → engine._on_tick → queue.put |
| subscribe TMFR1 tick | ✅ | ✅ |
| `api.kbars()` 抓 2729 bars warmup | ❌ 沒呼叫 | ✅ 呼叫 (engine.start) |
| `api.list_positions()` (401 fail) | ❌ | ✅ 失敗、回 401 但有呼叫 |
| 額外 background threads | ❌ main only | ✅ engine_loop / heartbeat_monitor / data_collector (3 threads) |
| data_collector 行為 | ❌ | 每 60s 抓 VIX (yfinance) / P/C (TAIFEX) / spot (TWSE) |

每次 die 都在 data_collector spot fetch 後 3-30 秒。

### 我們的 callback body（broker.py）

```python
# _order_cb（main thread 註冊、SDK 內部 thread invoke）
def _order_cb(stat, msg):
    try:
        if hasattr(msg, 'price') and hasattr(msg, 'quantity'):
            code = getattr(msg, 'code', '')
            instrument = self._code_to_instrument.get(code, code)
            self._pending_deals[instrument] = {...}
            evt = self._deal_events.get(instrument)
            if evt:
                evt.set()
    except Exception as _cb_err:
        logger.error(...)

self._sdk_order_cb_ref = _order_cb   # strong ref 防 GC
self._api.set_order_callback(_order_cb)

# on_tick（同理）
def on_tick(exchange, tick):
    try:
        self._last_tick_time = monotonic()
        close_price = float(tick.close)
        bid, ask = ...
        instrument = self._code_to_instrument.get(tick.code, "")
        t = Tick(datetime=tick.datetime, price=close_price, ...)
        if self._tick_callback:
            self._tick_callback(t)   # 呼叫 engine._on_tick → queue.put_nowait
    except Exception as e:
        logger.error(...)

self._sdk_tick_cb_ref = on_tick
self._api.quote.set_on_tick_fop_v1_callback(on_tick)
```

### 想請 expert 看的方向

1. 「4 args invoke 隨機 obj (`_engine_loop` / dict / tuple)」這個 pattern、是不是內部 event-callback table 被某種 race 踩到？我們已 set_event_callback noop、為何還死？
2. **fixed `ip:580ec2`** 是不是固定的 SDK 內部 code（例如 SolaceAPI 某 polling loop）撞 use-after-free？
3. Python multi-thread + SDK 是否有任何 thread-safety 規則我沒讀到？（doc 完全沒寫）
4. paper（simulation=True）跟 live（simulation=False）的 callback dispatcher 內部 thread model 是否不同？
5. 1.3.1 release note 寫過「race condition in contracts」、是否還有別的 known race 在 1.3.3 內？

### 我們的環境
- shioaji 1.3.3 (latest per release page、production)
- Python 3.12.13 on Ubuntu 22.04 (GCP VPS)
- 帳號權限：Data + Trade（list_positions 401 失敗中、客服已 ticket）
- 商品：TMFR1 微型台指期貨近月
- IP 固定（VPS）、平日連續運作
- min-verify 跑得起 = 不是 IP block / account 問題

### 已附 stderr 全文 (供參)

[user 可附上 ultratrader_20260520.log line 1170-1350 + journalctl 那段]
