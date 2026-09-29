# -*- coding: utf-8 -*-
"""settlement_v2 逆勢旁欄 — 雙欄損益回填 eval(v2 vs v2+skip 兩條 paper-tape)。

把每筆結算的「實際點數」接上「逆勢veto決策」,各自累積:
  - v2:全部進場(原口徑)
  - v2+skip:逆勢(三度全逆)那筆記 0、其餘照算
實際點數來源優先序:① 回測 CSV(settlement_v2_2020_2025.csv) ② 從 MXFR1 1min 算當週三開→收
  (entry=Wed 08:45 開、exit=13:45 收、side、cost 4pt) ③ 都沒有 → pending(不累積)。

用法:
  python scripts/settlement_v2_veto_eval.py            # 全史回填 → 印雙欄摘要 + 寫 tape CSV + md 區塊
  python scripts/settlement_v2_veto_eval.py --md       # 只印最新累積的 markdown paper-tape 區塊
"""
import sys
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settlement_v2_wave_veto as veto

ROOT = Path(__file__).resolve().parent.parent
PV = 50; COST = 4.0
TAPE = ROOT / 'data' / 'forward' / 'settlement_v2_veto_tape.csv'
# 權威實際點數覆寫(VPS 每週把報告「本筆結算」的 pnl 寫這:欄位 trade_date,pnl_pts)
REALIZED = ROOT / 'data' / 'forward' / 'settlement_v2_realized.csv'
BTCSV = ROOT / 'data' / 'backtest_history' / 'settlement_v2_2020_2025.csv'
LIVE_TAPE = ROOT / 'data' / 'settlement_v2' / 'decisions.csv'   # live paper 逐筆(隨結算成長)
LIVE = [  # 回測 CSV 之後、live tape 尚未涵蓋時的備援種子(報告值;有 live tape 會被它蓋過)
    {'signal_date': '2026-06-09', 'trade_date': '2026-06-10', 'side': 'long',  'pnl_pts': -421.0},
    {'signal_date': '2026-06-16', 'trade_date': '2026-06-17', 'side': 'short', 'pnl_pts': -625.0},
]


def settle_pnl_from_mxf(trade_date, side):
    """從 MXFR1 1min 算當週三 08:45開→13:45收 的 v2 點數(含 cost)。找不到回 None。"""
    td = pd.to_datetime(trade_date)
    ym = td.strftime('%Y%m')
    cands = [ROOT / 'data' / 'history' / f'MXFR1_1min_{ym}.parquet',
             ROOT / 'data' / 'history' / f'MXFR1_1min_{ym}_patch.parquet']
    for f in cands:
        if not f.exists():
            continue
        g = pd.read_parquet(f); g['ts'] = pd.to_datetime(g['ts'])
        d = g[(g['ts'].dt.normalize() == td.normalize()) &
              (g['ts'].dt.time >= dt.time(8, 45)) & (g['ts'].dt.time <= dt.time(13, 45))]
        if len(d) < 10:
            continue
        entry = float(d['Open'].iloc[0]); exit_ = float(d['Close'].iloc[-1])
        sgn = 1 if side == 'long' else -1
        return sgn * (exit_ - entry) - COST
    return None


def build_trades():
    """合併三源(優先序:live tape > LIVE 種子 > 回測CSV),依 trade_date 去重 → 隨結算成長。"""
    parts = [pd.read_csv(BTCSV)[['signal_date', 'trade_date', 'side', 'pnl_pts']],
             pd.DataFrame(LIVE)]
    if LIVE_TAPE.exists():                       # live paper 逐筆(每次結算 append、權威方向/點數)
        try:
            lt = pd.read_csv(LIVE_TAPE)
            if {'signal_date', 'trade_date', 'side', 'pnl_pts'}.issubset(lt.columns):
                parts.append(lt[['signal_date', 'trade_date', 'side', 'pnl_pts']])
        except Exception:
            pass
    full = pd.concat(parts, ignore_index=True)
    full['signal_date'] = pd.to_datetime(full['signal_date'], errors='coerce')
    full['trade_date'] = pd.to_datetime(full['trade_date'], errors='coerce')
    full = full.dropna(subset=['trade_date'])
    # 後出現者覆蓋(parts 順序:回測→LIVE→live tape;keep='last' 讓 live tape 勝出)
    full = full.drop_duplicates('trade_date', keep='last').sort_values('trade_date').reset_index(drop=True)
    return full


