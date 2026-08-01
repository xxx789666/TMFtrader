"""一次性:驗證 TAIFEX TXO HTML 欄位對齊(契約/到期/履約/買賣權/OI 的 index)。用後刪。"""
import re, urllib.request, urllib.parse, collections, sys

DS = sys.argv[1] if len(sys.argv) > 1 else "2026/06/04"
body = urllib.parse.urlencode({"queryType": "2", "marketCode": "1", "commodity_id": "TXO",
                               "queryDate": DS, "MarketCode": "1", "commodity_idt": "TXO",
                               "button": "送出查詢"}).encode()
req = urllib.request.Request("https://www.taifex.com.tw/cht/3/optDailyMarketReport",
                             data=body, headers={"User-Agent": "Mozilla/5.0"})
t = urllib.request.urlopen(req, timeout=50).read().decode("utf-8", "replace")

rows = []
for row in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
    c = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
    if len(c) >= 13 and c[0] == "TXO":
        rows.append(c)

print(f"date={DS} TXO列數={len(rows)}")
if not rows:
    print("無資料(假日?)"); sys.exit()
print(f"欄位數={len(rows[0])}")
print("樣本第1列逐欄:")
for i, v in enumerate(rows[0]):
    print(f"  c[{i}] = {v!r}")
# 各欄 distinct(找出哪欄是買賣權)
for i in range(min(5, len(rows[0]))):
    vals = collections.Counter(r[i] for r in rows)
    top = vals.most_common(6)
    print(f"c[{i}] distinct(top6): {top}")

# dump W2 Call 整條鏈:strike + c[12..15],找哪欄是 ATM 最大的整數(=OI)
print("\nW2 Call 鏈 (strike | c12 c13 c14 c15):")
chain = sorted([r for r in rows if r[1] == "202606W2" and r[4] == "Call"], key=lambda r: int(r[3]))
sums = {12: 0.0, 13: 0.0, 14: 0.0, 15: 0.0}
for r in chain:
    print(f"  {r[3]:>6} | {r[12]:>10} {r[13]:>10} {r[14]:>10} {r[15]:>10}")
    for i in sums:
        v = r[i].replace(",", "")
        try:
            sums[i] += float(v)
        except ValueError:
            pass
print("欄位合計(整條 Call 鏈):", {k: round(v, 1) for k, v in sums.items()})
