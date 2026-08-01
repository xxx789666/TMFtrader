"""一次性:驗 FinMind TaiwanOptionDaily session + W2 OI 合理性。用後刪。"""
import json, os, urllib.parse, urllib.request, collections, sys

DS = sys.argv[1] if len(sys.argv) > 1 else "2026-06-04"
tok = os.getenv("FINMIND_TOKEN", "").strip()
q = {"dataset": "TaiwanOptionDaily", "data_id": "TXO", "start_date": DS, "end_date": DS}
if tok:
    q["token"] = tok
url = f"https://api.finmindtrade.com/api/v4/data?{urllib.parse.urlencode(q)}"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
rows = json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8"))["data"]

ts = collections.Counter(r.get("trading_session", "?") for r in rows)
print("trading_session distinct:", ts.most_common())

for sess in ts:
    w2 = [r for r in rows if r["contract_date"] == "202606W2" and r.get("trading_session") == sess]
    coi = sum(r["open_interest"] for r in w2 if r["call_put"] == "call")
    poi = sum(r["open_interest"] for r in w2 if r["call_put"] == "put")
    nz = [r for r in w2 if r["open_interest"] > 0]
    print(f"  session={sess}: W2列={len(w2)} 有OI列={len(nz)} CallOI合計={coi:,} PutOI合計={poi:,}")
    # ATM 附近幾筆
    atm = sorted([r for r in w2 if r["call_put"] == "call" and r["open_interest"] > 0],
                 key=lambda r: -r["open_interest"])[:5]
    for r in atm:
        print(f"    K={r['strike_price']:.0f} call OI={r['open_interest']:,} settle={r['settlement_price']}")
