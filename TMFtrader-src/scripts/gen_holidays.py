# -*- coding: utf-8 -*-
"""產生市場非交易日表 market_holidays.txt。(TING 部署方貢獻,2026-08-01 收錄)

用法:python3 scripts/gen_holidays.py 116        # 民國年
     python3 scripts/gen_holidays.py 116 --out data/market_holidays_2027.txt

來源:TWSE 官方開(休)市日期表 CSV(Big5),已與期交所行事曆 PDF 交叉核對。

⚠️ 這支存在的理由:TWSE 端點對「尚未發布的年度」**不會回錯誤,會靜默退回當前年度**
   (2026-08-01 實測:queryYear=116 回傳的標題仍是「中華民國115年」,HTTP 200、大小相同)。
   若無年份驗證,明年照著換參數重跑會拿到舊表卻毫無警訊 → 整年的開休市判斷全錯。
   因此本程式強制比對回傳標題的年份,不符就拒絕產出。

⚠️ 本專案(主 VPS)的 scripts/market_holidays.txt 語義=「特殊休市日 only、不含週末」
   (watchdog 週末照跑、launcher 平日 guard) — 整表替換會讓 watchdog 週末提前退出。
   主專案用法:跑本程式後取「平日休市」清單去補 scripts/market_holidays.txt 的缺漏。
   TING 側 data/market_holidays.txt 才是全表語義(含週末)。
"""
import sys
import datetime as dt
import urllib.request
from pathlib import Path

# Windows cp950 遇中文/emoji 會 UnicodeEncodeError(本專案鐵律雷;Linux 無感)。
# ⚠️ stdout+stderr 都要包(2026-08-01 TING 方二次修正):sys.exit(訊息)走 stderr,
#    只包 stdout 時「年份驗證 ❌ 拒絕」= 本工具最重要的輸出,仍會在最後一刻亂碼/崩潰。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

URL = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=csv&queryYear={}"
TAIFEX_PDF = "https://www.taifex.com.tw/file/taifex/CHINESE/4/{}Calendar.pdf"


def fetch(roc: int) -> str:
    req = urllib.request.Request(URL.format(roc), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("big5", errors="replace")


def main():
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        sys.exit("用法:python3 gen_holidays.py <民國年>  例:python3 gen_holidays.py 116")
    roc = int(sys.argv[1])
    year = roc + 1911
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv \
        else Path("data") / "market_holidays.txt"

    print(f"抓取民國 {roc} 年({year})…")
    csv = fetch(roc)
    head = csv.split("\n", 1)[0]
    print(f"回傳標題:{head.strip()}")

    # ── 年份驗證:端點會靜默退回舊年度,這是唯一的防線 ──
    if f"中華民國{roc}年" not in head:
        sys.exit(f"\n❌ 年份不符!要求民國 {roc} 年,但回傳的是:{head.strip()}\n"
                 f"   代表該年度尚未發布,端點靜默退回了舊資料。**不產出檔案**。\n"
                 f"   台灣通常在前一年年中才公布次年行事曆,晚點再試。")

    # ── 解析 ──
    holidays, trading = set(), set()
    for line in csv.split("\n")[2:]:
        line = line.strip()
        if not line.startswith('"'):
            continue
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) < 4:
            continue
        datestr, name, _desc, note = cols[0], cols[1], cols[2], cols[3].rstrip('"')
        try:
            mm = int(datestr.split("月")[0])
            dd = int(datestr.split("月")[1].split("日")[0])
            d = dt.date(year, mm, dd).isoformat()
        except Exception:
            continue
        if "o" in note:                     # 標記為交易日(如春節後首個交易日)
            trading.add(d)
        else:                               # 空白=放假;"*"=市場無交易僅辦結算 → 皆視為非交易日
            holidays.add(d)

    # ── 加入所有週六日,再扣掉明確標記為交易日者(補班日照開市時這步才正確)──
    d = dt.date(year, 1, 1)
    while d.year == year:
        if d.weekday() >= 5:
            holidays.add(d.isoformat())
        d += dt.timedelta(days=1)
    holidays -= trading

    weekday_hols = sorted(x for x in holidays if dt.date.fromisoformat(x).weekday() < 5)
    hdr = [
        f"# 台灣期貨/證券市場非交易日表 {year}(民國{roc}年)",
        f"# 由 scripts/gen_holidays.py 產生於 {dt.date.today().isoformat()}",
        f"# 來源:{URL.format(roc)}",
        f"# 交叉核對:{TAIFEX_PDF.format(year)}",
        f"# 內容=所有週六日 + 國定假日/補假 + 市場無交易日,扣除明確標記為交易日者",
        f"# 平日休市 {len(weekday_hols)} 天:{', '.join(weekday_hols)}",
        "# ⚠️ 本檔必須是 LF 無 BOM;CRLF 會讓 grep -qx 全部匹配失敗",
        "# ⚠️ 颱風假等臨時休市不在此表 — 由夜檢的 tick 偵測補足",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(hdr + sorted(holidays)) + "\n")

    print(f"\n✅ 已寫入 {out}")
    print(f"   非交易日合計 {len(holidays)} 天,其中平日休市 {len(weekday_hols)} 天:")
    for x in weekday_hols:
        print(f"     {x} ({'一二三四五'[dt.date.fromisoformat(x).weekday()]})")


if __name__ == "__main__":
    main()
