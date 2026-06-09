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
TODAY = date.today().isoformat()


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
    """整數顯示(net_OI/flow)。"""
    v = _fnum(x)
    return f"{v:+,.0f}" if v is not None else str(x)


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


def _exec_pos(owner):
    p = ROOT / "data" / "paper" / owner / "active_position.json"
    d = _json(p)
    if not d or d.get("owner") != owner:
        return "無倉"
    side = d.get("side", "?"); qty = d.get("quantity", "?"); px = d.get("entry_price", 0)
    return f"持倉 {side} x{qty} @ {px:.0f}"


def write_chips():
    sig = _json(CHIPS / "next_signal.json") or {}
    hist = _rows(CHIPS / "history.csv")
    tape = _rows(CHIPS / "decisions.csv")
    last = hist[-1] if hist else {}

    def px(k):
        v = _fnum(last.get(k))
        return f"{v:.0f}" if v is not None else "?"
    side_zh = {"long": "做多 ▲", "short": "做空 ▼", "flat": "空手"}.get(sig.get("side"), sig.get("side", "?"))
    today_trade = [r for r in tape if str(r.get("trade_date", ""))[:10] == TODAY]

    L = [
        "---",
        f"title: 籌碼日報 chips_combo {TODAY}",
        f"date: {TODAY}",
        "tags: [籌碼, 每日報告, chips_combo]",
        "---",
        f"# 籌碼日報 — chips_combo / {TODAY}",
        "",
        "## 明日決策",
        f"- combo **{sig.get('combo', '?')}** → **{side_zh}**"
        f"(z_flow {sig.get('z_flow', '?')} / z_lt {sig.get('z_lt', '?')})",
        f"- 交易日: {sig.get('trade_date', '?')}",
        "",
        "## 今日籌碼原料（history.csv 最新）",
        f"- 外資淨OI: **{_i(last.get('net_OI'))}** | flow(Δ1d): **{_i(last.get('flow'))}** "
        f"| 大戶 all_ratio: **{_r(last.get('all_ratio'))}**",
        f"- 大台 TX 收盤: {px('close')}（OHLC {px('open')}/{px('high')}/{px('low')}/{px('close')}）",
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
        f"## 累積 tape（小台 pv50）\n- {_tape_stats(tape)}",
        "",
        f"## 真 tick 執行（chips_exec）\n- {_exec_pos('chips_exec')}",
        "",
        "---",
        "相關: [[strategy_chips_combo_v1]]",
    ]
    out = REPORTS / "chips_combo"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{TODAY}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"寫 {out / (TODAY + '.md')}")


def write_maxpain():
    sig = _json(MAXP / "next_signal.json") or {}
    signals = _json(MAXP / "signals.json") or {}
    tape = _rows(MAXP / "decisions.csv")
    state = sig.get("state", "?")
    state_zh = {"flat": "空手（等下個訊號日）", "signal_fired": "訊號已出、明日開盤進",
                "open": "持倉中（抱到結算）"}.get(state, state)
    latest_sig = signals.get(max(signals)) if signals else {}
    today_trade = [r for r in tape if str(r.get("exit_date", ""))[:10] == TODAY]

    L = [
        "---",
        f"title: 選擇權日報 maxpain {TODAY}",
        f"date: {TODAY}",
        "tags: [籌碼, 每日報告, maxpain, 選擇權]",
        "---",
        f"# 選擇權日報 — maxpain_v2 / {TODAY}",
        "",
        "## 當前狀態",
        f"- **{state_zh}**",
    ]
    if state in ("signal_fired", "open"):
        L.append(f"- 訊號日 {sig.get('signal_t','?')} | 目標到期 {sig.get('ed','?')} "
                 f"| dist {sig.get('dist','?')} | MaxPain {sig.get('maxpain','?')}")
    if state == "open":
        L.append(f"- S1 {sig.get('S1','?')} | 加碼線 {sig.get('scale_at','?')} | 停損線 {sig.get('stop_at','?')}")
    L += [
        "",
        "## 最近一個 Max Pain（signals.json 最新）",
        f"- 到期 {latest_sig.get('ed','?')} | MaxPain **{latest_sig.get('maxpain','?')}** "
        f"| 現價 {latest_sig.get('close','?')} | dist **{latest_sig.get('dist','?')}**" if latest_sig else "- 無",
        "",
        "## 今日結算成交",
    ]
    if today_trade:
        for r in today_trade:
            L.append(f"- long 進 {r.get('S1','')} → 出 {r.get('exit_px','')} "
                     f"**{_fnum(r.get('pnl')) or 0:+,.0f} 元** {r.get('exit_reason','')}（{r.get('lots','')}口）")
    else:
        L.append("- 無（未到結算日或空手）")
    L += [
        "",
        f"## 累積 tape（小台 pv50）\n- {_tape_stats(tape)}",
        "",
        f"## 真 tick 執行（maxpain_exec）\n- {_exec_pos('maxpain_exec')}",
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
