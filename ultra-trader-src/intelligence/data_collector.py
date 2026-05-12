"""
UltraTrader Intelligence — 資料收集器
從 TAIFEX（期交所 CSV 下載）、TWSE（JSON API）、yfinance 抓取資料

TAIFEX OpenAPI 端點只是 Swagger UI 文件頁面，不提供 JSON API。
實際資料從 www.taifex.com.tw 的 CSV 下載端點抓取。
"""

import copy
import threading
import time
from datetime import datetime, date, timedelta
from typing import Optional, Callable

import requests
from loguru import logger


from intelligence.models import (
    InstitutionalFutures,
    InstitutionalSpot,
    OptionsData,
    LargeTraderOI,
    MarginData,
    InternationalData,
    IntelligenceSnapshot,
)


# 請求標頭（模擬瀏覽器）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# TWSE 端點（JSON API，穩定可用）
TWSE_BASE = "https://www.twse.com.tw/rwd/zh"


class DataCollector:
    """
    資料收集器 — 定時抓取各數據源

    排程邏輯：
    - TAIFEX P/C Ratio：盤後更新，每日抓一次
    - TWSE 外資買賣超：盤後 JSON API
    - 國際市場 yfinance：每 5 分鐘更新
    """

    def __init__(self):
        self._snapshot = IntelligenceSnapshot(timestamp=datetime.now())
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 各資料源最後更新時間
        self._last_fetch = {
            "taifex": None,
            "twse": None,
            "international": None,
        }

        # 更新間隔（秒）
        self.TAIFEX_INTERVAL = 3600       # TAIFEX: 1 小時
        self.TWSE_INTERVAL = 3600         # TWSE: 1 小時
        self.INTL_INTERVAL = 300          # 國際市場: 5 分鐘

        # 回調
        self._on_update: Optional[Callable] = None

    @property
    def snapshot(self) -> IntelligenceSnapshot:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def set_on_update(self, callback: Callable):
        """設定資料更新時的回調"""
        self._on_update = callback

    def start(self):
        """啟動背景資料收集"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._collection_loop, daemon=True)
        self._thread.start()
        logger.info("[Intelligence] data collector started")

    def stop(self):
        """停止資料收集"""
        self._running = False
        logger.info("[Intelligence] data collector stopped")

    def fetch_all(self):
        """手動觸發一次完整資料更新"""
        logger.info("[Intelligence] full data refresh...")
        self._fetch_taifex_data()
        self._fetch_twse_data()
        self._fetch_international_data()
        self._update_timestamp()
        logger.info("[Intelligence] data refresh complete")

    # ============================================================
    # 背景排程
    # ============================================================

    def _collection_loop(self):
        """背景資料收集迴圈"""
        # 首次啟動：立即抓取（國際市場最快，其他延遲）
        self._safe_fetch("international", self._fetch_international_data)
        time.sleep(3)
        self._safe_fetch("taifex", self._fetch_taifex_data)
        time.sleep(2)
        self._safe_fetch("twse", self._fetch_twse_data)
        self._update_timestamp()

        while self._running:
            try:
                now = datetime.now()

                # 國際市場：每 5 分鐘
                if self._should_fetch("international", self.INTL_INTERVAL):
                    self._safe_fetch("international", self._fetch_international_data)

                # TAIFEX：每小時（盤後 15:00 後才有資料）
                if self._should_fetch("taifex", self.TAIFEX_INTERVAL):
                    self._safe_fetch("taifex", self._fetch_taifex_data)

                # TWSE：每小時
                if self._should_fetch("twse", self.TWSE_INTERVAL):
                    self._safe_fetch("twse", self._fetch_twse_data)

                self._update_timestamp()
                time.sleep(30)

            except Exception as e:
                logger.error(f"[Intelligence] collection loop error: {e}")
                time.sleep(60)

    def _should_fetch(self, source: str, interval: int) -> bool:
        last = self._last_fetch.get(source)
        if last is None:
            return True
        return (datetime.now() - last).total_seconds() >= interval

    def _safe_fetch(self, source: str, fetch_fn: Callable):
        try:
            fetch_fn()
            self._last_fetch[source] = datetime.now()
        except Exception as e:
            logger.warning(f"[Intelligence] {source} fetch failed: {e}")

    def _update_timestamp(self):
        with self._lock:
            self._snapshot.timestamp = datetime.now()
            self._snapshot.data_freshness = {
                k: v.isoformat() if v else None
                for k, v in self._last_fetch.items()
            }
        if self._on_update:
            try:
                self._on_update(self._snapshot)
            except Exception:
                pass

    # ============================================================
    # TAIFEX 資料（CSV 下載端點）
    # ============================================================

    def _fetch_taifex_data(self):
        """從 TAIFEX 抓取 P/C Ratio（CSV 格式）"""
        self._fetch_put_call_ratio()

    def _fetch_put_call_ratio(self):
        """
        P/C Ratio — 從 TAIFEX CSV 下載端點
        POST https://www.taifex.com.tw/cht/3/pcRatioDown
        回傳 MS950 編碼的 CSV
        """
        try:
            # 嘗試今天和前一個交易日
            for days_back in range(0, 5):
                query_date = date.today() - timedelta(days=days_back)
                date_str = query_date.strftime("%Y/%m/%d")

                resp = requests.post(
                    "https://www.taifex.com.tw/cht/3/pcRatioDown",
                    data={"queryDate": date_str, "queryType": "1"},
                    headers=HEADERS,
                    timeout=15,
                )
                resp.raise_for_status()

                # 解碼 MS950 CSV
                content = resp.content.decode("ms950", errors="replace")
                lines = content.strip().split("\n")

                # 跳過標頭行，取資料行
                if len(lines) < 2:
                    continue

                # 找到有效數據行
                data_line = None
                for line in lines[1:]:
                    parts = line.strip().split(",")
                    if len(parts) >= 7 and "/" in parts[0]:
                        data_line = parts
                        break

                if not data_line:
                    continue

                opts = OptionsData(date=query_date)
                try:
                    # CSV 格式: 日期,Put成交量,Call成交量,成交量比%,Put未平倉,Call未平倉,未平倉比%
                    put_vol = int(data_line[1].strip())
                    call_vol = int(data_line[2].strip())
                    pc_vol_pct = float(data_line[3].strip())  # 已經是百分比

                    put_oi = int(data_line[4].strip())
                    call_oi = int(data_line[5].strip())
                    pc_oi_pct = float(data_line[6].strip())   # 已經是百分比

                    # 轉換為比率（百分比 / 100）
                    opts.pc_ratio_volume = pc_vol_pct / 100.0
                    opts.pc_ratio_oi = pc_oi_pct / 100.0

                except (ValueError, IndexError):
                    continue

                with self._lock:
                    self._snapshot.options = opts

                logger.info(
                    f"[TAIFEX] P/C Ratio ({query_date}): "
                    f"volume={opts.pc_ratio_volume:.2f} "
                    f"OI={opts.pc_ratio_oi:.2f} "
                    f"signal={opts.pc_signal}"
                )
                return  # 成功拿到資料，跳出

            logger.debug("[TAIFEX] P/C Ratio: no data in recent 5 days")

        except requests.RequestException as e:
            logger.warning(f"[TAIFEX] P/C Ratio request failed: {e}")
        except Exception as e:
            logger.warning(f"[TAIFEX] P/C Ratio parse error: {e}")

    # ============================================================
    # TWSE 資料（JSON API）
    # ============================================================

    def _fetch_twse_data(self):
        """從 TWSE 抓取三大法人現貨買賣超"""
        self._fetch_foreign_spot()

    def _fetch_foreign_spot(self):
        """
        三大法人現貨買賣超
        GET https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json&date=YYYYMMDD
        """
        try:
            # 嘗試今天和前幾個交易日
            for days_back in range(0, 5):
                query_date = date.today() - timedelta(days=days_back)
                date_str = query_date.strftime("%Y%m%d")
                url = f"{TWSE_BASE}/fund/BFI82U?date={date_str}&response=json"

                resp = requests.get(url, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                if data.get("stat") != "OK" or "data" not in data:
                    continue

                spot = InstitutionalSpot(date=query_date)
                rows = data["data"]

                # TWSE BFI82U 固定格式（6 行）:
                # [0] 自營商(自行買賣)  → 自營
                # [1] 自營商(避險)      → 自營
                # [2] 投信              → 投信
                # [3] 外資及陸資        → 外資
                # [4] 外資自營商        → 外資（通常為 0）
                # [5] 合計
                def parse_net(row_data):
                    """解析買賣差額（第 4 欄）"""
                    if len(row_data) < 4:
                        return 0
                    try:
                        return int(str(row_data[3]).replace(",", ""))
                    except (ValueError, TypeError):
                        return 0

                if len(rows) >= 5:
                    dealer_1 = parse_net(rows[0])  # 自營(自行)
                    dealer_2 = parse_net(rows[1])  # 自營(避險)
                    trust_net = parse_net(rows[2])  # 投信
                    foreign_net = parse_net(rows[3])  # 外資及陸資
                    foreign_dealer = parse_net(rows[4]) if len(rows) > 4 else 0  # 外資自營商

                    spot.foreign_buy_sell = round((foreign_net + foreign_dealer) / 1e8, 2)
                    spot.trust_buy_sell = round(trust_net / 1e8, 2)
                    spot.dealer_buy_sell = round((dealer_1 + dealer_2) / 1e8, 2)

                spot.total_buy_sell = round(
                    spot.foreign_buy_sell + spot.trust_buy_sell + spot.dealer_buy_sell, 2
                )

                with self._lock:
                    self._snapshot.institutional_spot = spot

                logger.info(
                    f"[TWSE] spot ({query_date}): "
                    f"foreign={spot.foreign_buy_sell:+.1f}B "
                    f"trust={spot.trust_buy_sell:+.1f}B "
                    f"dealer={spot.dealer_buy_sell:+.1f}B"
                )
                return

            logger.debug("[TWSE] no spot data in recent 5 days")

        except requests.RequestException as e:
            logger.warning(f"[TWSE] spot request failed: {e}")
        except Exception as e:
            logger.warning(f"[TWSE] spot parse error: {e}")

    # ============================================================
    # 國際市場（yfinance）
    # ============================================================

    def _fetch_international_data(self):
        """從 yfinance 抓取 VIX、美股、費半等國際市場指標"""
        try:
            import yfinance as yf

            intl = InternationalData(timestamp=datetime.now())

            tickers = {
                "^VIX": "vix",
                "ES=F": "sp500",
                "NQ=F": "nasdaq",
                "^SOX": "sox",
                "CL=F": "crude",
                "DX-Y.NYB": "dxy",
                "^TNX": "us10y",
            }

            for symbol, key in tickers.items():
                try:
                    ticker = yf.Ticker(symbol)
                    hist = ticker.history(period="2d")
                    if hist.empty or len(hist) < 1:
                        continue

                    current = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current
                    change_pct = ((current - prev) / prev * 100) if prev != 0 else 0

                    if key == "vix":
                        intl.vix = current
                        intl.vix_change = round(change_pct, 2)
                    elif key == "sp500":
                        intl.sp500_futures = current
                        intl.sp500_change_pct = round(change_pct, 2)
                    elif key == "nasdaq":
                        intl.nasdaq_futures = current
                        intl.nasdaq_change_pct = round(change_pct, 2)
                    elif key == "sox":
                        intl.sox_index = current
                        intl.sox_change_pct = round(change_pct, 2)
                    elif key == "crude":
                        intl.crude_oil = current
                        intl.crude_change_pct = round(change_pct, 2)
                    elif key == "dxy":
                        intl.dxy = current
                        intl.dxy_change_pct = round(change_pct, 2)
                    elif key == "us10y":
                        intl.us10y_yield = current
                        intl.us10y_change = round(current - prev, 3)

                except Exception as e:
                    logger.debug(f"[yfinance] {symbol} failed: {e}")
                    continue

            with self._lock:
                self._snapshot.international = intl

            logger.info(
                f"[International] VIX={intl.vix:.1f} "
                f"SP500={intl.sp500_change_pct:+.1f}% "
                f"NQ={intl.nasdaq_change_pct:+.1f}% "
                f"SOX={intl.sox_change_pct:+.1f}%"
            )

        except ImportError:
            logger.warning("[Intelligence] yfinance not installed, skipping international data")
        except Exception as e:
            logger.warning(f"[Intelligence] international data fetch failed: {e}")
