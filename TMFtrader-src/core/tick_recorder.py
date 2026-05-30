"""Tick 錄製器 —— 把即時 tick 落地成每日 CSV,供未來忠實回放(replay)用。

設計原則:
- env 開關 `RECORD_TICKS=1` 才啟用;預設關閉 → 對現有 live 零影響。
- 緩衝 + 批次 append,低 IO 開銷;任何寫檔錯誤都吞掉,絕不讓錄製拖垮 tick 主路徑。
- 每商品每日一檔:data/ticks/{INSTRUMENT}_{YYYYMMDD}.csv
- 欄位:ts(ISO,毫秒)、instrument、price、volume、bid、ask
"""
import os
import csv
from pathlib import Path
from datetime import datetime
from loguru import logger

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TickRecorder:
    """緩衝式 tick 落地器。由 env RECORD_TICKS 控制是否啟用。"""

    def __init__(self, out_dir: Path = None, flush_every: int = 500):
        self.enabled = os.getenv("RECORD_TICKS", "").strip() in ("1", "true", "True", "yes")
        self.out_dir = Path(out_dir) if out_dir else (_PROJECT_ROOT / "data" / "ticks")
        self.flush_every = flush_every
        self._buf: list[tuple] = []
        self._open_files: dict[str, bool] = {}  # 已寫過 header 的 (instrument,date)
        self._count = 0
        if self.enabled:
            try:
                self.out_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"[TickRecorder] ENABLED -> {self.out_dir}")
            except Exception as e:
                logger.warning(f"[TickRecorder] mkdir failed, disabling: {e}")
                self.enabled = False

    def record(self, tick) -> None:
        """收一筆 tick(失敗絕不拋出到 tick 主路徑)。"""
        if not self.enabled:
            return
        try:
            dt = tick.datetime
            inst = getattr(tick, "instrument", "") or "UNKNOWN"
            self._buf.append((
                dt.isoformat(timespec="milliseconds") if isinstance(dt, datetime) else str(dt),
                inst, float(tick.price), int(tick.volume),
                float(getattr(tick, "bid_price", 0.0)), float(getattr(tick, "ask_price", 0.0)),
            ))
            self._count += 1
            if len(self._buf) >= self.flush_every:
                self.flush()
        except Exception as e:
            logger.debug(f"[TickRecorder] record skip: {e}")
            self._buf.clear()

    def flush(self) -> None:
        """把緩衝寫進每日 CSV(依 instrument + 日期分檔,append)。"""
        if not self.enabled or not self._buf:
            return
        try:
            # 依 (instrument, date) 分組
            groups: dict[tuple, list] = {}
            for row in self._buf:
                ts, inst = row[0], row[1]
                day = ts[:10].replace("-", "")
                groups.setdefault((inst, day), []).append(row)
            for (inst, day), rows in groups.items():
                path = self.out_dir / f"{inst}_{day}.csv"
                new_file = not path.exists()
                with open(path, "a", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    if new_file:
                        w.writerow(["ts", "instrument", "price", "volume", "bid", "ask"])
                    w.writerows(rows)
            self._buf.clear()
        except Exception as e:
            logger.warning(f"[TickRecorder] flush failed: {e}")
            self._buf.clear()
