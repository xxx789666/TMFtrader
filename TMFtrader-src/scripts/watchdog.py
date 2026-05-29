"""
scripts/watchdog.py — TMFtrader 自癒看門狗

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
  - 週六日不重啟、不推送 TG

啟動方式：
  python scripts/watchdog.py              # 日盤 port 8888（預設）
  python scripts/watchdog.py --night      # 夜盤 port 8889
  （或用 start_watchdog.bat 背景執行）
"""

import argparse
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


def is_market_day() -> bool:
    """週一至週五（weekday 0-4）才推送 TG 與執行重啟；週六日靜默。"""
    return datetime.now(TW_TZ).weekday() < 5

# Windows 終端機可能是 cp950，強制 UTF-8 輸出（不影響 log 檔）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ─── 模式選擇（--night 切換夜盤）────────────────────────────────────────────
_arg_parser = argparse.ArgumentParser(add_help=False)
_arg_parser.add_argument("--night", action="store_true")
_args, _ = _arg_parser.parse_known_args()
NIGHT_MODE = _args.night

# ─── 設定 ────────────────────────────────────────────────────────────────────
PROJECT_DIR  = Path(__file__).parent.parent
LOGS_DIR     = PROJECT_DIR / "data" / "logs"

if NIGHT_MODE:
    SERVER_URL   = "http://localhost:8889"
    WATCHDOG_LOG = LOGS_DIR / "watchdog_night.log"
    _MODE_LABEL  = "夜盤(8889)"
