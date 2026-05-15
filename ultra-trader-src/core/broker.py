"""
UltraTrader 券商 API 封裝
提供 BaseBroker 抽象 + ShioajiBroker（永豐實盤）+ MockBroker（本地模擬）
"""

import threading
import time as time_module
import random
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Callable, Optional

from loguru import logger

from core.market_data import Tick


@dataclass
class OrderResult:
    """下單結果"""
    success: bool
    order_id: str = ""
    fill_price: float = 0.0
    fill_quantity: int = 0
    message: str = ""


@dataclass
class AccountInfo:
    """帳戶資訊"""
    balance: float = 0.0          # 帳戶餘額
    equity: float = 0.0           # 帳戶權益
    margin_used: float = 0.0      # 已用保證金
    margin_available: float = 0.0  # 可用保證金
    unrealized_pnl: float = 0.0   # 未實現損益


# ============================================================
# 抽象基類
# ============================================================

class BaseBroker(ABC):
    """券商介面抽象"""

    @abstractmethod
    def connect(self) -> bool:
        """連線登入"""
        ...

    @abstractmethod
    def disconnect(self):
        """安全登出"""
        ...

    @abstractmethod
    def subscribe_tick(self, callback: Callable[[Tick], None]):
        """訂閱即時 Tick"""
        ...

    @abstractmethod
    def place_order(self, action: str, quantity: int, price: float = 0, price_type: str = "MKT") -> OrderResult:
        """
        下單
        action: "BUY" / "SELL"
        price_type: "MKT"（市價）/ "LMT"（限價）
        """
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """取消委託"""
        ...

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """查詢帳戶"""
        ...

    @abstractmethod
    def get_contract_name(self) -> str:
        """取得合約名稱"""
        ...


# ============================================================
# 永豐 Shioaji 實盤
# ============================================================

