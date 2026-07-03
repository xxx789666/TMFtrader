# -*- coding: utf-8 -*-
"""wave_fade — chips_combo 訊號 + 波浪 fade 濾網的「每日決策 producer」(VPS-native)。

定位:lab `wave_fade_forward.py` 的 VPS 版。lab 版讀本機 Obsidian MD + `vps api.txt`;這支改成:
  1) combo / 進場日 直接讀 data/chips_combo/next_signal.json(chips_combo_daily.py cron 18:30 已寫)。
  2) MXFR1 1min 用 shioaji **唯讀數據金鑰**(env)短暫登入抓近 25 日 → 併連續 15m。
  3) 切點B(進場日 08:45 開盤前最後一根 15m)算波浪方向 dir_full(1.0%、780 根≈10 交易日)。
  4) 三政策(A不濾/B同向跳/C只逆向)決定 + C1 觀察欄 → 寫 data/wave_fade/next_signal.json
     (side=選定政策 WAVE_POLICY 預設 B 的方向)+ append decisions.csv(三政策全記)。

執行載具 = strategy/wave_exec.py(引擎真 tick paper、MXF 小台 1 口);本檔只算「該不該進、哪方向」。
時序:每交易日 ~07:00(夜盤 05:00 收後、08:45 開盤前)跑。cron:scripts/run? → 見 launcher 註解。

⚠️ fail-open:若數據金鑰缺/抓 kbar 失敗 → wave_dir 不可得 → 濾網退化成純 chips(=政策A),
   並印明顯警告。paper 階段可接受(寧可多記真樣本),但要看得到濾網是否真的有在跑。

用法:
  python scripts/wave_fade_daily.py            # 每日:抓最新夜盤 + 算決策 + 寫 signal/log
  python scripts/wave_fade_daily.py --no-fetch # 不抓 kbar(用既有 parquet,測試/重算用)
  python scripts/wave_fade_daily.py --combo 0.83 --entry 2026-06-19  # 手動指定(不讀 chips signal)
"""
# VPS 系統時鐘 UTC → date.today()/datetime.now() 一律 TST(2026-07-03 稽核:別依賴 crontab TZ 前綴)
import os as _os, time as _time_tz
_os.environ.setdefault('TZ', 'Asia/Taipei')
try:
    _time_tz.tzset()
except AttributeError:
    pass

import csv
import datetime as dt
import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)   # 載入 WAVE_DATA_API_KEY 等(cron 不會自動帶 .env)
except Exception:
    pass
from strategy.wave_filter import dir_full, dir_c1  # noqa: E402

CHIPS_SIGNAL = ROOT / "data" / "chips_combo" / "next_signal.json"
DDIR = ROOT / "data" / "wave_fade"
SIGNAL = DDIR / "next_signal.json"
LOG = DDIR / "decisions.csv"
KBAR_PARQUET = ROOT / "data" / "history" / "MXFR1_1min_wave.parquet"

N_BARS = 780          # 切點B lookback:780 根 15m ≈ 10 交易日(含夜盤)
PCT = 0.010           # ZigZag 主浪門檻 1.0%(lab 凍結)
THR = 0.5             # |combo| 門檻(對齊 chips_combo)
POLICY = os.getenv("WAVE_POLICY", "B").strip().upper()   # 執行政策:B 同向跳(建議)


def read_chips_signal():
    """讀 chips_combo 最新訊號 → (signal_date, combo, entry_date)。"""
    try:
        j = json.loads(CHIPS_SIGNAL.read_text(encoding="utf-8"))
        return None, float(j["combo"]), str(j["trade_date"])
    except Exception as e:
        print(f"[warn] 讀不到 chips signal({CHIPS_SIGNAL}): {e}")
        return None, None, None


def _data_keys():
    """唯讀數據金鑰(優先序):WAVE_DATA_* → SHIOAJI_DATA_* → SHIOAJI_*(引擎主金鑰、kbar 亦可)。"""
    for ak, sk in (("WAVE_DATA_API_KEY", "WAVE_DATA_SECRET_KEY"),
                   ("SHIOAJI_DATA_API_KEY", "SHIOAJI_DATA_SECRET_KEY"),
                   ("SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY")):
        a, s = os.getenv(ak, "").strip(), os.getenv(sk, "").strip()
        if a and s:
            return a, s, ak
    return None, None, None