else:
    SERVER_URL   = "http://localhost:8888"
    WATCHDOG_LOG = LOGS_DIR / "watchdog.log"
    _MODE_LABEL  = "日盤(8888)"

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
    """返回今日（或最新）的 TMFtrader_*.log，不存在返回 None。"""
    today = datetime.now().strftime("%Y%m%d")
    candidate = LOGS_DIR / f"TMFtrader_{today}.log"
    if candidate.exists():
        return candidate
    # fallback：找最新的 .log（排除 watchdog.log）
    logs = sorted(
        [p for p in LOGS_DIR.glob("TMFtrader_*.log") if not p.name.endswith(".gz")],
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
if NIGHT_MODE:
    CLOSE_WINDOWS = [
        (_t(4,  45), _t(5,   0)),   # 夜盤收盤前
    ]
else:
    CLOSE_WINDOWS = [
        (_t(13, 25), _t(13, 45)),   # 日盤收盤前
        (_t(3,  45), _t(4,   0)),   # 夜盤收盤前（TMF 04:00 收盤）
    ]

# 日夜盤交接空窗期（此時心跳正常會暫停，熔斷器也可能非 active）
if NIGHT_MODE:
    # 夜盤 watchdog 只管夜盤：05:00 收盤後 → 15:00 開盤前，整段靜默
    GAP_WINDOWS = [
        (_t(5,   0), _t(15,  0)),   # 夜盤收盤 → 下次夜盤開盤（日盤時段全程不管）
    ]
else:
    GAP_WINDOWS = [
        (_t(13, 45), _t(15,  5)),   # 日→夜 交接
        (_t(4,   0), _t(8,  45)),   # 夜盤收盤(04:00) → 日盤開盤
    ]

# 交易時段（只在此期間監控心跳、允許重啟）
if NIGHT_MODE:
    TRADING_SESSIONS = [
        (_t(15,  0), _t(23, 59)),   # 夜盤（前段）
        (_t(0,   0), _t(5,   0)),   # 夜盤（後段，跨午夜）
    ]
else:
    TRADING_SESSIONS = [
        (_t(8,  45), _t(13, 45)),   # 日盤
        (_t(15,  0), _t(23, 59)),   # 夜盤（前段）
        (_t(0,   0), _t(4,   0)),   # 夜盤後段（TMF 04:00 收盤）
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
    """在交易時段內回 True（不限定星期，補班日也適用）。"""
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


def _server_port() -> int:
    """返回當前監控的 port。"""
    return 8889 if NIGHT_MODE else 8888


def find_server_pid() -> "int | None":
    """找對應 port 的 PID（PowerShell 優先，fallback netstat）。"""
    port = _server_port()
    # 方法 1：PowerShell Get-NetTCPConnection（最可靠）
    try:
        out = subprocess.check_output(
            [
                "powershell.exe", "-NoProfile", "-Command",
                f"(Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue)"
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
            if f":{port}" in line and "LISTEN" in line:
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

    if NIGHT_MODE:
        # 夜盤：直接呼叫 Python（與日盤相同方式），避免 bat→cmd 在無 console 環境下失敗
        night_script = str(PROJECT_DIR / "scripts" / "start_night.py")
        proc = subprocess.Popen(
            [PYTHON_EXE, night_script],
            cwd=str(PROJECT_DIR),
            creationflags=DETACHED | NEW_GRP | _CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    else:
        # 日盤：用 scripts/start.py
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
    if is_market_day():
        tg(f"[TMFtrader] [{_MODE_LABEL}] 伺服器重啟\n原因: {reason}\n時間: {tw_now().strftime('%H:%M:%S')}")

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
        if is_market_day():
            tg(f"[TMFtrader] [{_MODE_LABEL}] 伺服器重啟成功 [OK]\n時間: {tw_now().strftime('%H:%M:%S')}")
        # 清除 emergency / halted 狀態
        if call_resume():
            wlog("[Restart] [OK] 熔斷器已 resume")
        else:
            wlog("[Restart] [WARN] 熔斷器 resume 失敗（下次輪詢再重試）")
    else:
        wlog("[Restart] [ERR] 伺服器啟動失敗，下次輪詢再重試")
        if is_market_day():
            tg(f"[TMFtrader] [{_MODE_LABEL}] 伺服器啟動失敗！需要手動處理\n時間: {tw_now().strftime('%H:%M:%S')}")


# ─── 心跳監控（[Scan] 日盤 / [Heartbeat] TMFN 夜盤）────────────────────────

# 日盤：每 5 分 K 掃描一次；夜盤 ORB 不寫 [Scan]，改用 [Heartbeat] TMFN（約 90s 一次）
_SCAN_RE_DAY   = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[Scan\]")
_SCAN_RE_NIGHT = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[Heartbeat\] TMFN:")
_SCAN_RE = _SCAN_RE_NIGHT if NIGHT_MODE else _SCAN_RE_DAY
_SCAN_LABEL = "Heartbeat TMFN" if NIGHT_MODE else "Scan"


def last_scan_age_seconds() -> "float | None":
    """
    返回最後一筆心跳日誌距今幾秒。
    日盤監控 [Scan]，夜盤監控 [Heartbeat] TMFN。
    找不到或 log 不存在 → None。
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
    _OK_TG_INTERVAL = 3600  # 「策略運行正常」TG 推播間隔（秒），每小時最多一次

    def __init__(self):
        self._last_restart: float = 0.0   # unix timestamp
        self._solace_was_down: bool = False  # 追蹤 Solace 斷線狀態
        self._no_scan_since: "float | None" = None  # 首次偵測到無心跳的時間
        self._last_ok_tg: float = 0.0     # 上次「策略運行正常」TG 時間

    def _can_restart(self) -> bool:
        # 注意：不依賴 is_market_day()，補班日（周日開盤）也需能重啟
        # TG 通知才用 is_market_day() 過濾（避免平日週末噪音）
        if in_window(CLOSE_WINDOWS):
            wlog("[Skip] 收盤關閉窗口，不重啟")
            return False
        if in_window(GAP_WINDOWS):
            wlog("[Skip] 日夜交接空窗期，不重啟")
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
            if not in_trading_session():
                wlog("[Check] 伺服器無回應（休盤中，正常）")
                return
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
            if in_window(GAP_WINDOWS) or not in_trading_session():
                wlog(f"[CB] state={cb_state}，非交易時段，跳過 resume")
            else:
                halt_reason = cb.get("halt_reason", "")
                wlog(f"[CB] state={cb_state} reason='{halt_reason}'，嘗試 resume...")
                tg(f"[TMFtrader] [{_MODE_LABEL}] 熔斷器觸發\n狀態: {cb_state}\n原因: {halt_reason}\n時間: {tw_now().strftime('%H:%M:%S')}")
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
                if fb_min < 2 and is_market_day() and in_trading_session():
                    tg(f"[TMFtrader] [{_MODE_LABEL}] Solace Tick 斷線\nfallback 模式啟動\n時間: {tw_now().strftime('%H:%M:%S')}")
                if fb_min * 60 > FALLBACK_TIMEOUT:
                    # Bug 2+3 已修復：hard TP/SL 在 engine 層獨立運作，持倉重啟安全
                    if self._can_restart():
                        self._do_restart(f"Solace fallback {fb_min:.0f} 分鐘")
            else:
                if self._solace_was_down:
                    wlog("[Tick] Solace 已恢復 [OK]")
                    if is_market_day() and in_trading_session():
                        tg(f"[TMFtrader] [{_MODE_LABEL}] Solace Tick 已恢復正常\n時間: {tw_now().strftime('%H:%M:%S')}")
                    self._solace_was_down = False
                else:
                    wlog("[Tick] Solace [OK]")

        # ── 4. 心跳監控（日盤:[Scan] / 夜盤:[Heartbeat] TMFN）────────────────
        if in_trading_session():
            age = last_scan_age_seconds()
            # 持倉中 [Scan] 不寫是 engine.py 已知設計缺陷（持倉分支不呼叫 on_kbar）。
            # 暫時靜音避免 TG spam（明天修 engine.py 後可移除此 workaround）。
            # 持倉欄位：單商品在 state["position"]，多商品在 state["instruments_data"][inst]["position"]
            holding = False
            _single_pos = state.get("position") or {}
            if isinstance(_single_pos, dict) and (_single_pos.get("quantity", 0) or 0) > 0:
                holding = True
            if not holding:
                for _inst_data in (state.get("instruments_data") or {}).values():
                    _p = ((_inst_data or {}).get("position") or {})
                    if (_p.get("quantity", 0) or 0) > 0:
                        holding = True
                        break
            if age is None:
                # 今日 log 從未有心跳（token 過期/日誌輪轉）
                # 追蹤「無心跳」持續時間，超過 SCAN_TIMEOUT 視為卡住
                if self._no_scan_since is None:
                    self._no_scan_since = time.time()
                    wlog(f"[{_SCAN_LABEL}] 找不到心跳紀錄，開始計時...")
                else:
                    no_scan_elapsed = time.time() - self._no_scan_since
                    wlog(f"[{_SCAN_LABEL}] 仍無心跳紀錄，已持續 {no_scan_elapsed/60:.1f} 分鐘 holding={holding}")
                    if no_scan_elapsed > SCAN_TIMEOUT:
                        if holding:
                            wlog(f"[{_SCAN_LABEL}] 持倉中 [Scan] 缺失（已知設計缺陷），靜音不重啟")
                        else:
                            wlog(f"[{_SCAN_LABEL}] [WARN] {no_scan_elapsed/60:.1f} 分鐘無任何心跳，策略疑似卡住")
                            tg(f"[TMFtrader] [{_MODE_LABEL}] 策略心跳異常\n{no_scan_elapsed/60:.1f} 分鐘無 [{_SCAN_LABEL}]\n時間: {tw_now().strftime('%H:%M:%S')}")
                            if self._can_restart():
                                self._no_scan_since = None
                                self._do_restart(f"[{_SCAN_LABEL}] {no_scan_elapsed/60:.0f} 分鐘無心跳")
            elif age > SCAN_TIMEOUT:
                self._no_scan_since = None
                if holding:
                    wlog(f"[{_SCAN_LABEL}] 持倉中 [Scan] 缺失（已知設計缺陷），靜音不重啟（age={age/60:.1f}min）")
                else:
                    wlog(f"[{_SCAN_LABEL}] [WARN] {age/60:.1f} 分鐘沒有心跳，策略疑似卡住")
                    tg(f"[TMFtrader] [{_MODE_LABEL}] 策略心跳異常\n{age/60:.1f} 分鐘沒有 [{_SCAN_LABEL}]\n時間: {tw_now().strftime('%H:%M:%S')}")
                    if self._can_restart():
                        self._do_restart(f"[{_SCAN_LABEL}] {SCAN_TIMEOUT//60} 分鐘沒更新")
            else:
                self._no_scan_since = None
                wlog(f"[{_SCAN_LABEL}] [OK] 最後心跳 {age:.0f}s 前")
                # 每小時推一次「策略運行正常」（僅交易日）
                if is_market_day() and (time.time() - self._last_ok_tg) > self._OK_TG_INTERVAL:
                    self._last_ok_tg = time.time()
                    tg(f"[TMFtrader] [{_MODE_LABEL}] 策略運行正常\n心跳: {age:.0f}s 前\n引擎: {state.get('engine_state','?')}\n時間: {tw_now().strftime('%H:%M:%S')}")
        else:
            self._no_scan_since = None
            wlog(f"[{_SCAN_LABEL}] 非交易時段，跳過心跳檢查")

    def run(self):
        wlog("=" * 55)
        wlog(f"[Watchdog] TMFtrader 自癒看門狗啟動 [{_MODE_LABEL}]")
        wlog(f"  伺服器 : {SERVER_URL}")
        wlog(f"  日誌目錄: {LOGS_DIR}")
        wlog(f"  輪詢   : {CHECK_INTERVAL}s | Scan超時: {SCAN_TIMEOUT//60}min | 重啟冷卻: {RESTART_COOLDOWN}s")
        wlog("=" * 55)
        tg(f"[TMFtrader] [{_MODE_LABEL}] Watchdog 啟動\n監控: {SERVER_URL}\n時間: {tw_now().strftime('%Y-%m-%d %H:%M:%S')}")

        while True:
            try:
                self.tick()
            except Exception as e:
                wlog(f"[Watchdog] 輪詢例外: {e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    Watchdog().run()
