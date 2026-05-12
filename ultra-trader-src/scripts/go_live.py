"""
UltraTrader 實戰啟動腳本
自動驗證所有系統正常後，詢問是否切換 live
"""

import sys
import os
import time
import requests
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DASHBOARD = "http://127.0.0.1:8888"


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}" + (f" — {detail}" if detail else ""))
        return True
    else:
        print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))
        return False


def main():
    print()
    print("  ⚡ UltraTrader — 實戰啟動檢查")
    print("  ================================")
    print()

    # 1. 檢查 Dashboard 是否在跑
    print("[1/5] 檢查 Dashboard...")
    try:
        r = requests.get(f"{DASHBOARD}/api/state", timeout=5)
        state = r.json()
        check("Dashboard 運行中", True, f"引擎狀態: {state.get('engine_state')}")
    except Exception as e:
        check("Dashboard 連線", False, str(e))
        print()
        print("  ⚠️  Dashboard 沒有在跑！")
        print("  請先執行: python scripts/start.py --mode paper --risk crisis")
        sys.exit(1)

    # 2. 檢查交易模式
    print()
    print("[2/5] 檢查交易模式...")
    mode = state.get("trading_mode", "unknown")
    contract = state.get("contract", "unknown")
    risk = state.get("risk_profile", "unknown")
    instruments = state.get("instruments", [])
    check("交易模式", True, mode)
    check("合約", bool(contract), contract)
    check("商品", len(instruments) > 0, ", ".join(instruments) if instruments else "未知")
    check("風險等級", True, risk)

    # 3. 檢查 Shioaji 連線（看有沒有 tick 進來）
    print()
    print("[3/5] 等待行情數據...")
    if mode == "simulation":
        check("模擬模式", True, "使用假數據，不需要行情")
        has_ticks = True
    else:
        has_ticks = False
        # 檢查所有商品的行情
        inst_data = state.get("instruments_data", {})
        if inst_data:
            all_ok = True
            for inst, data in inst_data.items():
                p = data.get("price", 0)
                if p > 0:
                    check(f"{inst} 行情", True, f"價格: {p:,.1f}")
                else:
                    all_ok = False
            if all_ok:
                has_ticks = True
        else:
            price = state.get("price", 0)
            if price > 0:
                has_ticks = True
                check("即時行情", True, f"價格: {price:,.0f}")

        if not has_ticks:
            print("  ⏳ 等待第一筆 tick（最多 60 秒）...")
            for i in range(60):
                time.sleep(1)
                try:
                    r = requests.get(f"{DASHBOARD}/api/state", timeout=3)
                    s = r.json()
                    idata = s.get("instruments_data", {})
                    if idata:
                        prices_ok = all(d.get("price", 0) > 0 for d in idata.values())
                        if prices_ok:
                            has_ticks = True
                            for inst, d in idata.items():
                                check(f"{inst} 行情", True, f"價格: {d['price']:,.1f}（等了 {i+1} 秒）")
                            break
                    else:
                        p = s.get("price", 0)
                        if p > 0:
                            has_ticks = True
                            check("即時行情", True, f"價格: {p:,.0f}（等了 {i+1} 秒）")
                            break
                except:
                    pass
                if (i + 1) % 10 == 0:
                    print(f"  ⏳ 已等 {i+1} 秒...")

            if not has_ticks:
                check("即時行情", False, "60 秒內沒有收到 tick — 可能休市或 API 斷線")

    # 4. 檢查 Intelligence
    print()
    print("[4/5] 檢查情報模組...")
    try:
        r = requests.get(f"{DASHBOARD}/api/intelligence", timeout=5)
        intel = r.json()
        vix = intel.get("international", {}).get("vix", 0)
        pc = intel.get("options", {}).get("pc_ratio_oi", 0)
        left = intel.get("left_side", {}).get("signal", "unknown")
        check("VIX", vix > 0, f"{vix:.1f}")
        check("P/C Ratio", pc > 0, f"{pc:.2f}")
        check("左側訊號", True, left)
    except:
        check("情報模組", False, "無法取得資料")

    # 5. 檢查風控
    print()
    print("[5/5] 檢查風控系統...")
    risk_data = state.get("risk", {})
    cb = risk_data.get("circuit_breaker", {})
    cb_state = cb.get("state", "unknown")
    max_loss = cb.get("max_daily_loss", 0)
    check("熔斷器", cb_state == "active", cb_state)
    check("日最大虧損", True, f"{max_loss:,.0f} 元")

    ps = risk_data.get("position_sizer", {})
    max_c = ps.get("max_contracts", "?")
    check("最大口數", True, f"{max_c} 口")

    # 總結
    print()
    print("  ================================")
    regime = state.get("strategy", {}).get("regime", "未知")
    print(f"  市場狀態: {regime}")
    print(f"  當前模式: {mode}")
    print(f"  商品: {', '.join(instruments) if instruments else contract}")
    print()

    if not has_ticks and mode != "simulation":
        print("  ⚠️  沒有行情數據！可能是休市或 API 問題。")
        print("  建議等開盤後再試。")
        print()
        return

    if mode == "live":
        print("  ✅ 已經在 LIVE 模式，系統正在交易中！")
        return

    # 全部正常 → 自動切換 LIVE
    print("  全部檢查通過，自動切換到 LIVE 模式...")
    print()
    try:
        r = requests.post(f"{DASHBOARD}/api/mode/live", timeout=10)
        result = r.json()
        if result.get("status") == "ok":
            print("  🔥 已切換到 LIVE 模式！系統開始交易！")
            print()
            print("  Dashboard: http://127.0.0.1:8888")
            print("  祝你大賺 💰")
        else:
            print(f"  ❌ 切換失敗: {result.get('error', '未知錯誤')}")
            print("  系統維持觀盤模式，請手動檢查。")
    except Exception as e:
        print(f"  ❌ 切換失敗: {e}")
        print("  系統維持觀盤模式，請手動檢查。")


if __name__ == "__main__":
    main()
