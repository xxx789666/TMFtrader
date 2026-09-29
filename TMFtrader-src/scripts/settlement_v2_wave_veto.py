# -*- coding: utf-8 -*-
"""settlement_v2 逆勢降險 veto(可選旁路;v2 本體不動)。

規則:用 TAIEX 日線三度數線上 ZigZag 趨勢(長10%/中6%/短3%,因果無前視),
若三度數皆非0 且全部與 v2 訊號方向逆向 → 該筆「逆勢」→ 建議 SKIP(1口無法半倉)。
其餘照做 1 口。定位=降回撤旁路(回測:DD −1358→−640pt、勝率不變60.2%、每筆EV+30→+41、總報酬−6%),
非 alpha、無 2014-19 OOS。見記憶 wave-gann-filter-tests ⑤b。

用法:
  python scripts/settlement_v2_wave_veto.py 2026-06-16 short     # 指定訊號日+方向 → 印雙欄+寫log
  python scripts/settlement_v2_wave_veto.py --md 2026-06-16 short # 只印可貼進報告的 markdown 區塊
"""
import sys
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TAIEX = ROOT / 'data' / 'taiex_daily.csv'
LOG = ROOT / 'data' / 'forward' / 'settlement_v2_veto_log.csv'
DEGREES = [('長勢', 0.10), ('中勢', 0.06), ('短勢', 0.03)]


def zigzag_trend_online(H, L, pct):
    """每根 bar 結束時的 trend 狀態(+1多/-1空/0未定),因果無前視。"""
    n = len(H); s = np.zeros(n, int); trend = 0; ph = H[0]; pl = L[0]
    for i in range(1, n):
        if trend >= 0:
            if H[i] > ph: ph = H[i]
            if L[i] < ph * (1 - pct): trend = -1; pl = L[i]
        if trend <= 0:
            if L[i] < pl: pl = L[i]
            if H[i] > pl * (1 + pct): trend = 1; ph = H[i]
        s[i] = trend
    return s


def decide(signal_date, side, taiex_csv=TAIEX):
    """signal_date=訊號日(週二, str/date), side='long'/'short'。
    回傳 dict(trends, is_counter, action, sig_used_date)。"""
    sd = pd.to_datetime(signal_date).date()
    sgn = 1 if side == 'long' else -1
    w = pd.read_csv(taiex_csv); w['d'] = pd.to_datetime(w['d'])
    w = w.sort_values('d').reset_index(drop=True)
    H = w['High'].values; L = w['Low'].values; dts = w['d'].dt.date.tolist()
    idx = None
    for j in range(len(dts) - 1, -1, -1):
        if dts[j] <= sd:
            idx = j; break
    if idx is None:
        raise ValueError(f'TAIEX 日線無 {sd} 或更早資料')
    trends = {}
    for nm, pct in DEGREES:
        trends[nm] = int(zigzag_trend_online(H[:idx + 1], L[:idx + 1], pct)[-1])
    nonzero = [t for t in trends.values() if t != 0]
    all_counter = len(nonzero) == 3 and all(np.sign(t) == -sgn for t in trends.values())
    return dict(trends=trends, is_counter=bool(all_counter),
                action='SKIP（逆勢降險）' if all_counter else '做 1 口',
                sig_used_date=str(dts[idx]))


def md_block(signal_date, side, r):
    sgn_txt = '做多 ▲' if side == 'long' else '做空 ▼'
    flags = ' / '.join(f'{nm} {("+1多" if t>0 else "-1空" if t<0 else "0未定")}'
                       for nm, t in r['trends'].items())
    counter = '是 → 三度數全逆 v2 方向' if r['is_counter'] else '否(至少一度數順向或未定)'
    lines = [
        '## 🌊 逆勢降險旁欄(可選;v2 本體照常)',
        f'- v2 訊號方向:**{sgn_txt}**',
        f'- 波浪三度數大勢(TAIEX 日線, 用到 {r["sig_used_date"]}):{flags}',
        f'- 逆勢(三度全逆)?**{counter}**',
        f'- **旁欄建議:{r["action"]}**',
        '',
        '> 定位:降回撤旁路,非 alpha。skip 版回測勝率與 v2 同(60.2%),每筆 EV +30→+41 點、'
        '最大回撤 −1358→−640 點、總報酬約 −6%;無 2014–19 OOS。**v2 凍結口徑不受影響,本欄僅紀錄/觀察。**',
    ]
    return '\n'.join(lines)


def append_log(signal_date, side, r):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {'signal_date': str(pd.to_datetime(signal_date).date()), 'side': side,
           '長勢': r['trends']['長勢'], '中勢': r['trends']['中勢'], '短勢': r['trends']['短勢'],
           'is_counter': int(r['is_counter']), 'action': 'SKIP' if r['is_counter'] else 'TAKE'}
    if LOG.exists():
        df = pd.read_csv(LOG)
        df = df[df['signal_date'] != row['signal_date']]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    else:
        df = pd.DataFrame([row])
    df = df.sort_values('signal_date').reset_index(drop=True)
    df.to_csv(LOG, index=False, encoding='utf-8-sig')
    return LOG


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    args = [a for a in sys.argv[1:] if a != '--md']
    md_only = '--md' in sys.argv
    if len(args) < 2:
        print('用法: python scripts/settlement_v2_wave_veto.py [--md] <訊號日YYYY-MM-DD> <long|short>')
        return
    signal_date, side = args[0], args[1]
    r = decide(signal_date, side)
    print(md_block(signal_date, side, r))
    if not md_only:
        p = append_log(signal_date, side, r)
        print(f'\n[log 已更新 -> {p}]')


if __name__ == '__main__':
    main()
