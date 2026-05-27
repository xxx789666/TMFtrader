"""
scripts/fetch_history_kbars.py — quota-safe 分段歷史 K 線拉取（永豐 Shioaji）

把連續期貨（TMFR1 / TXFR1 …）的 1 分鐘 K 棒分「月」拉下來、落地成 parquet
（無 pyarrow 時自動 fallback 成 csv.gz）。設計成可以安全、分多次拉長歷史，
不一次撞爆永豐每日流量配額（500MB–10GB；超量時永豐只切歷史查詢，會回 0 bars）。

安全特性
--------
  - 分「日曆月」拉，每段之間 sleep 節流（--sleep，預設 3s）
  - 已存在的月份檔自動跳過 -> 中斷後重跑會接續（resume），不重抓
  - 撞 0-bars（已抓到資料後又突然 0 = 疑似配額/錯誤）或任何例外 -> 立刻停 + TG 告警 + 保留進度
  - 預設「交易時段拒跑」：日 08:45-13:45 / 夜 15:00-05:00 內不跑，避免吃掉 live 要用的配額
    （要硬跑加 --allow-trading-hours）
  - --max-chunks：每次最多拉幾個月（分多天拉最穩）
  - 配額計量 + 雙閘門（用 api.usage() 帳號當日累計）：
      * --history-budget-mb（預設 420）：本次歷史拉取自身用量達此值就停
      * --reserve-mb（預設 70）：今日總用量逼近「上限-保留」就停，永遠留 70MB 給策略
    每抓一段印「本段約 X MB / 歷史累計 Y/420MB / 今日 Z/500MB」，第一批跑完即知真實每月成本

注意
----
  - 歷史資料起始日（永豐伺服器端）：期貨 2020-03-22、股票/指數 2020-03-02
  - 但個別商品實際可回溯點不同（2026-05-27 實測）：
      * TXFR1 大台：2020-03 起（完整）
      * TMFR1 微台：kbars 只回到 2024-07（比上市日晚很多、micro 合約歷史短）→ start 給太早會撞 max-empty 停下
  - 必須在「已授權的 IP（VPS）」上跑；本機 Windows IP 會 Sign data timeout

用法
----
  # 先測一小段（低配額、驗證可用）
  python3.12 scripts/fetch_history_kbars.py --contract TMFR1 --start 2026-05-01 --allow-trading-hours
  # 正式分批（離峰跑）
  python3.12 scripts/fetch_history_kbars.py --contract TMFR1 --start 2024-06-01   # TMF kbars 只回到 2024-07
  python3.12 scripts/fetch_history_kbars.py --contract TXFR1 --start 2020-03-22
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, time as dtime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from loguru import logger

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
load_dotenv(ROOT / ".env")

# TG（沿用 core/notify，失敗靜默；訊息用純文字、不放 emoji）
try:
    from core.notify import tg as _tg
except Exception:
    def _tg(msg: str): pass

def tg(msg: str):
    try:
        _tg(f"[HistFetch] {msg}")
    except Exception:
        pass

MB = 1024 * 1024

def get_usage(api):
    """回 (used_bytes, limit_bytes)：永豐 api.usage() 的 account 當日累計用量。
    注意：是「整個帳號當日」累計（含 live 引擎並發流量），不是本 process 專屬。
    失敗回 (None, None)。"""
    try:
        u = api.usage()
        return int(u.bytes), int(u.limit_bytes)
    except Exception as e:
        logger.warning(f"[usage] 查詢失敗: {e}")
        return None, None

OUT_DIR = ROOT / "data" / "history"


# ─── 分月切段 ────────────────────────────────────────────────
def month_chunks(start: date, end: date):
    """產生 [(chunk_start, chunk_end, 'YYYYMM'), ...]，依日曆月切，首尾裁到 start/end。"""
    out = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        ms = date(y, m, 1)
        nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        me = nxt - timedelta(days=1)
        out.append((max(ms, start), min(me, end), f"{y:04d}{m:02d}"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


# ─── 交易時段判斷（TST）────────────────────────────────────────
def in_trading_session(now: datetime) -> bool:
    t = now.time()
    day = dtime(8, 45) <= t <= dtime(13, 45)
    night = t >= dtime(15, 0) or t <= dtime(5, 0)
    return day or night


# ─── 存檔（parquet 優先、無 pyarrow 退 csv.gz）──────────────────
def save_df(df: pd.DataFrame, base: Path) -> Path:
    pq = base.with_suffix(".parquet")
    try:
        df.to_parquet(pq, index=False)
        return pq
    except Exception as e:
        gz = base.with_suffix(".csv.gz")
        df.to_csv(gz, index=False, compression="gzip")
        logger.warning(f"parquet 不可用（{e}）-> 改存 {gz.name}")
        return gz


def chunk_already_done(base: Path) -> bool:
    return base.with_suffix(".parquet").exists() or base.with_suffix(".csv.gz").exists()


# ─── 解析合約 ────────────────────────────────────────────────
def resolve_contract(api, code: str):
    """'TMFR1' -> api.Contracts.Futures.TMF.TMFR1。"""
    prod = code[:-2] if code[-2:] in ("R1", "R2") else code
    try:
        cat = getattr(api.Contracts.Futures, prod)
        c = getattr(cat, code)
    except Exception as e:
        raise SystemExit(f"[FATAL] 找不到合約 {code}（Futures.{prod}.{code}）: {e}")
    if c is None or not getattr(c, "code", ""):
        raise SystemExit(f"[FATAL] 合約 {code} 載入不完整")
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", default="TMFR1", help="連續期貨代碼，如 TMFR1 / TXFR1")
    ap.add_argument("--start", required=True, help="起始日 YYYY-MM-DD")
    ap.add_argument("--end", default=date.today().isoformat(), help="結束日 YYYY-MM-DD（預設今天）")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="輸出資料夾")
    ap.add_argument("--sleep", type=float, default=3.0, help="每段之間 sleep 秒數（節流）")
    ap.add_argument("--max-chunks", type=int, default=0, help="本次最多拉幾個月（0=不限）")
    ap.add_argument("--max-empty", type=int, default=18, help="連續空月上限（上市前空檔），超過視為異常停下")
    ap.add_argument("--allow-trading-hours", action="store_true", help="允許在交易時段跑（預設拒跑保護 live 配額）")
    ap.add_argument("--force", action="store_true", help="已存在的月份也重抓")
    ap.add_argument("--history-budget-mb", type=float, default=420.0, help="本次歷史拉取最多用多少 MB（預設 420）")
    ap.add_argument("--reserve-mb", type=float, default=70.0, help="保留給策略的 MB（今日總用量逼近 上限-此值 就停，預設 70）")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        raise SystemExit("[FATAL] start 晚於 end")

    # 交易時段保護
    now = datetime.now()
    if in_trading_session(now) and not args.allow_trading_hours:
        msg = (f"現在 {now:%H:%M} 在交易時段內，為保護 live 配額，預設不跑歷史拉取。"
               f"離峰再跑，或加 --allow-trading-hours 強制。")
        logger.error(msg)
        print(msg)
        sys.exit(2)

    chunks = month_chunks(start, end)
    if args.max_chunks > 0:
        # 只算「還沒抓」的月份，避免 max-chunks 被已完成的月份吃掉
        pending = [c for c in chunks if args.force or not chunk_already_done(out_dir / f"{args.contract}_1min_{c[2]}")]
        chunks = pending[: args.max_chunks]

    logger.info("=" * 60)
    logger.info(f"歷史 K 線拉取：{args.contract} 1-min | {start} -> {end}")
    logger.info(f"本次待抓月份：{len(chunks)} 個 | sleep={args.sleep}s | out={out_dir}")
    logger.info("=" * 60)
    if not chunks:
        print("沒有待抓的月份（可能都已存在）。用 --force 可重抓。")
        return

    # ── Shioaji 登入（沿用 broker.py 修法：fetch_contract=False + 手動 fetch_contracts retry）
    import shioaji as sj
    api = sj.Shioaji(simulation=False)
    api.login(
        api_key=os.environ["SHIOAJI_API_KEY"],
        secret_key=os.environ["SHIOAJI_SECRET_KEY"],
        receive_window=300000,
        fetch_contract=False,
    )
    logger.info("[Login] Shioaji OK")
    for r in range(3):
        try:
            api.fetch_contracts(contracts_timeout=30000)
            break
        except Exception as e:
            logger.warning(f"[fetch_contracts] attempt #{r+1} 失敗: {e}")
            if r < 2:
                time.sleep(60)

    contract = resolve_contract(api, args.contract)
    logger.info(f"[Contract] {contract.code} ({getattr(contract,'name','')})")

    # ── 配額計量起點（api.usage() = 帳號當日累計、含 live 引擎）
    run_start_bytes, limit_bytes = get_usage(api)
    last_used = run_start_bytes
    if run_start_bytes is not None and limit_bytes:
        logger.info(f"[Quota] 今日起點 {run_start_bytes/MB:.1f}MB / {limit_bytes/MB:.0f}MB | "
                    f"歷史預算 {args.history_budget_mb:.0f}MB、保留給策略 {args.reserve_mb:.0f}MB")
    else:
        logger.warning("[Quota] 起點 usage 查不到、配額閘門將無法生效（仍會靠 0-bars 停）")

    total_bars = 0
    saved = 0
    seen_data = False
    consecutive_empty = 0
    try:
        for cs, ce, ym in chunks:
            # ── 配額閘門（用上一段量到的數字判斷、抓之前先檢查）
            if last_used is not None and limit_bytes:
                # 跨午夜 daily reset 偵測（用量突然變小）
                if run_start_bytes is not None and last_used < run_start_bytes:
                    logger.info("[Quota] 偵測到當日用量歸零（跨日 reset）、重設歷史預算起點")
                    run_start_bytes = last_used
                used_mb = last_used / MB
                limit_mb = limit_bytes / MB
                hist_mb = (last_used - run_start_bytes) / MB if run_start_bytes is not None else 0.0
                if used_mb >= limit_mb - args.reserve_mb:
                    m = (f"STOP：今日總用量 {used_mb:.0f}MB 已逼近 上限-保留({limit_mb:.0f}-{args.reserve_mb:.0f}MB)、"
                         f"停下保護策略。已存 {saved} 月。隔日離峰 resume。")
                    logger.error(m); tg(m); break
                if hist_mb >= args.history_budget_mb:
                    m = (f"STOP：本次歷史拉取已用 {hist_mb:.0f}MB、達預算 {args.history_budget_mb:.0f}MB。"
                         f"已存 {saved} 月。隔日 resume。")
                    logger.error(m); tg(m); break

            base = out_dir / f"{args.contract}_1min_{ym}"
            if not args.force and chunk_already_done(base):
                logger.info(f"  [{ym}] 已存在、跳過")
                continue

            try:
                kb = api.kbars(contract, start=cs.isoformat(), end=ce.isoformat())
                df = pd.DataFrame({**kb})
            except Exception as e:
                tg(f"STOP：{args.contract} {ym} kbars 例外：{e}。已存 {saved} 個月、{total_bars} 根。")
                logger.error(f"  [{ym}] kbars 例外、停下：{e}")
                break

            if df is None or len(df) == 0:
                consecutive_empty += 1
                if not seen_data:
                    # 還在「上市前」的空檔，跳過即可
                    logger.info(f"  [{ym}] 0 bars（疑似上市前）、跳過 ({consecutive_empty}/{args.max_empty})")
                    if consecutive_empty >= args.max_empty:
                        tg(f"STOP：{args.contract} 連續 {consecutive_empty} 個空月、可能 start 太早或商品代碼錯。")
                        logger.error("  連續空月過多、停下")
                        break
                    continue
                else:
                    # 已經抓到過資料卻突然 0 -> 高度疑似撞配額
                    tg(f"STOP：{args.contract} {ym} 回 0 bars（已抓到過資料）= 疑似撞每日配額。"
                       f"已存 {saved} 個月、{total_bars} 根。明天離峰再 resume。")
                    logger.error(f"  [{ym}] 0 bars 但前面有資料 = 疑似配額、停下")
                    break

            # 正常有資料
            consecutive_empty = 0
            seen_data = True
            if "ts" in df.columns:
                df["ts"] = pd.to_datetime(df["ts"])
                df = df.sort_values("ts").reset_index(drop=True)
            path = save_df(df, base)
            total_bars += len(df)
            saved += 1

            # ── 量這段花了多少（usage 是帳號當日累計、含並發 live 流量 → 近似值）
            now_used, now_limit = get_usage(api)
            if now_used is not None:
                if limit_bytes is None and now_limit:
                    limit_bytes = now_limit
                if run_start_bytes is None:
                    run_start_bytes = now_used
                chunk_mb = (now_used - last_used) / MB if last_used is not None else float("nan")
                hist_mb = (now_used - run_start_bytes) / MB
                lim_mb = (limit_bytes or 0) / MB
                logger.info(f"  [{ym}] {len(df):>6} 根 -> {path.name} | 本段約 {chunk_mb:.1f}MB | "
                            f"歷史累計 {hist_mb:.0f}/{args.history_budget_mb:.0f}MB | "
                            f"今日 {now_used/MB:.0f}/{lim_mb:.0f}MB")
                last_used = now_used
            else:
                logger.info(f"  [{ym}] {len(df):>6} 根 -> {path.name}  (累計 {total_bars} 根 / {saved} 月、usage 查詢失敗)")
            time.sleep(args.sleep)

        end_used, _ = get_usage(api)
        hist_total = ((end_used - run_start_bytes) / MB
                      if (end_used is not None and run_start_bytes is not None) else float("nan"))
        msg = (f"完成本批：{args.contract} 存 {saved} 個月、{total_bars} 根（{start}~{end} 內）。"
               f"本次歷史約用 {hist_total:.0f}MB / 預算 {args.history_budget_mb:.0f}MB。")
        logger.info(msg)
        tg(msg)
        print(msg)
    finally:
        try:
            api.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
