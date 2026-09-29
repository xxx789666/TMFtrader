# -*- coding: utf-8 -*-
"""波浪 fade 濾網 — 引擎(impulse)方向偵測核心。

從 tmf-strategy-lab `wave_track.py`(zigzag/label_adaptive/validate)+
`wave_filter_chips_daynight.dir_full` + `wave_fade_c1_subwave.dir_c1` 抽出的**最小自包含版**:
只保留 producer(scripts/wave_fade_daily.py)算「波浪方向」需要的函式,不拖進 lab 的
chips_regime/chips_attribution 整條依賴鏈(那些只給 lab 的 build_combo/main 用)。

⚠️ 函式邏輯**逐字照搬 lab**,勿改動(handoff §7.4:波浪一律用 dir_full;簡化單錨版會把
   該 +1 的誤判成不表態)。純 Python(無 numpy/pandas 依賴);H/L 可為 list 或 numpy array。
"""


def zigzag(H, L, pct):
    """ZigZag 轉折點。回傳 [(idx, price, 'H'/'L'), ...](含收尾的進行中端點)。"""
    piv = []; trend = 0; ph = H[0]; phi = 0; pl = L[0]; pli = 0
    for i in range(1, len(H)):
        if trend >= 0:
            if H[i] > ph: ph = H[i]; phi = i
            if L[i] < ph * (1 - pct): piv.append((phi, ph, 'H')); trend = -1; pl = L[i]; pli = i
        if trend <= 0:
            if L[i] < pl: pl = L[i]; pli = i
            if H[i] > pl * (1 + pct): piv.append((pli, pl, 'L')); trend = 1; ph = H[i]; phi = i
    # 收尾:把目前未完成的擺動端點補上(進行中的浪)
    if trend >= 0:
        piv.append((phi, ph, 'H'))
    else:
        piv.append((pli, pl, 'L'))
    return piv


def label_adaptive(start_i, H, L, dates, base_pct):
    """從 anchor 低點(start_i,raw index)起,用『自適應門檻』收斂出乾淨的 5 浪骨架:
    從 base_pct 起逐步放大門檻,吸收小幅 sub-swing,直到 anchor 後是 3~6 個浪(W0..W5)。
    回傳 (waves, inprog, eff_pct);waves=[(label,date,price,kind)]。"""
    labels = ['W0', 'W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7']
    for mult in (1, 1.5, 2, 2.5):          # 限制放大倍數,避免度數互相糊掉
        pct = base_pct * mult
        sub = zigzag(H[start_i:], L[start_i:], pct)
        while sub and sub[0][2] != 'H':          # 去掉開頭被記為 'L' 的 anchor 本身
            sub = sub[1:]
        if not sub:                              # 起漲低之後第一筆須為高(W1)
            continue
        waves = [('W0', dates[start_i], float(L[start_i]), 'L')]
        for k, (idx, pr, kind) in enumerate(sub):
            if k + 1 >= len(labels):
                break
            waves.append((labels[k + 1], dates[start_i + idx], float(pr), kind))
        if len(waves) >= 2 and waves[1][1] == waves[0][1]:   # W0,W1 同日 = 雜訊
            continue
        if not (3 <= len(waves) <= 6):
            continue
        last_kind = waves[-1][3]; last_pr = waves[-1][2]
        prior_highs = [pr for (lab, d, pr, k) in waves[:-1] if k == 'H']
        inprog = max(prior_highs) if (last_kind == 'H' and prior_highs and last_pr < max(prior_highs)) else None
        return waves, inprog, pct
    return None, None, None


def validate(waves, inprog):
    """套三法則 + 鐵律。進行中(未過前高)的最後一筆不當『推動失敗』,只當『待確認』。
    回 (合法?, 違規清單, 失效價, 是否含第6條重疊)。"""
    px = {w[0]: w[2] for w in waves}
    viol = []; note6 = False
    W0, W1, W2, W3, W4 = (px.get(k) for k in ['W0', 'W1', 'W2', 'W3', 'W4'])
    if W2 is not None and W0 is not None and W2 < W0:
        viol.append("W2<W0")
    if W3 is not None and W1 is not None and W3 <= W1 and inprog is None:
        viol.append("W3<=W1")
    if W3 is not None and W1 is not None and W2 is not None and W0 is not None:
        L1 = W1 - W0; L3 = W3 - W2
        if L3 < L1 * 0.9 and px.get('W5') is None and inprog is None:
            viol.append("W3short")
    if W4 is not None and W1 is not None and W4 < W1:
        note6 = True
        viol.append("W4overlap")
    hard_fail = ("W2<W0" in viol) or ("W3<=W1" in viol)
    invalid = W4 if W4 is not None else (W2 if W2 is not None else W0)
    return (not hard_fail), viol, invalid, note6


def dir_full(H, Lo, dates, pct):
    """完整 impulse 引擎方向:+1 多頭推動 / -1 推動失敗(淘汰)/ 0 進行中或不表態。
    輪流試多個錨點(最深近低 + 最近兩低),首個成立計法定方向(handoff §7.4 鐵則)。"""
    piv = zigzag(H, Lo, pct)
    if len(piv) < 4:
        return 0
    low_idx = [i for i, p in enumerate(piv) if p[2] == 'L']
    if not low_idx:
        return 0
    recent = low_idx[-8:]; deepest = min(recent, key=lambda i: piv[i][1])
    seen = set()
    for a in dict.fromkeys([deepest] + low_idx[-2:]):
        waves, inprog, eff = label_adaptive(piv[a][0], H, Lo, dates, pct)
        if not waves:
            continue
        key = (round(waves[0][2]), len(waves))
        if key in seen:
            continue
        seen.add(key)
        ok, viol, invalid, note6 = validate(waves, inprog)
        return 0 if inprog is not None else (1 if ok else -1)
    return 0


def dir_c1(H, L, pct):
    """C1 定律(觀察欄、不下單):上升起漲低被最近低跌破 → 上段衰竭 D=+1(反之 -1)。"""
    piv = zigzag(H, L, pct)
    if len(piv) < 3:
        return 0
    p = piv[-3:]; k = [x[2] for x in p]; pr = [x[1] for x in p]
    if k == ['L', 'H', 'L'] and pr[2] < pr[0]:   # 上升起漲低 L0 被最近低 L2 跌破
        return 1
    if k == ['H', 'L', 'H'] and pr[2] > pr[0]:
        return -1
    return 0
