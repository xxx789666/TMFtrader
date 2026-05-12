---
name: Trading server restart on Windows
description: How to reliably kill and restart the uvicorn trading server in the Windows bash environment
type: feedback
---

`taskkill /F /IM python.exe` from Git Bash on Windows fails silently — the server keeps running.

**Correct method:**
1. Find PID: `netstat -ano | grep ":8888"` → get PID
2. Kill by PID: `/c/Windows/System32/taskkill.exe //F //PID <pid>`
3. Verify: `netstat -ano | grep ":8888"` (should show no LISTENING)
4. Start: `cd /path/to/project && nohup python scripts/start.py --no-browser > /tmp/trader.log 2>&1 &`

**Why:** Git Bash translates `/F` to `F:/` (a Windows drive path). Using full path + `//` flags bypasses this.

**How to apply:** Any time the trading server (port 8888) needs to be restarted after code changes.