def fetch_update(days=25):
    """短暫 shioaji 登入(simulation、唯讀)抓 MXFR1 1min 近 days 日 → 寫 KBAR_PARQUET。回 True/False。"""
    import pandas as pd
    ak, sk, src = _data_keys()
    if not ak:
        print("[warn] 無數據金鑰(WAVE_DATA_API_KEY/SHIOAJI_API_KEY 皆空)→ 跳過 kbar 抓取")
        return False
    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=True)
        api.login(api_key=ak, secret_key=sk, receive_window=300000, fetch_contract=False)
        for _ in range(3):
            try:
                api.fetch_contracts(contracts_timeout=30000); break
            except Exception:
                pass
        c = api.Contracts.Futures.MXF.MXFR1
        start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        kb = api.kbars(c, start=start, end=dt.date.today().isoformat())
        df = pd.DataFrame({**kb}); df["ts"] = pd.to_datetime(df["ts"])
        KBAR_PARQUET.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(KBAR_PARQUET, index=False)
        try:
            api.logout()
        except Exception:
            pass
        print(f"[update] MXFR1 1min {len(df)} 根 (金鑰 {src}) → {df.ts.min()}~{df.ts.max()}")
        return True
    except Exception as e:
        print(f"[warn] 抓 MXFR1 kbar 失敗: {e}")
        return False


def build_15m():
    """既有 1min parquet → 連續 15m(floor 15min、high/low/close)。回 DataFrame 或 None。"""
    import pandas as pd
    files = sorted(glob.glob(str(ROOT / "data" / "history" / "MXFR1_1min_2026*.parquet"))) + \
        ([str(KBAR_PARQUET)] if KBAR_PARQUET.exists() else [])
    if not files:
        return None
    parts = []
    for f in files:
        try:
            d = pd.read_parquet(f); d["ts"] = pd.to_datetime(d["ts"]); parts.append(d)
        except Exception:
            continue
    if not parts:
        return None
    d = pd.concat(parts).drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    d["bar"] = d["ts"].dt.floor("15min")
    g = d.groupby("bar").agg(high=("High", "max"), low=("Low", "min"),
                             close=("Close", "last")).reset_index()
    g["t"] = g["bar"].dt.time
    g["d"] = g["bar"].dt.normalize()
    return g


def wave_dir_for(entry_date, g):
    """切點B:進場日 08:45 開盤前最後一根 15m 為 ci → 取 N_BARS 根算 dir_full / dir_c1。
    回 (wave_dir, c1_dir, asof_bar) 或 (None, None, None)(資料不足)。"""
    import pandas as pd
    ed = pd.Timestamp(entry_date)
    dayopen = g[(g["d"] == ed) & (g["t"] >= dt.time(8, 45)) & (g["t"] <= dt.time(13, 45))]
    if dayopen.empty:
        before = g[g["bar"] < ed + pd.Timedelta(hours=8, minutes=45)]
        if before.empty:
            return None, None, None
        ci = before.index[-1]
    else:
        ci = dayopen.index[0] - 1
    if ci < 0:
        return None, None, None
    lo = max(0, ci - N_BARS + 1)
    H = g["high"].values[lo:ci + 1]
    L = g["low"].values[lo:ci + 1]
    bars = g["bar"].tolist()[lo:ci + 1]
    if len(H) < 20:
        return None, None, None
    d = dir_full(H, L, bars, PCT)
    c1 = dir_c1(H, L, PCT) if d == 0 else 0
    return d, c1, bars[-1]