class ShioajiBroker(BaseBroker):
    """
    永豐金 Shioaji API 封裝
    支援多商品同時訂閱和下單
    """

    def __init__(self, api_key: str, secret_key: str, ca_path: str = "", ca_password: str = "",
                 person_id: str = "", simulation: bool = True,
                 contract_code: str = "MXF", contract_codes: list[str] = None):
        self._api_key = api_key
        self._secret_key = secret_key
        self._ca_path = ca_path
        self._ca_password = ca_password
        self._person_id = person_id
        self._simulation = simulation
        # 多商品支援
        self._contract_codes = contract_codes or [contract_code]
        self._contract_code = self._contract_codes[0]  # 向後相容
        self._api = None
        self._contracts: dict[str, object] = {}  # code -> contract
        self._contract = None  # 向後相容（第一個合約）
        self._code_to_instrument: dict[str, str] = {}  # "TMFR1" -> "TMF"
        self._tick_callback: Optional[Callable] = None
        self._connected = False
        self._lock = threading.Lock()
        self._account_query_failed = False
        # 成交回報（per-instrument events）
        self._pending_deals: dict[str, dict] = {}  # contract_code -> deal info
        self._deal_events: dict[str, threading.Event] = {}  # instrument -> event
        # 重連機制
        self._reconnecting = False
        self._last_reconnect_time: float = 0.0  # 上次重連時間，防止頻繁觸發
        self._last_tick_time: Optional[float] = None  # monotonic clock timestamp（含 fallback）
        self._last_real_tick_time: Optional[float] = None  # 只有真實 Solace tick 才更新
        self._last_real_tick_price: float = 0.0        # 最後一筆真實 Solace tick 的價格（供 KbarPoller fallback 用）
        self._fallback_since: Optional[float] = None   # fallback 開始時間（monotonic）
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._on_connection_lost_cb: Optional[Callable] = None
        self._on_connection_restored_cb: Optional[Callable] = None

    def set_connection_callbacks(self, on_lost: Callable = None, on_restored: Callable = None):
        """設定連線狀態回調"""
        self._on_connection_lost_cb = on_lost
        self._on_connection_restored_cb = on_restored

    def connect(self) -> bool:
        try:
            import shioaji as sj

            self._api = sj.Shioaji(simulation=self._simulation)
            accounts = self._api.login(
                api_key=self._api_key,
                secret_key=self._secret_key,
                receive_window=300000,
                fetch_contract=False,
            )
            # 手動 fetch contracts，容忍 Options 失敗（部分帳號 / IP 沒 Options 權限）
            try:
                self._api.fetch_contracts(contract_download=True, contracts_timeout=30000)
            except Exception as fc_err:
                logger.warning(f"[Shioaji] fetch_contracts partial: {fc_err}")

            if not accounts:
                logger.error("登入失敗：沒有取得帳戶資訊")
                return False

            logger.info(f"[Shioaji] login OK ({'simulation' if self._simulation else 'production'})")

            # 啟用憑證（實單需要）
            if not self._simulation and self._ca_path:
                try:
                    self._api.activate_ca(
                        ca_path=self._ca_path,
                        ca_passwd=self._ca_password,
                        person_id=self._person_id,
                    )
                    logger.info("[Shioaji] CA certificate activated")
                except Exception as ca_err:
                    logger.warning(f"[Shioaji] CA activation failed: {ca_err}")

            # 取得所有商品的近月合約
            for code in self._contract_codes:
                contract = self._get_nearby_contract(code)
                if contract:
                    self._contracts[code] = contract
                    self._code_to_instrument[contract.code] = code
                    # 也映射實際交割月合約代碼（如 MXFE6），避免 Shioaji tick 以實際代碼回傳時找不到商品
                    if hasattr(contract, 'target_code') and contract.target_code:
                        self._code_to_instrument[contract.target_code] = code
                    logger.info(f"[Contract] {code}: {contract.code} ({contract.name})")
                else:
                    logger.error(f"[Contract] {code}: 找不到合約")

            # 向後相容
            self._contract = self._contracts.get(self._contract_code)

            # 註冊委託/成交回調
            def _order_cb(stat, msg):
                logger.info(f"[Shioaji] order_cb: stat={stat} | {msg}")
                # 檢查成交（deal）
                if hasattr(msg, 'price') and hasattr(msg, 'quantity'):
                    code = getattr(msg, 'code', '')
                    logger.info(f"[Shioaji] DEAL: {code} | action={getattr(msg, 'action', '')} qty={msg.quantity} price={msg.price}")
                    # Resolve contract code to instrument name
                    instrument = self._code_to_instrument.get(code, code)
                    self._pending_deals[instrument] = {
                        "action": str(getattr(msg, 'action', '')),
                        "quantity": int(msg.quantity),
                        "price": float(msg.price),
                    }
                    # Set per-instrument event
                    evt = self._deal_events.get(instrument)
                    if evt:
                        evt.set()

            self._api.set_order_callback(_order_cb)

            self._connected = True
            return True

        except ImportError:
            logger.error("找不到 shioaji 套件，請執行: pip install shioaji")
            return False
        except Exception as e:
            logger.error(f"連線失敗: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def disconnect(self):
        self._connected = False
        if self._heartbeat_thread:
            self._heartbeat_thread = None  # daemon thread will die
        if self._api:
            try:
                self._api.logout()
                logger.info("已登出永豐")
            except Exception as e:
                logger.warning(f"登出時發生錯誤: {e}")

    def start_heartbeat_monitor(self, tick_timeout_sec: int = 30):
        """啟動 tick 心跳監控 — 超過 N 秒沒收到 tick 視為斷線（使用 monotonic clock 防 NTP 跳動）"""
        def _monitor():
            while self._connected:
                time_module.sleep(10)
                if not self._connected:
                    break
                if self._last_tick_time:
                    elapsed = time_module.monotonic() - self._last_tick_time
                    if elapsed > tick_timeout_sec and not self._reconnecting:
                        logger.error(f"[Heartbeat] {elapsed:.0f}s 未收到 tick，嘗試重連...")
                        if self._on_connection_lost_cb:
                            try:
                                self._on_connection_lost_cb()
                            except Exception:
                                pass
                        self._attempt_reconnect()

        self._heartbeat_thread = threading.Thread(target=_monitor, daemon=True)
        self._heartbeat_thread.start()
        logger.info(f"[Heartbeat] 監控啟動（{tick_timeout_sec}s timeout, monotonic clock）")

    def _attempt_reconnect(self):
        """嘗試重新連線"""
        if self._reconnecting:
            return
        self._reconnecting = True
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            logger.info(f"[Reconnect] 嘗試 {attempt}/{max_retries}...")
            try:
                # 先登出，明確釋放舊物件避免 GC 時 __del__ 觸發 segfault
                old_api = self._api
                self._api = None          # 先清空，讓後續賦值不會觸發 GC
                if old_api is not None:
                    try:
                        import signal as _sig
                        old_api.logout()
                    except Exception:
                        pass
                    finally:
                        del old_api       # 明確刪除，確保在此處完成 GC
                        import gc as _gc
                        _gc.collect()
                time_module.sleep(1)      # 給 Solace 連線清理時間

                # 重新登入
                import shioaji as sj
                self._api = sj.Shioaji(simulation=self._simulation)
                accounts = self._api.login(
                    api_key=self._api_key,
                    secret_key=self._secret_key,
                    receive_window=300000,
                    fetch_contract=False,
                )

                # 2026-05-15 修法 C：reconnect cooldown 內跳過 fetch_contracts、避免 dead zone
                # storm（13:45-14:55 / 05:00-15:00）每 11s 一次 reconnect × ~50-100MB fetch、
                # 一場 storm 可燒 200-400MB（昨日 quota 爆量主因）。
                # cooldown 期內保留前次 cached self._contracts、跨 session 重用 contract 物件。
                # 風險：cross-session subscribe 若 Shioaji 拒收會 mute、但 5min cooldown 過後
                # 下一次 reconnect 會自動全抓恢復。設 DISABLE_RECONNECT_FETCH_COOLDOWN=true 可關。
                import os as _os
                _now_mono = time_module.monotonic()
                _last_fetch = getattr(self, '_last_contracts_fetch_mono', 0)
                _cooldown_sec = float(_os.environ.get("RECONNECT_FETCH_COOLDOWN_SEC", "300"))
                _disable_cooldown = (
                    _os.environ.get("DISABLE_RECONNECT_FETCH_COOLDOWN", "").strip().lower()
                    in ("1", "true", "yes")
                )
                _should_fetch = _disable_cooldown or (_now_mono - _last_fetch) > _cooldown_sec

                if _should_fetch:
                    try:
                        self._api.fetch_contracts(contract_download=True, contracts_timeout=30000)
                        self._last_contracts_fetch_mono = _now_mono
                    except Exception as fc_err:
                        logger.warning(f"[Shioaji] fetch_contracts partial: {fc_err}")
                else:
                    _elapsed = _now_mono - _last_fetch
                    logger.info(
                        f"[Reconnect] 跳過 fetch_contracts（cooldown {_cooldown_sec:.0f}s 內、"
                        f"上次 {_elapsed:.0f}s 前、節省流量、沿用 {len(self._contracts)} 個 cached 合約）"
                    )

                if not accounts:
                    logger.error(f"[Reconnect] 登入失敗（第 {attempt} 次）")
                    time_module.sleep(5 * attempt)
                    continue

                # 啟用 CA
                if not self._simulation and self._ca_path:
                    self._api.activate_ca(
                        ca_path=self._ca_path,
                        ca_passwd=self._ca_password,
                        person_id=self._person_id,
                    )

                # 重新取得合約（修法 C：cooldown 跳過時複用 cached 合約物件、不 clear / rebuild）
                if _should_fetch:
                    self._contracts.clear()
                    self._code_to_instrument.clear()
                    for code in self._contract_codes:
                        contract = self._get_nearby_contract(code)
                        if contract:
                            self._contracts[code] = contract
                            self._code_to_instrument[contract.code] = code
                            if hasattr(contract, 'target_code') and contract.target_code:
                                self._code_to_instrument[contract.target_code] = code

                    self._contract = self._contracts.get(self._contract_code)
                # else: 沿用前次 cached self._contracts / self._code_to_instrument / self._contract

                # 重新註冊 callback + 重新訂閱
                def _order_cb(stat, msg):
                    if hasattr(msg, 'price') and hasattr(msg, 'quantity'):
                        code = getattr(msg, 'code', '')
                        instrument = self._code_to_instrument.get(code, code)
                        self._pending_deals[instrument] = {
                            "action": str(getattr(msg, 'action', '')),
                            "quantity": int(msg.quantity),
                            "price": float(msg.price),
                        }
                        evt = self._deal_events.get(instrument)
                        if evt:
                            evt.set()

                self._api.set_order_callback(_order_cb)

                # 重新訂閱 tick
                if self._tick_callback:
                    self.subscribe_tick(self._tick_callback)

                self._connected = True
                self._reconnecting = False
                logger.info(f"[Reconnect] 重連成功！")

                if self._on_connection_restored_cb:
                    try:
                        self._on_connection_restored_cb()
                    except Exception:
                        pass
                return

            except Exception as e:
                logger.error(f"[Reconnect] 第 {attempt} 次失敗: {e}")
                time_module.sleep(5 * attempt)

        self._reconnecting = False
        logger.error(f"[Reconnect] {max_retries} 次重連全部失敗，需要手動處理！")

    def subscribe_tick(self, callback: Callable[[Tick], None]):
        if not self._api or not self._contracts:
            logger.error("尚未連線，無法訂閱行情")
            return

        self._tick_callback = callback

        import shioaji as sj

        @self._api.on_tick_fop_v1()
        def on_tick(exchange, tick):
            try:
                _now = time_module.monotonic()
                self._last_tick_time = _now
                self._last_real_tick_time = _now   # 真實 Solace tick
                self._fallback_since = None        # Solace 正常，清除 fallback 計時
                close_price = float(tick.close)
                self._last_real_tick_price = close_price  # 供 KbarPoller fallback 使用

                # === Tick 資料驗證 ===
                if close_price <= 0 or close_price != close_price:  # NaN check
                    logger.warning(f"[Tick] 無效價格: {close_price}，丟棄")
                    return

                bid = close_price - 1
                ask = close_price + 1
                if hasattr(tick, 'bid_price') and tick.bid_price:
                    bid = float(tick.bid_price[0]) if isinstance(tick.bid_price, (list, tuple)) else float(tick.bid_price)
                if hasattr(tick, 'ask_price') and tick.ask_price:
                    ask = float(tick.ask_price[0]) if isinstance(tick.ask_price, (list, tuple)) else float(tick.ask_price)

                # bid > ask = 交易所資料錯誤，用 close 修正
                if bid > ask:
                    logger.warning(f"[Tick] bid({bid}) > ask({ask})，修正為 close±1")
                    bid = close_price - 1
                    ask = close_price + 1

                # 從 tick.code 反查商品代碼
                instrument = self._code_to_instrument.get(tick.code, "")
                if not instrument:
                    for code in self._contract_codes:
                        if tick.code.startswith(code):
                            instrument = code
                            break

                t = Tick(
                    datetime=tick.datetime,
                    price=close_price,
                    volume=int(tick.volume),
                    bid_price=bid,
                    ask_price=ask,
                    instrument=instrument,
                )
                if self._tick_callback:
                    self._tick_callback(t)
            except Exception as e:
                logger.error(f"Tick 轉換錯誤: {e}")

        # 訂閱所有商品
        for code, contract in self._contracts.items():
            self._api.quote.subscribe(
                contract,
                quote_type=sj.constant.QuoteType.Tick,
                version=sj.constant.QuoteVersion.v1,
            )
            logger.info(f"[Subscribe] {contract.code} tick data")

        # 啟動 REST 輪詢 fallback（Solace 失敗時仍可更新 K 棒）
        self._start_kbar_poller()

    def _start_kbar_poller(self, interval: int = 30):
        """
        REST K棒輪詢 fallback：
        當 Solace 失敗（超過 interval 秒沒收到 Tick）時，
        每 interval 秒從 api.kbars() 取最新 1 分 K 棒，合成假 Tick 注入。

        2026-05-14: 預設停用、節省 500MB/日流量配額。
        若 Solace 真斷線、改靠 circuit_breaker.on_connection_lost() 推 TG 通知人工處理、
        不再合成假 tick 餵策略（避免基於 stale 資料的錯誤訊號）。
        要復開：環境變數 ENABLE_KBAR_POLLER=true。
        """
        import os as _os
        if (_os.environ.get("ENABLE_KBAR_POLLER", "").strip().lower()
                not in ("1", "true", "yes")):
            logger.info("[KbarPoller] 已停用（節省流量、設 ENABLE_KBAR_POLLER=true 可復開）")
            return

        if getattr(self, '_kbar_poller_started', False):
            return  # 已有輪詢線程，避免重連後重複啟動
        self._kbar_poller_started = True

        def _poll():
            time_module.sleep(interval)  # 給 Solace 一段時間先嘗試
            while self._connected:
                elapsed = (time_module.monotonic() - self._last_tick_time) if self._last_tick_time else 999
                if elapsed > interval:
                    # Solace 沒在推資料 → 立即標記 fallback（不論 REST 是否成功）
                    if self._fallback_since is None:
                        self._fallback_since = time_module.monotonic()
                        logger.warning(f"[KbarPoller] Solace 斷線，切換 fallback 模式")
                    # 合成假 Tick：優先用 api.snapshots() REST 取即時價（帶 5s timeout 防 hang）
                    # 若 REST 失敗才 fallback 使用最後一筆 Solace 價格
                    import concurrent.futures as _cf
                    import datetime as _dt_mod
                    from core.market_data import Tick as _Tick

                    for code, contract in list(self._contracts.items()):
                        try:
                            # 嘗試用 REST 取最新 snapshot 價格（timeout=5s）
                            rest_price = None
                            if self._api:
                                try:
                                    with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                                        _fut = _ex.submit(self._api.snapshots, [contract])
                                        _snaps = _fut.result(timeout=5)
                                    if _snaps and len(_snaps) > 0:
                                        rest_price = float(_snaps[0].close)
                                        if rest_price > 0:
                                            self._last_real_tick_price = rest_price
                                            logger.debug(f"[KbarPoller] {code} REST snapshot: {rest_price:.0f}")
                                except _cf.TimeoutError:
                                    logger.warning(f"[KbarPoller] {code} REST snapshot timeout（5s），使用 stale price")
                                except Exception as _re:
                                    logger.debug(f"[KbarPoller] {code} REST snapshot 失敗: {_re}，使用 stale price")

                            last_price = rest_price if (rest_price and rest_price > 0) else self._last_real_tick_price
                            if last_price <= 0:
                                logger.debug("[KbarPoller] 尚無已知價格，略過本輪")
                                continue

                            instrument = self._code_to_instrument.get(contract.code, code)
                            t = _Tick(
                                datetime=_dt_mod.datetime.now(),
                                price=last_price,
                                volume=0,
                                bid_price=last_price - 1,
                                ask_price=last_price + 1,
                                instrument=instrument,
                            )
                            if self._tick_callback:
                                self._tick_callback(t)
                            # fallback 不更新 _last_tick_time，讓 heartbeat monitor 能偵測真實斷線
                            logger.debug(f"[KbarPoller] {code} synthetic tick: {last_price:.0f} @ {t.datetime.strftime('%H:%M:%S')}")
                        except Exception as e:
                            logger.warning(f"[KbarPoller] {code} 合成 tick 失敗: {e}")
                time_module.sleep(interval)
            self._kbar_poller_started = False  # 線程結束，允許斷線重連後重啟

        threading.Thread(target=_poll, daemon=True, name="KbarPoller").start()
        logger.info(f"[KbarPoller] REST fallback 啟動（{interval}s 輪詢）")

    def place_order(self, action: str, quantity: int, price: float = 0,
                    price_type: str = "MKT", instrument: str = "") -> OrderResult:
        """下單（指定商品），等待成交回報確認"""
        contract = self._contracts.get(instrument, self._contract)
        if not self._api or not contract:
            return OrderResult(success=False, message=f"找不到合約: {instrument}")

        import shioaji as sj

        # Resolve instrument key for deal matching
        instr_key = instrument or self._contract_code

        with self._lock:
            try:
                # Create per-instrument event (clear any stale state first)
                self._pending_deals.pop(instr_key, None)
                evt = threading.Event()
                self._deal_events[instr_key] = evt

                order = self._api.Order(
                    action=sj.constant.Action.Buy if action == "BUY" else sj.constant.Action.Sell,
                    price=price,
                    quantity=quantity,
                    price_type=sj.constant.FuturesPriceType.MKT if price_type == "MKT" else sj.constant.FuturesPriceType.LMT,
                    order_type=sj.constant.OrderType.IOC,
                    octype=sj.constant.FuturesOCType.Auto,
                    account=self._api.futopt_account,
                )

                logger.info(f"[Shioaji] submitting: {action} {quantity} {instr_key} @ {price_type}")
                trade = self._api.place_order(contract, order)
                order_id = trade.order.id if trade else ""

                if not trade:
                    self._deal_events.pop(instr_key, None)
                    return OrderResult(success=False, message=f"{instr_key} place_order 返回 None")

                # 等待成交回報（最多 5 秒）
                filled = evt.wait(timeout=5.0)
                if filled:
                    deal = self._pending_deals.pop(instr_key, {})
                    fill_price = deal.get("price", 0)
                    fill_qty = deal.get("quantity", quantity)
                    logger.info(f"[Shioaji] CONFIRMED: {instr_key} {action} {fill_qty} @ {fill_price}")
                    return OrderResult(
                        success=True,
                        order_id=order_id,
                        fill_price=fill_price,
                        fill_quantity=fill_qty,
                        message=f"{instr_key} 成交確認 @ {fill_price}",
                    )
                else:
                    # 5 秒內沒收到回調 — 查詢實際委託狀態避免 ghost position
                    logger.warning(f"[Shioaji] NO DEAL callback in 5s for {instr_key}, querying order status...")
                    try:
                        self._api.update_status(self._api.futopt_account)
                        if trade.status and hasattr(trade.status, 'deals') and trade.status.deals:
                            total_qty = sum(int(d.quantity) for d in trade.status.deals)
                            avg_price = sum(float(d.price) * int(d.quantity) for d in trade.status.deals) / total_qty if total_qty else 0
                            logger.info(f"[Shioaji] STATUS CHECK FILLED: {instr_key} {action} {total_qty} @ {avg_price}")
                            return OrderResult(
                                success=True,
                                order_id=order_id,
                                fill_price=avg_price,
                                fill_quantity=total_qty,
                                message=f"{instr_key} 狀態查詢確認成交 @ {avg_price}",
                            )
                        else:
                            logger.warning(f"[Shioaji] STATUS CHECK: {instr_key} {action} {quantity} — 確認未成交")
                    except Exception as se:
                        logger.error(f"[Shioaji] update_status failed: {se}")

                    return OrderResult(
                        success=False,
                        order_id=order_id,
                        message=f"{instr_key} 送單成功但未成交（IOC 可能已取消）",
                    )

            except Exception as e:
                logger.error(f"{instr_key} 下單失敗: {e}")
                return OrderResult(success=False, message=str(e))
            finally:
                # 原子清理：確保不會殘留 stale event/deal 污染下一筆訂單
                self._deal_events.pop(instr_key, None)
                self._pending_deals.pop(instr_key, None)

    def cancel_order(self, order_id: str) -> bool:
        logger.warning("取消委託功能尚未實作")
        return False

    def _is_token_expired(self, error: Exception) -> bool:
        """判斷是否為 token 過期錯誤（只匹配真正的 expired，不含 permission 錯誤）"""
        err_str = str(error).lower()
        # 明確的 token 過期訊息
        if 'expired' in err_str or 'expir' in err_str:
            return True
        # 排除 "doesn't have permission" / "not acceptable" 等非過期 401
        if '401' in err_str and 'permission' not in err_str and 'acceptable' not in err_str:
            return True
        return False

    def get_account_info(self) -> AccountInfo:
        if not self._api:
            return AccountInfo()
        try:
            account = self._api.futopt_account or self._api.stock_account
            if not account:
                return AccountInfo()

            # 查 margin（真實權益數）
            info = AccountInfo()
            try:
                m = self._api.margin(account)
                if m and hasattr(m, 'equity'):
                    info.equity = float(m.equity or 0)
                    info.balance = float(m.today_balance or 0)
                    info.margin_used = float(m.initial_margin or 0)
                    info.margin_available = float(m.available_margin or 0)
                    info.unrealized_pnl = float(m.future_open_position or 0)
            except Exception as me:
                logger.debug(f"margin 查詢: {me}")
                if self._is_token_expired(me):
                    import time as _t
                    if _t.monotonic() - self._last_reconnect_time > 60:  # 60 秒內不重複觸發
                        self._last_reconnect_time = _t.monotonic()
                        logger.warning("[Token] margin 查詢偵測到 token 過期，觸發重連")
                        threading.Thread(target=self._attempt_reconnect, daemon=True).start()

            return info
        except Exception as e:
            if not self._account_query_failed:
                logger.debug(f"get_account_info 查詢: {e}")
                self._account_query_failed = True
            if self._is_token_expired(e):
                import time as _t
                if _t.monotonic() - self._last_reconnect_time > 60:
                    self._last_reconnect_time = _t.monotonic()
                    logger.warning("[Token] get_account_info 偵測到 token 過期，觸發重連")
                    threading.Thread(target=self._attempt_reconnect, daemon=True).start()
        return AccountInfo()

    def get_real_positions(self) -> list[dict]:
        """查詢真實持倉"""
        if not self._api:
            return []
        try:
            account = self._api.futopt_account or self._api.stock_account
            if not account:
                return []
            positions = self._api.list_positions(account)
            result = []
            for p in (positions or []):
                result.append({
                    "code": getattr(p, 'code', ''),
                    "direction": str(getattr(p, 'direction', '')),
                    "quantity": int(getattr(p, 'quantity', 0)),
                    "price": float(getattr(p, 'price', 0)),
                    "last_price": float(getattr(p, 'last_price', 0)),
                    "pnl": float(getattr(p, 'pnl', 0)),
                })
            return result
        except Exception as e:
            logger.debug(f"list_positions 查詢: {e}")
            if self._is_token_expired(e):
                import time as _t
                if _t.monotonic() - self._last_reconnect_time > 60:
                    self._last_reconnect_time = _t.monotonic()
                    logger.warning("[Token] list_positions 偵測到 token 過期，觸發重連")
                    threading.Thread(target=self._attempt_reconnect, daemon=True).start()
            return []

    def get_historical_kbars(self, instrument: str = "", count: int = 60) -> list:
        """用 Shioaji API 取得歷史 K 棒（暖機用）"""
        contract = self._contracts.get(instrument, self._contract)
        if not contract or not self._api:
            return []
        try:
            import shioaji as sj
            from datetime import datetime, timedelta
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")

            # 多抓幾天歷史，重啟後 dashboard 能看到之前的 K 棒
            # 1 分 K 一天約 600 根（日盤 300 + 夜盤 300），抓 3 天 ≈ 1800 根
            if now.hour < 5:
                start_date = (now - timedelta(days=4)).strftime("%Y-%m-%d")
            else:
                start_date = (now - timedelta(days=3)).strftime("%Y-%m-%d")

            kbars = self._api.kbars(
                contract=contract,
                start=start_date,
                end=today,
            )
            if not kbars or not hasattr(kbars, 'Close'):
                return []

            from core.market_data import KBar
            result = []
            for i in range(len(kbars.Close)):
                # kbars.ts[i] 可能是：
                #   1. int/float (nanoseconds since epoch, UTC)
                #   2. pandas Timestamp (可能帶 UTC timezone)
                #   3. datetime
                # 統一轉成台灣本地時間 naive datetime
                # 重要：Unix epoch 永遠是 UTC，需要 +8h 轉台灣時間
                raw_ts = kbars.ts[i]
                if isinstance(raw_ts, (int, float)):
                    epoch_sec = raw_ts / 1e9 if raw_ts > 1e12 else raw_ts
                    # Shioaji kbars.ts 已是 UTC+8 baked-in epoch（非標準 UTC）
                    # 必須用 utcfromtimestamp 解讀，才能還原台灣本地時間 naive datetime
                    ts = datetime.utcfromtimestamp(epoch_sec)
                elif hasattr(raw_ts, 'to_pydatetime'):
                    ts = raw_ts.to_pydatetime()
                    if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
                        ts = ts.astimezone().replace(tzinfo=None)
                else:
                    ts = raw_ts
                bar = KBar(
                    datetime=ts,
                    open=float(kbars.Open[i]),
                    high=float(kbars.High[i]),
                    low=float(kbars.Low[i]),
                    close=float(kbars.Close[i]),
                    volume=int(kbars.Volume[i]),
                    interval=1,
                )
                result.append(bar)

            logger.info(f"[Shioaji] {instrument} 歷史 K 棒: {len(result)} bars")
            return result[-count:]
        except Exception as e:
            logger.warning(f"[Shioaji] 取得歷史 K 棒失敗: {e}")
            return []

    def get_contract_name(self) -> str:
        """回傳所有合約名稱"""
        if self._contracts:
            names = [f"{c.code}({c.name})" for c in self._contracts.values()]
            return " + ".join(names)
        return ",".join(self._contract_codes)

    def get_tick_status(self) -> dict:
        """回傳 Tick 連線狀態（供 watchdog 監控）"""
        now = time_module.monotonic()
        fallback = self._fallback_since is not None
        fallback_minutes = round((now - self._fallback_since) / 60, 1) if fallback else 0.0
        real_ago = round((now - self._last_real_tick_time) / 60, 1) if self._last_real_tick_time else None
        return {
            "solace_ok": not fallback,
            "fallback_active": fallback,
            "fallback_minutes": fallback_minutes,
            "last_real_tick_minutes_ago": real_ago,
        }

    def _get_nearby_contract(self, code: str = None):
        """自動取得指定商品的近月合約"""
        code = code or self._contract_code
        # 支援 shioaji_code 映射（如 TMFN → MXF）
        try:
            from core.instrument_config import INSTRUMENT_SPECS
            spec = INSTRUMENT_SPECS.get(code)
            sj_code = (spec.shioaji_code or code) if spec else code
        except Exception:
            sj_code = code
        try:
            futures = self._api.Contracts.Futures
            product = getattr(futures, sj_code, None)
            if product is None:
                logger.error(f"找不到商品: {sj_code} (instrument={code})")
                return None

            r1_code = f"{sj_code}R1"
            if hasattr(product, r1_code):
                return product[r1_code]

            contracts = [c for c in product if c.code[-2:] not in ("R1", "R2")]
            if contracts:
                return min(contracts, key=lambda c: c.delivery_date)
            return None
        except Exception as e:
            logger.error(f"取得 {code} 合約失敗: {e}")
            return None


# ============================================================
# 本地模擬券商（MockBroker）
# ============================================================

class MockBroker(BaseBroker):
    """
    本地模擬券商 — 多商品版本
    為每個商品產生獨立的合成 Tick 資料
    """

    def __init__(self, initial_price: float = 22000.0, tick_interval: float = 0.5,
                 volatility: float = 0.3, initial_balance: float = 100000.0,
                 instruments: dict = None):
        """
        instruments: {code: {"initial_price": float, "volatility": float}}
        """
        self._tick_interval = tick_interval
        self._initial_balance = initial_balance
        self._balance = initial_balance
        self._tick_callback: Optional[Callable] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._order_counter = 0
        self._position_pnl = 0.0

        # 多商品價格追蹤
        self._instruments = instruments or {"TMF": {"initial_price": initial_price, "volatility": volatility}}
        self._prices: dict[str, float] = {}
        self._initial_prices: dict[str, float] = {}
        for code, cfg in self._instruments.items():
            p = cfg.get("initial_price", initial_price)
            self._prices[code] = p
            self._initial_prices[code] = p

        # 向後相容
        self._initial_price = initial_price
        self._volatility = volatility
        self._price = initial_price

    def connect(self) -> bool:
        logger.info("[MockBroker] connected (multi-instrument)")
        logger.info(f"[MockBroker] balance: {self._balance:,.0f}")
        for code, p in self._prices.items():
            logger.info(f"[MockBroker] {code}: {p:,.1f}")
        return True

    def disconnect(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.info("模擬券商已斷線")

    def subscribe_tick(self, callback: Callable[[Tick], None]):
        self._tick_callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._generate_ticks, daemon=True)
        self._thread.start()
        logger.info(f"[MockBroker] tick generation started for {list(self._instruments.keys())}")

    def place_order(self, action: str, quantity: int, price: float = 0,
                    price_type: str = "MKT", instrument: str = "") -> OrderResult:
        with self._lock:
            self._order_counter += 1
            current_price = self._prices.get(instrument, self._price)
            slippage = random.randint(0, 2)
            fill_price = current_price + slippage if action == "BUY" else current_price - slippage

            commission = 18.0 * quantity
            self._balance -= commission

            order_id = f"MOCK-{self._order_counter:06d}"
            logger.info(f"[MockBroker] {instrument} fill: {action} {quantity} @ {fill_price}")

            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=fill_price,
                fill_quantity=quantity,
                message=f"{instrument} 模擬成交 @ {fill_price}",
            )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(
            balance=self._balance,
            equity=self._balance + self._position_pnl,
            margin_used=0,
            margin_available=self._balance,
            unrealized_pnl=self._position_pnl,
        )

    def get_contract_name(self) -> str:
        return " + ".join(f"{c} 模擬" for c in self._instruments.keys())

    def update_pnl(self, pnl: float):
        self._position_pnl = pnl

    def update_balance(self, amount: float):
        self._balance += amount

    def generate_warmup_ticks(self, instrument: str, minutes: int = 60, ticks_per_bar: int = 30) -> list[Tick]:
        """產生指定商品的暖機 Tick"""
        ticks = []
        now = datetime.now()
        start_time = now - timedelta(minutes=minutes)
        initial_price = self._initial_prices.get(instrument, 22000.0)
        vol = self._instruments.get(instrument, {}).get("volatility", 0.3)
        price = initial_price

        segments = []
        remaining = minutes
        while remaining > 0:
            seg_len = min(remaining, random.randint(12, 25))
            direction = random.choice([1, 1, -1, -1, 0])
            strength = random.uniform(1.5, 4.0) if direction != 0 else 0
            segments.append((seg_len, direction, strength))
            remaining -= seg_len

        bar_idx = 0
        for seg_len, direction, strength in segments:
            for s in range(seg_len):
                if bar_idx >= minutes:
                    break
                bar_time = start_time + timedelta(minutes=bar_idx)
                hour = bar_time.hour + bar_time.minute / 60.0
                time_vol = self._get_time_volatility(hour)

                for t in range(ticks_per_bar):
                    tick_time = bar_time + timedelta(seconds=t * (60 / ticks_per_bar))
                    trend = direction * strength / ticks_per_bar
                    noise = random.gauss(0, vol * time_vol * 0.4)
                    mean_rev = (initial_price - price) * 0.0005
                    change = trend + noise + mean_rev
                    if random.random() < 0.005:
                        change *= random.uniform(2, 4)

                    price = round(price + change, 1)
                    price = max(price, initial_price * 0.95)
                    price = min(price, initial_price * 1.05)

                    volume = max(1, int(random.gauss(5, 3) * time_vol))
                    ticks.append(Tick(
                        datetime=tick_time,
                        price=price,
                        volume=volume,
                        bid_price=price - 1,
                        ask_price=price + 1,
                        instrument=instrument,
                    ))
                bar_idx += 1

        self._prices[instrument] = price
        logger.info(f"[MockBroker] {instrument} warmup: {minutes} min, {len(ticks)} ticks, {initial_price:.1f} -> {price:.1f}")
        return ticks

    def _generate_ticks(self):
        """為所有商品交替產生模擬 Tick"""
        while self._running:
            try:
                now = datetime.now()
                hour = now.hour + now.minute / 60.0
                time_volatility = self._get_time_volatility(hour)

                for code, cfg in self._instruments.items():
                    vol = cfg.get("volatility", 0.3)
                    initial_p = self._initial_prices[code]
                    price = self._prices[code]

                    change = random.gauss(0, vol * time_volatility)
                    mean_reversion = (initial_p - price) * 0.001
                    change += mean_reversion

                    if random.random() < 0.005:
                        change *= random.uniform(3, 8)

                    price = round(price + change, 1)
                    price = max(price, initial_p * 0.9)
                    price = min(price, initial_p * 1.1)
                    self._prices[code] = price

                    volume = max(1, int(random.gauss(5, 3) * time_volatility))

                    tick = Tick(
                        datetime=now,
                        price=price,
                        volume=volume,
                        bid_price=price - 1,
                        ask_price=price + 1,
                        instrument=code,
                    )

                    if self._tick_callback:
                        self._tick_callback(tick)

                time_module.sleep(self._tick_interval)

            except Exception as e:
                logger.error(f"模擬 Tick 產生錯誤: {e}")
                time_module.sleep(1)

    @staticmethod
    def _get_time_volatility(hour: float) -> float:
        if 8.75 <= hour <= 9.5:
            return 2.0
        elif 9.5 <= hour <= 11.0:
            return 1.0
        elif 11.0 <= hour <= 12.5:
            return 0.6
        elif 12.5 <= hour <= 13.75:
            return 1.8
        elif 15.0 <= hour <= 16.0:
            return 1.5
        elif 16.0 <= hour <= 23.0:
            return 0.8
        else:
            return 0.5
