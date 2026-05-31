"""隔離 paper 入口。

存在理由:讓 paper 進程的 cmdline 是 `scripts/start_paper.py`(**不含** `scripts/start.py`),
這樣 live 日盤的 `restart_day.sh`(`pkill -f 'scripts/start.py'`)與 `watchdog_alert.sh`
(`pgrep -f 'scripts/start.py'`)都**不會誤殺/誤判** paper 進程 —— 同 `paper_night_orb.py` 用獨立
檔名的隔離手法。

行為完全等同 `scripts/start.py`:直接呼叫其 `main()`,所有參數照吃(--mode/--port/--no-browser)。
paper 請務必用獨立 --port(例 8890)避免撞 live 的 8888。
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))          # 讓 `import start` 找得到
sys.path.insert(0, str(_SCRIPTS.parent))   # PROJECT_ROOT(start 內部也會再 insert 一次)

import start  # noqa: E402

if __name__ == "__main__":
    start.main()