def decide(combo, wave_dir):
    """三政策決定。combo_dir=sign(combo) if |combo|>THR else 0。
    A 不濾=全做;B 同向跳=波浪同向→跳、逆向+不表態→做;C 只逆向。
    回 (combo_dir, decA, decB, decC)。
    ⚠️ wave_dir 為 None(資料不足)時:B fail-open(非同向→照做純 chips,= 執行政策的安全退化);
       C 則跳過(無方向不能斷言『逆向』)。此 None 邊角刻意比 lab 嚴(lab 把 None 當逆向讓 C 照做);
       C 僅觀察欄、且 lab 回測幾乎不觸 None,forward 對帳不受影響。"""
    side = (1 if combo > THR else (-1 if combo < -THR else 0))
    if side == 0:
        return 0, 0, 0, 0
    wd = wave_dir
    same = (wd is not None and wd != 0 and wd == side)
    opp = (wd is not None and wd != 0 and wd != side)
    decA = side
    decB = 0 if same else side          # 同向跳
    decC = side if opp else 0           # 只做逆向
    return side, decA, decB, decC


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if "--combo" in args:
        combo = float(args[args.index("--combo") + 1])
        entry = args[args.index("--entry") + 1]
        sig = "(manual)"
    else:
        sig, combo, entry = read_chips_signal()
    if combo is None or entry is None:
        print("讀不到 combo/進場日;請先確認 chips_combo_daily.py 已寫 next_signal.json,"
              "或用 --combo X --entry YYYY-MM-DD")
        sys.exit(1)

    if "--no-fetch" not in args:
        fetch_update()

    g = build_15m()
    if g is None or g.empty:
        print("[warn] 無 15m 資料 → 波浪不表態(資料不足),濾網退化成純 chips")
        wd, c1, asof = None, 0, ""
    else:
        wd, c1, asof = wave_dir_for(entry, g)
        # 防呆:asof(波浪定格 bar)若距進場日 >5 天 = 抓取失敗用到陳舊 parquet → 視為資料不足、
        # 不可拿幾週前的波浪當今天訊號(2026-06-18 數據金鑰未載入時實際踩到:用到 5/27 的波浪)。
        try:
            import pandas as _pd
            stale_days = (_pd.Timestamp(entry).normalize() - _pd.Timestamp(asof).normalize()).days
            if stale_days > 5:
                print(f"⚠️ 波浪定格 {asof} 距進場日 {entry} 已 {stale_days} 天 = 資料陳舊(抓取可能失敗)"
                      f"→ 視為資料不足、濾網 fail-open;勿信此波浪方向。")
                wd, c1 = None, 0
        except Exception:
            pass

    side, decA, decB, decC = decide(combo, wd)
    dirmap = {1: "多", -1: "空", 0: "空手"}
    wmap = {1: "+1多頭", -1: "-1淘汰", 0: "0不表態", None: "資料不足"}
    sel = {"A": decA, "B": decB, "C": decC}.get(POLICY, decB)
    exec_side = "long" if sel == 1 else ("short" if sel == -1 else "flat")

    if wd is None:
        print("⚠️ 波浪方向『資料不足』→ 濾網 fail-open(B/C 退化成做 chips)。檢查數據金鑰/kbar。")
    print("=" * 56)
    print(f"  signal {sig}  進場日 {entry}  (波浪定格 {asof})")
    print(f"  combo {combo:+.3f} → 籌碼 {dirmap[side]}{'(|combo|<0.5空手)' if side == 0 else ''}")
    print(f"  波浪 {wmap.get(wd)}   C1觀察 {wmap.get(c1) if side else '-'}")
    print(f"  A 不濾:{dirmap[decA]}  |  B 同向跳:{dirmap[decB]}  |  C 只逆向:{dirmap[decC]}")
    print(f"  ▶ 執行政策 {POLICY} → {dirmap[sel]} ({exec_side})")
    print("=" * 56)

    DDIR.mkdir(parents=True, exist_ok=True)
    SIGNAL.write_text(json.dumps(dict(
        trade_date=entry, side=exec_side, policy=POLICY, combo=round(combo, 3),
        combo_dir=side, wave_dir=(wd if wd is not None else None), c1_dir=c1,
        decA=decA, decB=decB, decC=decC, asof=str(asof)),
        ensure_ascii=False, indent=2), encoding="utf-8")

    row = dict(signal_date=sig or "", entry_date=entry, combo=round(combo, 3), combo_dir=side,
               wave_dir=("" if wd is None else wd), c1_dir=c1, decA=decA, decB=decB, decC=decC,
               policy=POLICY, asof=str(asof), status="pending")
    rows = []
    if LOG.exists():
        with open(LOG, encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f) if r.get("entry_date") != entry]  # 同進場日去重(覆蓋)
    rows.append(row)
    with open(LOG, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"已寫 {SIGNAL.name} + {LOG.name}(共 {len(rows)} 筆;實際成交/滑價由引擎 paper tape 記)")


if __name__ == "__main__":
    main()
