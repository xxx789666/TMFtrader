"""每日籌碼分析報告(Obsidian MD)— chips_combo 與 maxpain 各產一支獨立 MD。

cron 18:45 TST(排在 chips_combo_daily 18:30 / maxpain_daily 18:40 之後)。讀各自 data/ 產出:
  data/reports/chips_combo/YYYY-MM-DD.md
  data/reports/maxpain/YYYY-MM-DD.md
各帶 Obsidian frontmatter + [[wikilink]]。純讀取 + 寫檔,不碰交易。本機用 rsync 拉進 Obsidian vault。
"""
import csv
import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHIPS = ROOT / "data" / "chips_combo"
MAXP = ROOT / "data" / "maxpain_v2"
REPORTS = ROOT / "data" / "reports"
TODAY = os.environ.get("REPORT_DATE") or date.today().isoformat()   # 可用 REPORT_DATE 補產任一天


def _json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def _rows(p):
    try:
        with open(p, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _i(x):
    """有號整數(net_OI/flow,顯示方向)。"""
    v = _fnum(x)
    return f"{v:+,.0f}" if v is not None else str(x)


def _n(x):
    """無號整數(價格,不顯示 +)。"""
    v = _fnum(x)
    return f"{v:,.0f}" if v is not None else str(x)


def _r(x, nd=4):
    """小數四捨五入(all_ratio)。"""
    v = _fnum(x)
    return f"{v:+.{nd}f}" if v is not None else str(x)


def _tape_stats(rows):
    pnls = [v for v in (_fnum(r.get("pnl")) for r in rows) if v is not None]
    if not pnls:
        return "0 筆"
    n = len(pnls); wins = [x for x in pnls if x > 0]
    gp = sum(wins); gl = -sum(x for x in pnls if x < 0)
    pf = (gp / gl) if gl > 0 else float("inf")
    return f"{n} 筆 淨 {sum(pnls):+,.0f} 元 勝 {len(wins)}/{n} PF {pf:.2f}"


def _tape_period(rows, date_col):
    """tape 的日期區間 'first ~ last（N 筆）'。"""
    ds = sorted(str(r.get(date_col, ""))[:10] for r in rows if r.get(date_col))
    if not ds:
        return "無資料"
    return f"{ds[0]} ~ {ds[-1]}"


def _maxpain_lots(rows):
    """maxpain:加碼(2口)筆數。"""
    two = sum(1 for r in rows if str(r.get("lots", "")).strip() == "2")
    return f"1-2 口含加碼（{two}/{len(rows)} 筆有加碼到 2 口）" if rows else "1-2 口含加碼"


def _exec_pos(owner):
    """引擎真 tick 持倉:有倉時給精確進場時間(到秒)/價/口數(取自引擎持倉鎖)。"""
    p = ROOT / "data" / "paper" / owner / "active_position.json"
    d = _json(p)
    if not d or d.get("owner") != owner:
        return "無倉"
    side = d.get("side", "?"); qty = d.get("quantity", "?"); px = d.get("entry_price", 0)
    et = str(d.get("entry_time", ""))[:19].replace("T", " ")
    return f"持倉 {side} x{qty} @ {px:.0f}(進場 {et}、{d.get('instrument','MXF')})"


def _verdict(combo):
    c = _fnum(combo)
    if c is None:
        return "資料不足"
    if c > 0.5:
        return f"**偏多 → 做多進場 ▲**（combo {c:+.2f} > +0.5）"
    if c < -0.5:
        return f"**偏空 → 做空進場 ▼**（combo {c:+.2f} < −0.5）"
    lean = "略偏多" if c > 0 else ("略偏空" if c < 0 else "中性")
    return f"**中性（{lean}）→ 空手不進場**（combo {c:+.2f} 在 −0.5~+0.5 之間）"


def _zread(z, pos="偏多", neg="偏空"):
    v = _fnum(z)
    if v is None:
        return "?", "?"
    if v > 0.5:
        return f"{v:+.2f}", f"明顯{pos}（高過60日均 {v:+.2f}σ）"
    if v < -0.5:
        return f"{v:+.2f}", f"明顯{neg}（低於60日均 {v:+.2f}σ）"
    return f"{v:+.2f}", f"中性（{v:+.2f}σ）"


def _delta(cur, prev):
    a, b = _fnum(cur), _fnum(prev)
    if a is None or b is None:
        return "—"
    return f"{a - b:+,.0f}"


def _delta4(cur, prev):
    a, b = _fnum(cur), _fnum(prev)
    if a is None or b is None:
        return "—"
    return f"{a - b:+.4f}"


def _night_block(tape_a):
    """夜盤變體 B 紙上 tape 段(night_tape.csv;chips_night_snapshot.py 18:36 記/結)。無檔=空字串。"""
    rows = _rows(CHIPS / "night_tape.csv")
    if not rows:
        return ""
    settled = [r for r in rows if str(r.get("exit", "")).strip()]
    pending = [r for r in rows if not str(r.get("exit", "")).strip()]
    L = ["## 🌙 夜盤變體 B(紙上 tape:訊號當晚 18:36 進、隔日收盤平;forward 驗證中)"]
    if settled:
        pn = [_fnum(r.get("pnl_pts")) or 0 for r in settled]
        wins = sum(1 for x in pn if x > 0)
        gp = sum(x for x in pn if x > 0); gl = -sum(x for x in pn if x < 0)
        pf = gp / gl if gl > 0 else float("inf")
        tot = sum(_fnum(r.get("pnl")) or 0 for r in settled)
        L.append(f"- B 累積:{len(settled)} 筆 勝 {wins}/{len(settled)} PF {pf:.2f} 淨 **{tot:+,.0f} 元**")
        start = str(settled[0].get("trade_date", ""))
        a_same = [r for r in tape_a if str(r.get("trade_date", "")) >= start]
        if a_same:
            ta = sum(_fnum(r.get("pnl")) or 0 for r in a_same)
            L.append(f"- A 現版同期({start} 起 {len(a_same)} 筆):{ta:+,.0f} 元 → **B−A = {tot - ta:+,.0f} 元**")
        last = settled[-1]
        L.append(f"- 最近結算:{last.get('trade_date')} {last.get('side')} 進 {last.get('entry')} → "
                 f"出 {last.get('exit')} **{(_fnum(last.get('pnl')) or 0):+,.0f} 元**({last.get('exit_reason', '')})")
    if pending:
        p = pending[-1]
        L.append(f"- 🟡 持倉中:{p.get('trade_date')} {p.get('side')} 夜盤進 {p.get('entry')}(等隔日收盤結算)")
    L.append("- 依據:回測 2024-26 B 優(2026 夜盤段 +121.6 點/筆)、2020-23 B 劣 → regime 依賴。"
             "判準:forward ~60 筆 B 仍優 → 考慮升真 tick;連續落後 → 關閉。夜盤時段停損未模擬(日盤 OHLC 近似)。")
    return "\n".join(L)


def write_chips():
    sig = _json(CHIPS / "next_signal.json") or {}
    hist = _rows(CHIPS / "history.csv")
    tape = _rows(CHIPS / "decisions.csv")
    cur = hist[-1] if hist else {}
    prev = hist[-2] if len(hist) >= 2 else {}
    d_cur, d_prev = cur.get("date", "最新"), prev.get("date", "前一日")
    zf, zf_txt = _zread(sig.get("z_flow"))
    zl, zl_txt = _zread(sig.get("z_lt"))
    today_trade = [r for r in tape if str(r.get("trade_date", ""))[:10] == TODAY]

    L = [
        "---",
        f"title: 籌碼日報 chips_combo {TODAY}",
        f"date: {TODAY}",
        "tags: [籌碼, 每日報告, chips_combo]",
        "---",
        f"# 籌碼日報 — chips_combo / {TODAY}",
        "",
        "## 📌 今日結論",
        f"- 大盤籌碼:{_verdict(sig.get('combo'))}",
        f"- 下一交易日({sig.get('trade_date','?')})動作:**"
        + {"long": "做多 ▲", "short": "做空 ▼", "flat": "空手"}.get(sig.get("side"), "?") + "**",
        "",
        "## 📊 籌碼明細（前一交易日 → 最新)",
        f"| 指標 | {d_prev} | {d_cur} | 變化 |",
        "|---|--:|--:|--:|",
        f"| 外資淨OI(口) | {_i(prev.get('net_OI'))} | {_i(cur.get('net_OI'))} | "
        f"**{_i(cur.get('flow'))}**(=flow) |",
        f"| 大戶 all_ratio | {_r(prev.get('all_ratio'))} | {_r(cur.get('all_ratio'))} | "
        f"{_delta4(cur.get('all_ratio'), prev.get('all_ratio'))} |",
        f"| 大台 TX 收盤 | {_n(prev.get('close'))} | {_n(cur.get('close'))} | {_delta(cur.get('close'), prev.get('close'))} |",
        "",
        "## 🎯 訊號分數",
        "| 因子 | 值 | 解讀 |",
        "|---|--:|---|",
        f"| z_flow(外資流) | {zf} | {zf_txt} |",
        f"| z_lt(大戶) | {zl} | {zl_txt} |",
        f"| **combo(合成)** | **{sig.get('combo','?')}** | (z_flow+z_lt)/2;>+0.5做多 / <−0.5做空 / 中間空手 |",
        "",
        "## 今日成交",
    ]
    if today_trade:
        for r in today_trade:
            L.append(f"- {r.get('side','')} 進 {r.get('entry','')} → 出 {r.get('exit','')} "
                     f"**{_fnum(r.get('pnl')) or 0:+,.0f} 元** {r.get('exit_reason','')}")
    else:
        L.append("- 無（空手或非交易日）")
    L += [
        "",
        f"## 累積 tape（小台 pv50、固定 1 口、期間 {_tape_period(tape, 'trade_date')}）\n- {_tape_stats(tape)}",
    ]
    _nb = _night_block(tape)
    if _nb:
        L += ["", _nb]
    L += [
        "",
        f"## 真 tick 執行（chips_exec）\n- {_exec_pos('chips_exec')}",
        "",
        "## 📖 名詞解釋",
        "- **外資淨OI**:外資台指期 多單−空單。負=整體偏空。這是「水位」。",
        "- **flow(Δ1d)**:外資淨OI 今天−昨天 = 外資今天的「動作」。正=加多/減空。⭐策略看這個變化、不看水位。",
        "- **大戶 all_ratio**:前十大交易人(前十大買−前十大賣)/全市場OI。負=大戶偏空。",
        "- **z_flow / z_lt**:各自的「60日標準分數(σ)」=今天比過去60天平均高/低幾個標準差;>0偏多、<0偏空。",
        "- **combo**:(z_flow+z_lt)/2 等權合成。**combo>+0.5→做多、<−0.5→做空、−0.5~+0.5→空手**。",
        "",
        "---",
        "相關: [[strategy_chips_combo_v1]]",
    ]
    out = REPORTS / "chips_combo"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{TODAY}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"寫 {out / (TODAY + '.md')}")


