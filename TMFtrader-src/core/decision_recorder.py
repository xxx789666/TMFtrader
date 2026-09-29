"""決策帶錄製 — 每次 on_kbar(掃描進場)把策略「當下看到的真值」整包寫檔。

env `RECORD_DECISIONS=1` 開啟(預設關、零影響,同 RECORD_TICKS 套路)。
用途:日後回測**直接讀 live 當時算出來的 ATR/ADX/OR 真值**(不重算)→ 消除 indicator/暖身誤差;
也能拿來逐筆比對「回測 vs live 決策」差在哪根。檔:data/decisions/{owner}_{YYYYMMDD}.csv。

on_kbar 頻率極低(每根 bar 一次),直接 append 即可、無效能顧慮。失敗一律靜默(不影響交易)。
"""
import csv
import os
from pathlib import Path

_ENABLED = os.getenv("RECORD_DECISIONS", "").strip() in ("1", "true", "True", "yes")
_DIR = Path(__file__).resolve().parent.parent / "data" / "decisions"
_OWNER = (os.getenv("STRATEGY_OWNER", "").strip()
          or os.getenv("STRATEGY_TYPE", "").strip() or "default")
_seen: set = set()   # 已寫過 header 的檔

FIELDS = ["bar_time", "instrument", "owner", "strategy", "price", "atr", "adx",
          "ema60", "ema200", "or_hi", "or_lo", "or_ready", "traded", "trail_armed",
          "signal", "reason"]


def enabled() -> bool:
    return _ENABLED


def record(kbar, snapshot, strategy, signal, instrument: str) -> None:
    """記一筆 on_kbar 決策。signal 為 None(無訊號)或 Signal(有訊號)。"""
    if not _ENABLED:
        return
    try:
        day = kbar.datetime.strftime("%Y%m%d")
        path = _DIR / f"{_OWNER}_{day}.csv"
        write_header = path not in _seen and not path.exists()
        _DIR.mkdir(parents=True, exist_ok=True)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if write_header:
                w.writerow(FIELDS)
            _seen.add(path)
            sig_dir = ""
            sig_reason = ""
            if signal is not None:
                d = getattr(signal, "direction", None)
                sig_dir = getattr(d, "value", str(d)) if d is not None else ""
                sig_reason = getattr(signal, "reason", "")
            w.writerow([
                kbar.datetime.isoformat(), instrument, _OWNER, getattr(strategy, "name", ""),
                getattr(snapshot, "price", ""), getattr(snapshot, "atr", ""), getattr(snapshot, "adx", ""),
                getattr(snapshot, "ema60", ""), getattr(snapshot, "ema200", ""),
                getattr(strategy, "_or_hi", ""), getattr(strategy, "_or_lo", ""),
                getattr(strategy, "_or_ready", ""), getattr(strategy, "_traded", ""),
                getattr(strategy, "_trail_armed", ""),
                sig_dir, sig_reason,
            ])
    except Exception:
        pass
