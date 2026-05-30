"""
core/notify.py — TMFtrader Telegram 通知
所有推播邏輯集中於此，失敗靜默（不影響交易主流程）。

設定方式：在 .env 內提供
    TG_BOT_TOKEN=...
    TG_CHAT_ID=...
（舊變數名 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 也支援）
"""

import json
import os
import urllib.request
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TG_TOKEN = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT_ID")  or os.environ.get("TELEGRAM_CHAT_ID", "")


def tg(msg: str):
    """推送 Telegram 訊息，失敗靜默。"""
    try:
        payload = json.dumps({"chat_id": TG_CHAT, "text": msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=6)
    except Exception:
        pass


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def notify_entry(mode: str, instrument: str, action: str, price: float,
                 qty: int, stop_loss: float, reason: str, take_profit: float = 0, trail_dist_pts: float = 0):
    direction = "做多 ▲" if action == "BUY" else "做空 ▼"
    sl_pts = abs(round(price - stop_loss))
    tp_line = ""
    if take_profit and take_profit > 0:
        tp_line = f"硬止盈: {take_profit:.0f}（{abs(round(take_profit - price))}pt）\n"
    trail_line = ""
    if trail_dist_pts and trail_dist_pts > 0:
        trail_line = f"追蹤回落止盈: 高點回落 {trail_dist_pts:.0f}pt 出場\n"
    tg(
        f"{'[PAPER]' if mode == 'paper' else '[LIVE]'} 進場\n"
        f"{instrument} {direction} x{qty}\n"
        f"進場價: {price:.0f}\n"
        f"停損: {stop_loss:.0f}（{sl_pts}pt）\n"
        f"{tp_line}"
        f"{trail_line}"
        f"原因: {reason}\n"
        f"時間: {_now()}"
    )


def notify_exit(mode: str, instrument: str, side: str, price: float,
                pnl: float, pnl_pts: float, reason: str):
    emoji = "✅" if pnl >= 0 else "❌"
    sign  = "+" if pnl >= 0 else ""
    tg(
        f"{'[PAPER]' if mode == 'paper' else '[LIVE]'} 出場 {emoji}\n"
        f"{instrument} {'多' if side == 'long' else '空'}\n"
        f"出場價: {price:.0f}\n"
        f"損益: {sign}{pnl:.0f} 元（{sign}{pnl_pts:.1f}pt）\n"
        f"原因: {reason}\n"
        f"時間: {_now()}"
    )


def notify_exit_failed(instrument: str, reason: str):
    tg(
        f"[LIVE] 出場失敗！請手動確認\n"
        f"{instrument}\n"
        f"原因: {reason}\n"
        f"時間: {_now()}"
    )