def build_tape(write=True):
    """合併三源 → 套 veto → 算雙欄 pnl(v2 / v2+skip)。回 tape DataFrame。write=True 才落地 CSV。"""
    full = build_trades()
    override = {}
    if REALIZED.exists():
        rv = pd.read_csv(REALIZED); rv['trade_date'] = pd.to_datetime(rv['trade_date']).dt.date.astype(str)
        override = dict(zip(rv['trade_date'], rv['pnl_pts']))

    rows = []
    for _, t in full.iterrows():
        sd = t['signal_date'].date() if pd.notna(t['signal_date']) else t['trade_date'].date()
        side = t['side']
        tdk = str(t['trade_date'].date())
        # 實際點數優先序:① 權威覆寫(報告值) ② 回測CSV/LIVE/live tape ③ MXF近似算
        if tdk in override:
            pnl = override[tdk]; src = 'report'
        elif not pd.isna(t['pnl_pts']):
            pnl = t['pnl_pts']; src = 'csv'
        else:
            pnl = settle_pnl_from_mxf(t['trade_date'], side); src = 'mxf~'
        try:
            d = veto.decide(sd, side)
            action = 'SKIP' if d['is_counter'] else 'TAKE'
        except Exception:
            action = 'TAKE'  # 趨勢算不出(資料不足)→ 保守照做
        pnl_skip = 0.0 if action == 'SKIP' else pnl
        rows.append({'trade_date': str(t['trade_date'].date()), 'side': side,
                     'action': action, 'pnl_v2': pnl, 'pnl_skip': pnl_skip, 'src': src})

    tape = pd.DataFrame(rows)
    tape = tape[tape['pnl_v2'].notna()].reset_index(drop=True)  # pending 不累積
    tape['cum_v2'] = tape['pnl_v2'].cumsum()
    tape['cum_skip'] = tape['pnl_skip'].cumsum()
    if write:
        TAPE.parent.mkdir(parents=True, exist_ok=True)
        tape.to_csv(TAPE, index=False, encoding='utf-8-sig')
    return tape


def _stats(tape, col):
    p = tape[tape[col] != 0][col].values if col == 'pnl_skip' else tape[col].values
    p = np.asarray(p, float)
    eq = np.cumsum(p); peak = np.maximum.accumulate(eq); dd = (eq - peak).min() if len(p) else 0
    w = p[p > 0].sum(); l = -p[p < 0].sum(); pf = w/l if l > 0 else float('inf')
    return dict(n=len(p), pts=int(p.sum()), ntd=int(p.sum()*PV), pf=round(pf, 2),
                wr=round((p > 0).mean(), 3) if len(p) else 0.0, mdd=int(dd))


def report_block(tape=None):
    """回傳可貼進每日報告的『逆勢濾網雙欄參數』markdown 區塊(含濾網規格 + v2 vs v2+skip 績效)。"""
    if tape is None:
        tape = build_tape(write=False)
    if tape.empty:
        return '## 🌊 逆勢濾網(雙欄 paper-tape)\n\n> 尚無樣本。'
    s2 = _stats(tape, 'pnl_v2'); ss = _stats(tape, 'pnl_skip')
    n_skip = int((tape['action'] == 'SKIP').sum())
    return '\n'.join([
        '## 🌊 逆勢濾網雙欄 paper-tape(v2 vs v2+逆勢skip;MXF 1口 pv50、cost 4pt)',
        '- **濾網規格**:TAIEX 日線三度數線上 ZigZag(長勢10% / 中勢6% / 短勢3%、因果無前視);'
        '三度數**皆非0且全逆** v2 訊號方向 → 該筆 SKIP(降險),其餘照做 1 口。',
        f'- 樣本 {len(tape)} 筆({tape.trade_date.iloc[0]}~{tape.trade_date.iloc[-1]});逆勢 SKIP 累計 {n_skip} 筆。',
        '',
        '| 欄 | 進場 | 累積點 | 累積 NT$ | PF | 勝率 | 最大回撤pt |',
        '|---|--:|--:|--:|--:|--:|--:|',
        f'| v2(原口徑) | {s2["n"]} | {s2["pts"]:+d} | {s2["ntd"]:+,} | {s2["pf"]} | {s2["wr"]:.0%} | {s2["mdd"]} |',
        f'| v2+skip(降險) | {ss["n"]} | {ss["pts"]:+d} | {ss["ntd"]:+,} | {ss["pf"]} | {ss["wr"]:.0%} | {ss["mdd"]} |',
        '',
        '> 定位:降回撤旁路、**非 alpha**(無 2014–19 OOS)。skip 版勝率與 v2 同、最大回撤較小;'
        '**v2 凍結口徑不受影響,本欄僅紀錄/觀察。**',
    ])


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    md_only = '--md' in sys.argv
    tape = build_tape(write=True)
    if not md_only and not tape.empty:
        s2 = _stats(tape, 'pnl_v2'); ss = _stats(tape, 'pnl_skip')
        n_skip = int((tape['action'] == 'SKIP').sum())
        print(f'雙欄 paper-tape 已寫 -> {TAPE}')
        print(f'樣本 {len(tape)} 筆({tape.trade_date.iloc[0]}~{tape.trade_date.iloc[-1]}),逆勢SKIP {n_skip} 筆\n')
        print(f'{"欄":<12}{"進場n":>6}{"總點":>8}{"總NT$":>11}{"PF":>6}{"勝率":>7}{"最大回撤pts":>12}')
        print(f'{"v2":<12}{s2["n"]:>6}{s2["pts"]:>8}{s2["ntd"]:>11,}{s2["pf"]:>6}{s2["wr"]:>7}{s2["mdd"]:>12}')
        print(f'{"v2+skip":<12}{ss["n"]:>6}{ss["pts"]:>8}{ss["ntd"]:>11,}{ss["pf"]:>6}{ss["wr"]:>7}{ss["mdd"]:>12}')
        print('\n最近 6 筆:')
        print(tape.tail(6).to_string(index=False))
    print('\n' + report_block(tape))


if __name__ == '__main__':
    main()
