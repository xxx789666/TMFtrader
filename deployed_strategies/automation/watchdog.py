"""
scripts/watchdog.py — UltraTrader 自癒看門狗

每 60 秒檢查一次：
  1. 伺服器是否在線（HTTP GET /api/state）
  2. 熔斷器是否為 active（若非 active 則自動 resume）
  3. [Scan] 日誌是否超過 8 分鐘沒更新（交易時段中）

自動修復：
  - 伺服器掛掉      → 重新啟動
  - 熔斷器非 active → 呼叫 /api/engine/resume
  - [Scan] 超時     → 重新啟動伺服器

安全限制：
  - 收盤前關閉窗口（13:25-13:45、04:45-05:00）不重啟
  - 重啟冷卻 120 秒

啟動方式：
  python scripts/watchdog.py
  （或用 start_watchdog.bat 背景執行）
"""

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, time as dtime, timezone, timedelta
from pathlib import Path

TW_TZ = timezone(timedelta(hours=8))

def tw_now() -> datetime:
    """返回 UTC+8 的當前時間。"""
    return datetime.now(TW_TZ)

# Windows 終端機可能是 cp950，強制 UTF-8 輸出（不影響 log 檔）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ─── 設定 ────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.parent
SERVER_URL  = "http://localhost:8888"
LOGS_DIR    = PROJECT_DIR / "data" / "logs"
WATCHDOG_LOG = LOGS_DIR / "watchdog.log"

# ─── Telegram 通知（從 .env 讀） ─────────────────────────────────────────────
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
TG_TOKEN = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT_ID")  or os.environ.get("TELEGRAM_CHAT_ID", "")