def _crash_dualcol(tape):
    """暴跌後訊號(前5日≤−3%)雙欄對照(觀察級濾網,2026-06-13;只記錄、不動凍結)。"""
    flagged = [r for r in tape if str(r.get("crash5d", "")).strip() in ("1", "True", "true")]
    if not flagged:
        return ""
    def _pf(p):
        gl = -sum(x for x in p if x < 0)
        return (sum(x for x in p if x > 0) / gl) if gl > 0 else float("inf")
    allp = [v for v in (_fnum(r.get("pnl")) for r in tape) if v is not None]
    exp = [v for r in tape if str(r.get("crash5d", "")).strip() not in ("1", "True", "true")
           for v in [_fnum(r.get("pnl"))] if v is not None]
    if not allp or not exp:
        return ""
    return (f"\n- 雙欄(「暴跌後訊號」純記錄;濾網已被 2015-19 OOS 否決):無濾 {len(allp)}筆 淨{sum(allp):+,.0f} PF{_pf(allp):.2f}"
            f";剔「前5日≤−3%」後 {len(exp)}筆 淨{sum(exp):+,.0f} PF{_pf(exp):.2f}")


def _mp_verdict(state, dist):
    d = _fnum(dist)
    if state == "open":
        return "**持倉中** — 已做多,抱到該週選結算(中途 +1% 加第2口 / −2% 全停)"
    if state == "stopped":
        return "**已停損出場** — 持倉中途觸 −2% 停損全平(此筆已入帳 tape、現空手等下個訊號)"
    if state == "signal_fired":
        ds = f"(dist {d:+.3f} > 0)" if d is not None else ""
        return f"**訊號已出 → 明日開盤做多第1口** {ds}"
    # flat
    if d is None:
        return "**空手** — 無有效訊號"
    if d > 0:
        return f"**空手等下個訊號日** — 最近 dist {d:+.3f} > 0(MaxPain 在現價之上=偏多),到 ~6 DTE 訊號日才進"
    return f"**空手不做多** — 最近 dist {d:+.3f} ≤ 0(MaxPain 在現價之下/相等),不符做多條件(只做多)"


