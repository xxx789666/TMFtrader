"""靜默失敗掃描器 — 迴圈 #7 的裁判。

AST 掃 core/ strategy/ scripts/ 的 .py,抓「該炸卻沉默」的 pattern:
  - bare-except:`except:`(吞下 KeyboardInterrupt 在內的一切)
  - swallow:except 區塊整塊只有 pass/continue/... (無 log、無告警、無 re-raise)
正當沉默進 allowlist:scripts/silent_allowlist.txt,格式「相對路徑:行內識別子串  # 理由」,
每筆必附理由。allowlist 不是垃圾桶 — 濫塞 = 弱化檢查器。

用法:python scripts/scan_silent_failures.py
exit code: 0 = SILENT: 0 / 1 = 有未豁免項。最後一行固定 SILENT: N 供迴圈 grep。
"""
import ast, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["core", "strategy", "scripts"]
ALLOWLIST = ROOT / "scripts" / "silent_allowlist.txt"


def load_allowlist():
    entries = []
    if ALLOWLIST.exists():
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            path, _, needle = line.partition(":")
            entries.append((path.strip().replace("\\", "/"), needle.strip()))
    return entries


def is_swallow_body(body):
    """except 區塊只有 pass/continue/省略號 → 純吞噬"""
    for stmt in body:
        if isinstance(stmt, (ast.Pass, ast.Continue)):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring / ...
        return False
    return True


def scan_file(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        return [(0, f"SyntaxError 無法掃描: {e.msg}")]
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                hits.append((node.lineno, "bare-except(except: 吞下一切)"))
            elif is_swallow_body(node.body):
                hits.append((node.lineno, "swallow(except 區塊只有 pass/continue,無告警無 log)"))
    return hits


def main():
    allow = load_allowlist()
    lines_cache = {}
    findings, waived = [], 0
    for dname in SCAN_DIRS:
        for p in sorted((ROOT / dname).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            for lineno, why in scan_file(p):
                if rel not in lines_cache:
                    lines_cache[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
                src = lines_cache[rel][lineno - 1].strip() if 0 < lineno <= len(lines_cache[rel]) else ""
                if any(rel == ap and needle and needle in src for ap, needle in allow):
                    waived += 1
                    continue
                findings.append(f"{rel}:{lineno}: {why}  | {src[:70]}")

    print(f"=== scan_silent_failures ({', '.join(SCAN_DIRS)}/) ===")
    for f in findings:
        print(f"  [SILENT] {f}")
    print(f"(allowlist 豁免 {waived} 筆)")
    print(f"SILENT: {len(findings)}")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