def tg(msg: str):
    """發送 Telegram 通知，失敗靜默（不影響主流程）。"""
    try:
        payload = json.dumps({"chat_id": TG_CHAT, "text": msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass


def get_log_file() -> "Path | None":
    """返回今日（或最新）的 ultratrader_*.log，不存在返回 None。"""
    today = datetime.now().strftime("%Y%m%d")
    candidate = LOGS_DIR / f"ultratrader_{today}.log"
    if candidate.exists():
        return candidate
    # fallback：找最新的 .log（排除 watchdog.log）
    logs = sorted(
        [p for p in LOGS_DIR.glob("ultratrader_*.log") if not p.name.endswith(".gz")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return logs[0] if logs else None

CHECK_INTERVAL      = 60      # 主輪詢間隔（秒）
SCAN_TIMEOUT        = 12 * 60 # [Scan] 超時門檻（秒）
RESTART_COOLDOWN    = 120     # 重啟冷卻（秒）
FALLBACK_TIMEOUT    = 10 * 60 # Solace fallback 超時門檻（秒）：超過此時間自動重啟

# 與伺服器使用同一個 Python 環境
PYTHON_EXE   = sys.executable
START_SCRIPT = str(PROJECT_DIR / "scripts" / "start.py")


# ─── 時段定義 ────────────────────────────────────────────────────────────────

def _t(h, m):
    return dtime(h, m)

# 不允許重啟的窗口（收盤前，有未平倉風險）
CLOSE_WINDOWS = [
    (_t(13, 25), _t(13, 45)),   # 日盤收盤前
    (_t(4,  45), _t(5,   0)),   # 夜盤收盤前
]

# 日夜盤交接空窗期（此時 [Scan] 正常會暫停，熔斷器也可能非 active）
GAP_WINDOWS = [
    (_t(13, 45), _t(15,  5)),   # 日→夜 交接
    (_t(5,   0), _t(8,  45)),   # 夜→日 交接
]

# 交易時段（只在此期間監控 [Scan] 心跳）
TRADING_SESSIONS = [
    (_t(8,  45), _t(13, 45)),   # 日盤
    (_t(15,  0), _t(23, 59)),   # 夜盤（前段，23:59 作為端點）
    (_t(0,   0), _t(5,   0)),   # 夜盤（後段，跨午夜）
]


def _now_t():
    return datetime.now().time()


def in_window(windows) -> bool:
    now = _now_t()
    for start, end in windows:
        if start <= now <= end:
            return True
    return False


def in_trading_session() -> bool:
    return in_window(TRADING_SESSIONS)


# ─── 日誌 ────────────────────────────────────────────────────────────────────

def wlog(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─── HTTP 工具 ───────────────────────────────────────────────────────────────

def get_server_state() -> "dict | None":
    """GET /api/state。成功返回 dict，失敗（連線拒絕 / 逾時）返回 None。"""
    try:
        req = urllib.request.Request(
            f"{SERVER_URL}/api/state",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def call_resume() -> bool:
    """POST /api/engine/resume。成功返回 True。"""
    try:
        req = urllib.request.Request(
            f"{SERVER_URL}/api/engine/resume",
            data=b"",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode())
            return body.get("status") == "ok"
    except Exception:
        return False


# ─── 程序管理 ────────────────────────────────────────────────────────────────

_CREATE_NO_WINDOW = 0x08000000


def find_server_pid() -> "int | None":
    """找 port 8888 的 PID（PowerShell 優先，fallback netstat）。"""
    # 方法 1：PowerShell Get-NetTCPConnection（最可靠）
    try:
        out = subprocess.check_output(
            [
                "powershell.exe", "-NoProfile", "-Command",
                "(Get-NetTCPConnection -LocalPort 8888 -ErrorAction SilentlyContinue)"
                ".OwningProcess",
            ],
            timeout=10,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        ).strip()
        tokens = out.split()
        if tokens:
            pid = int(tokens[0])
            if pid > 0:
                return pid
    except Exception:
        pass

    # 方法 2：netstat fallback
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            timeout=10,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        for line in out.splitlines():
            if ":8888" in line and "LISTEN" in line:
                parts = line.split()
                if parts:
                    return int(parts[-1])
    except Exception:
        pass

    return None


def kill_server(pid: int):
    """用 PowerShell Stop-Process 強制終止。"""
    try:
        subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue",
            ],
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        wlog(f"  已終止 PID {pid}")
    except Exception as e:
        wlog(f"  終止 PID {pid} 失敗: {e}")


def start_server() -> int:
    """背景啟動伺服器，返回新 PID。"""
    DETACHED = 0x00000008   # DETACHED_PROCESS
    NEW_GRP  = 0x00000200   # CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [PYTHON_EXE, START_SCRIPT, "--no-browser"],
        cwd=str(PROJECT_DIR),
        creationflags=DETACHED | NEW_GRP | _CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    wlog(f"  伺服器已啟動 PID={proc.pid}")
    return proc.pid


def restart_server(reason: str):
    """完整重啟流程：kill → wait → start → wait → resume CB。"""
    wlog(f"[Restart] 原因: {reason}")
    tg(f"[UltraTrader] 伺服器重啟\n原因: {reason}\n時間: {tw_now().strftime('%H:%M:%S')}")

    pid = find_server_pid()
    if pid:
        wlog(f"[Restart] 終止舊程序 PID={pid}")
        kill_server(pid)
    else:
        wlog("[Restart] 找不到舊程序（已崩潰）")

    time.sleep(4)

    start_server()
    wlog("[Restart] 等待伺服器初始化（20s）...")
    time.sleep(20)

    # 確認啟動
    state = get_server_state()
    if state is None:
        wlog("[Restart] 伺服器未回應，再等 20s...")
        time.sleep(20)
        state = get_server_state()

    if state:
        wlog("[Restart] [OK] 伺服器已上線")
        tg(f"[UltraTrader] 伺服器重啟成功 [OK]\n時間: {tw_now().strftime('%H:%M:%S')}")
        # 清除 emergency / halted 狀態
        if call_resume():
            wlog("[Restart] [OK] 熔斷器已 resume")
        else:
            wlog("[Restart] [WARN] 熔斷器 resume 失敗（下次輪詢再重試）")
    else:
        wlog("[Restart] [ERR] 伺服器啟動失敗，下次輪詢再重試")
        tg(f"[UltraTrader] 伺服器啟動失敗！需要手動處理\n時間: {tw_now().strftime('%H:%M:%S')}")


# ─── [Scan] 心跳監控 ─────────────────────────────────────────────────────────

_SCAN_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[Scan\]")


def last_scan_age_seconds() -> "float | None":
    """
    返回最後一筆 [Scan] 日誌距今幾秒。
    找不到 [Scan] 或 log 檔不存在 → None。
    """
    log_file = get_log_file()
    if log_file is None or not log_file.exists():
        return None

    # 只讀尾部 4096 bytes，避免大檔案 I/O
    try:
        size = log_file.stat().st_size
        offset = max(0, size - 4096)
        with open(log_file, "rb") as f:
            f.seek(offset)
            tail = f.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    last_ts = None
    for match in _SCAN_RE.finditer(tail):
        try:
            ts = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            if last_ts is None or ts > last_ts:
                last_ts = ts
        except ValueError:
            pass

    if last_ts is None:
        return None

    return (datetime.now() - last_ts).total_seconds()


# ─── 主輪詢 ──────────────────────────────────────────────────────────────────

class Watchdog:
    def __init__(self):
        self._last_restart: float = 0.0   # unix timestamp
        self._solace_was_down: bool = False  # 追蹤 Solace 斷線狀態

    def _can_restart(self) -> bool:
        if in_window(CLOSE_WINDOWS):
            wlog("[Skip] 收盤關閉窗口，不重啟")
            return False
        elapsed = time.time() - self._last_restart
        if elapsed < RESTART_COOLDOWN:
            remaining = int(RESTART_COOLDOWN - elapsed)
            wlog(f"[Skip] 重啟冷卻中（還需 {remaining}s）")
            return False
        return True

    def _do_restart(self, reason: str):
        self._last_restart = time.time()
        restart_server(reason)

    def tick(self):
        # ── 1. 伺服器健康檢查 ───────────────────────────────────────────────
        state = get_server_state()

        if state is None:
            wlog("[Check] 伺服器無回應 [ERR]")
            if self._can_restart():
                self._do_restart("伺服器無回應")
            return

        engine_state = state.get("engine_state", "?")
        wlog(f"[Check] 伺服器在線 engine={engine_state} [OK]")

        # ── 2. 熔斷器狀態 ───────────────────────────────────────────────────
        cb = state.get("risk", {}).get("circuit_breaker", {})
        cb_state = cb.get("state", "active")

        if cb_state != "active":
            if in_window(GAP_WINDOWS):
                wlog(f"[CB] state={cb_state}，交接空窗期，跳過 resume")
            else:
                halt_reason = cb.get("halt_reason", "")
                wlog(f"[CB] state={cb_state} reason='{halt_reason}'，嘗試 resume...")
                tg(f"[UltraTrader] 熔斷器觸發\n狀態: {cb_state}\n原因: {halt_reason}\n時間: {tw_now().strftime('%H:%M:%S')}")
                if call_resume():
                    wlog("[CB] [OK] resume 成功")
                else:
                    wlog("[CB] [ERR] resume 失敗")
        else:
            wlog("[CB] state=active [OK]")

        # ── 3. Solace Tick 連線監控 ─────────────────────────────────────────
        tick_status = state.get("tick_status", {})
        if tick_status:
            if tick_status.get("fallback_active"):
                fb_min = tick_status.get("fallback_minutes", 0)
                wlog(f"[Tick] Solace 斷線，fallback 模式已 {fb_min:.1f} 分鐘 [WARN]")
                self._solace_was_down = True
                # 第一次偵測到 fallback 時通知（fb_min < 2 表示剛開始）
                if fb_min < 2:
                    tg(f"[UltraTrader] Solace Tick 斷線\nfallback 模式啟動\n時間: {tw_now().strftime('%H:%M:%S')}")
                if fb_min * 60 > FALLBACK_TIMEOUT:
                    pos_side = state.get("position", {}).get("side", "flat")
                    if pos_side != "flat":
                        wlog(f"[Tick] 持倉中 side={pos_side}，跳過重啟")
                    elif self._can_restart():
                        self._do_restart(f"Solace fallback {fb_min:.0f} 分鐘")
            else:
                if self._solace_was_down:
                    wlog("[Tick] Solace 已恢復 [OK]")
                    tg(f"[UltraTrader] Solace Tick 已恢復正常\n時間: {tw_now().strftime('%H:%M:%S')}")
                    self._solace_was_down = False
                else:
                    wlog("[Tick] Solace [OK]")

        # ── 4. [Scan] 心跳監控 ──────────────────────────────────────────────
        if in_trading_session():
            age = last_scan_age_seconds()
            if age is None:
                wlog("[Scan] 找不到 [Scan] 紀錄（策略尚未掃描或日誌尚未生成）")
            elif age > SCAN_TIMEOUT:
                wlog(f"[Scan] [WARN] {age/60:.1f} 分鐘沒有 [Scan]，策略疑似卡住")
                tg(f"[UltraTrader] 策略心跳異常\n{age/60:.1f} 分鐘沒有 [Scan]\n時間: {tw_now().strftime('%H:%M:%S')}")
                # 有開倉時不重啟（持倉表示引擎仍在運作，心跳正常）
                pos_side = state.get("position", {}).get("side", "flat")
                if pos_side != "flat":
                    wlog(f"[Scan] 持倉中 side={pos_side}，跳過重啟（引擎正在管理持倉）")
                elif self._can_restart():
                    self._do_restart(f"[Scan] {SCAN_TIMEOUT//60} 分鐘沒更新")
            else:
                wlog(f"[Scan] [OK] 最後掃描 {age:.0f}s 前")
        else:
            wlog("[Scan] 非交易時段，跳過心跳檢查")

    def run(self):
        wlog("=" * 55)
        wlog("[Watchdog] UltraTrader 自癒看門狗啟動")
        wlog(f"  伺服器 : {SERVER_URL}")
        wlog(f"  日誌目錄: {LOGS_DIR}")
        wlog(f"  輪詢   : {CHECK_INTERVAL}s | Scan超時: {SCAN_TIMEOUT//60}min | 重啟冷卻: {RESTART_COOLDOWN}s")
        wlog("=" * 55)
        tg(f"[UltraTrader] Watchdog 啟動\n時間: {tw_now().strftime('%Y-%m-%d %H:%M:%S')}")

        while True:
            try:
                self.tick()
            except Exception as e:
                wlog(f"[Watchdog] 輪詢例外: {e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    Watchdog().run()