def write_maxpain():
    sig = _json(MAXP / "next_signal.json") or {}
    signals = _json(MAXP / "signals.json") or {}
    tape = _rows(MAXP / "decisions.csv")
    state = sig.get("state", "?")
    latest = signals.get(max(signals)) if signals else {}
    dist = _fnum(latest.get("dist"))
    dist_pct = f"（{dist*100:+.1f}%）" if dist is not None else ""
    today_trade = [r for r in tape if str(r.get("exit_date", ""))[:10] == TODAY]

    L = [
        "---",
        f"title: 選擇權日報 maxpain {TODAY}",
        f"date: {TODAY}",
        "tags: [籌碼, 每日報告, maxpain, 選擇權]",
        "---",
        f"# 選擇權日報 — maxpain_v2 / {TODAY}",
        "",
        "## 📌 今日結論",
        f"- {_mp_verdict(state, latest.get('dist'))}",
        "",
        "## 📊 Max Pain 明細（最近一次計算）",
        "| 項目 | 值 | 說明 |",
        "|---|--:|---|",
        f"| 目標週選到期 | {latest.get('ed','?')} | ~6 DTE 那檔週選 |",
        f"| **Max Pain** | **{_n(latest.get('maxpain'))}** | 選擇權「最大痛點」價,指數傾向往這靠 |",
        f"| 現價(訊號日收盤) | {_n(latest.get('close'))} | 大台 TX |",
        f"| **dist** | **{_r(latest.get('dist'), 3)} {dist_pct}** | (MaxPain−現價)/現價;**>0 才做多** |",
        "",
    ]
    # 今日重算(僅供參考,不影響決策;maxpain_daily 每晚用當日 OI 對當前相關到期重算,2026-07-04 加)
    rc = sig.get("today_recalc") or {}
    if rc.get("maxpain"):
        _rd = _fnum(rc.get("dist"))
        L += [
            f"## 🔄 今日重算 maxpain（{rc.get('as_of','?')} 盤後 OI・僅供參考,不影響決策）",
            "| 項目 | 值 | 說明 |",
            "|---|--:|---|",
            f"| 對應到期 | {rc.get('ed','?')} | 持倉中=本役到期;空手=下一個週三 |",
            f"| 今日 Max Pain | **{_n(rc.get('maxpain'))}** | 痛點會隨每日 OI 移動 |",
            f"| 今日收盤 | {_n(rc.get('close'))} | 大台 TX |",
            f"| 今日 dist | {_r(rc.get('dist'), 3)}{f'（{_rd*100:+.1f}%）' if _rd is not None else ''} | 參考痛點移動軌跡;進出場仍照訊號日凍結規則 |",
            "",
        ]
    L += [
        "## 持倉狀態",
    ]
    if state == "open":
        lots = sig.get("lots") or 1
        added = bool(sig.get("added"))
        unreal = _fnum(sig.get("unreal_pnl"))
        L += ["| 項目 | 值 |", "|---|--:|",
              f"| 進場日/時間 | **{sig.get('entry_t1','?')} 開盤(08:45)**(日OHLC紙上口徑=開盤價) |",
              f"| 進場價 S1 | **{_n(sig.get('S1'))}**(大台 TX 開盤) |",
              f"| **目前口數** | **{lots} 口**" + ("(已 +1% 加碼第2口 @ " + str(sig.get('S2')) + ")" if added else "(尚未加碼;漲到 " + str(sig.get('scale_at','?')) + " 加第2口)") + " |",
              f"| 均價 | {_n(sig.get('avg_cost', sig.get('S1')))} |",
              f"| 停損線(−2%) | {sig.get('stop_at','?')} |",
              f"| 最新收盤 | {_n(sig.get('last_close'))}(已持有 {sig.get('days_held','?')} 個交易日) |",
              f"| 未實現損益 | **{unreal:+,.0f} 元**(小台 pv50、紙上) |" if unreal is not None else "| 未實現損益 | ? |",
              f"| 出場計畫 | 抱到 **{sig.get('ed','?')} 結算**;中途跌破停損線全平 |"]
        if sig.get("stop_hit"):
            L.append("- ⚠️ 持有期間日低已觸及停損線 → 結算時此筆將記為 stop 出場(日OHLC 回溯口徑)")
        if sig.get("crash5d"):
            L.append(f"- ℹ️ 純資訊:暴跌後訊號(前5日 {sig.get('ret5d',0)*100:+.1f}% ≤ −3%)。此濾網已被"
                     f" 2015-19 真 OOS 否決(剔掉組反而 PF1.34、p=0.60)— 僅長期記錄、無操作含義")
    elif state == "stopped":
        rp = _fnum(sig.get("realized_pnl"))
        L += ["| 項目 | 值 |", "|---|--:|",
              f"| 進場日 | {sig.get('entry_t1','?')} 開盤 @ {_n(sig.get('S1'))} |",
              f"| 停損線(−2%) | {sig.get('stop_at','?')} |",
              f"| **已停損出場** | **{sig.get('exit_d','?')} @ {_n(sig.get('exit_px'))}**(中途跌破停損線全平、未抱到結算) |",
              (f"| **已實現損益** | **{rp:+,.0f} 元**({sig.get('lots',1)} 口、小台 pv50、已入帳 tape) |"
               if rp is not None else "| 已實現損益 | ? |")]
    elif state == "signal_fired":
        L.append(f"- 訊號已出:訊號日 {sig.get('signal_t','?')}、目標到期 {sig.get('ed','?')}、明日開盤進")
        if sig.get("crash5d"):
            L.append(f"- ℹ️ 純資訊:暴跌後訊號(前5日 {sig.get('ret5d',0)*100:+.1f}% ≤ −3%;濾網已被 OOS 否決、僅記錄)")
    else:
        L.append("- 空手(無持倉)")
    L += ["", "## 今日結算成交"]
    if today_trade:
        for r in today_trade:
            L.append(f"- long 進 {r.get('S1','')} → 出 {r.get('exit_px','')} "
                     f"**{_fnum(r.get('pnl')) or 0:+,.0f} 元** {r.get('exit_reason','')}（{r.get('lots','')}口）")
    else:
        L.append("- 無（未到結算日或空手）")
    L += [
        "",
        f"## 累積 tape（小台 pv50、{_maxpain_lots(tape)}、期間 {_tape_period(tape, 'signal_t')}）\n- {_tape_stats(tape)}"
        + _crash_dualcol(tape),
        "",
        f"## 真 tick 執行（maxpain_exec）\n- {_exec_pos('maxpain_exec')}"
        + ("\n- ⚠️ 紙上帳持倉中、但引擎無倉 → divergence(對帳時標註;如基礎設施事故/漏單)"
           if state == "open" and _exec_pos('maxpain_exec') == "無倉" else ""),
        "",
        "## 📖 名詞解釋",
        "- **Max Pain(最大痛點)**:由選擇權各履約價的買權/賣權未平倉量(OI)算出——指數若結算在這個價,"
        "全體選擇權持有者「總損失最小」。理論上造市商/賣方有把指數拉向這裡的傾向(結算前靠攏)。",
        "- **dist =(MaxPain − 現價)/ 現價**:現價離痛點多遠(%)。**dist>0(痛點在現價之上)→ 預期往上靠 → 做多;"
        "dist≤0 → 不做多、空手**(此策略只做多、不做空)。",
        "- **進出場(凍結規則)**:每週週選到期前 ~6 DTE(≈週四)算一次;dist>0 → 隔天開盤做多第1口,"
        "盤中漲 +1% 加第2口、跌 −2% 全停,否則抱到該週選結算。",
        "- **狀態**:空手(等下個訊號日)/ 訊號已出(明日開盤進)/ 持倉中(抱到結算)。",
        "",
        "---",
        "相關: [[strategy_maxpain_v2]]",
    ]
    out = REPORTS / "maxpain"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{TODAY}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"寫 {out / (TODAY + '.md')}")


if __name__ == "__main__":
    write_chips()
    write_maxpain()
