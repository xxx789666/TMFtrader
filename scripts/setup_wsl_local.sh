#!/usr/bin/env bash
# WSL2 一鍵 setup —— 補 apt、建 symlink、生 SSH key、裝 Hermes、設 NIM、驗證
#
# 用法：bash /mnt/c/Users/xx/Desktop/vps永豐微台指/scripts/setup_wsl_local.sh
#
# 冪等：可重複執行，每步會先檢查是否已完成
# 失敗：任一步失敗即停（set -e），不會搞砸後續

set -euo pipefail

PROJECT_MNT="/mnt/c/Users/xx/Desktop/vps永豐微台指"
PROJECT_LINK="$HOME/vps_trader"
PROJECT_ENV="$PROJECT_MNT/TMFtrader-src/.env"

# ---------- 工具 ----------
GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RED=$'\e[31m'; RESET=$'\e[0m'
ok()    { echo "${GREEN}✓${RESET} $*"; }
warn()  { echo "${YELLOW}!${RESET} $*"; }
fail()  { echo "${RED}✗${RESET} $*"; exit 1; }
step()  { echo; echo "${YELLOW}━━━ $* ━━━${RESET}"; }

# ---------- 預檢 ----------
step "[0/7] 預檢"
[ -d "$PROJECT_MNT" ] || fail "找不到專案：$PROJECT_MNT"
ok "專案路徑可達"
ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 && ok "網路 OK" || fail "沒網路"

step "[0.5/7] 取得 sudo 權限（可能要密碼）"
sudo -v || fail "sudo 失敗"
ok "sudo 已快取"

# ---------- 1. apt ----------
step "[1/7] apt 補裝缺的套件"
MISSING=()
for pkg in jq python3-pip unzip ca-certificates; do
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -eq 0 ]; then
  ok "全部已裝"
else
  echo "  將裝：${MISSING[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y -qq "${MISSING[@]}"
  ok "apt 完成"
fi

# ---------- 2. symlink ----------
step "[2/7] 建立 ~/vps_trader symlink"
if [ -L "$PROJECT_LINK" ] && [ "$(readlink "$PROJECT_LINK")" = "$PROJECT_MNT" ]; then
  ok "symlink 已存在且正確"
elif [ -e "$PROJECT_LINK" ]; then
  warn "$PROJECT_LINK 已存在但不是預期 symlink，重建中"
  rm -rf "$PROJECT_LINK"
  ln -s "$PROJECT_MNT" "$PROJECT_LINK"
  ok "已重建"
else
  ln -s "$PROJECT_MNT" "$PROJECT_LINK"
  ok "symlink 已建"
fi

step "[2.5/7] 給 scripts/*.sh 執行權限（在 /mnt/c 上 chmod 沒效果、改建 alias）"
# 注：DrvFs 不支援 Linux permission，所以 chmod +x 在 /mnt/c 無效
# 使用 `bash <script>` 而非 `./script` 即可，加 alias 補一下方便手動跑
ok "scripts 可用 `bash <path>` 執行"

# ---------- 3. SSH key ----------
step "[3/7] 產生 ed25519 SSH key（用於 rsync from VPS）"
if [ -f "$HOME/.ssh/id_ed25519" ]; then
  ok "key 已存在"
else
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  ssh-keygen -t ed25519 -C "wsl2-tmf-review" -f "$HOME/.ssh/id_ed25519" -N ""
  ok "key 已產生"
fi
PUB_KEY=$(cat "$HOME/.ssh/id_ed25519.pub")

# ---------- 4. Hermes Agent ----------
step "[4/7] 安裝 Hermes Agent"
if command -v hermes >/dev/null 2>&1; then
  ok "hermes 已裝：$(hermes --version 2>&1 | head -1)"
else
  echo "  下載並執行 install.sh..."
  curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
  # 重 source 讓 PATH 生效
  # shellcheck source=/dev/null
  [ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"
  # 找 hermes 可執行檔
  if command -v hermes >/dev/null 2>&1; then
    ok "hermes 已裝：$(hermes --version 2>&1 | head -1)"
  else
    warn "hermes 未在 PATH，可能要 'source ~/.bashrc' 或重開 shell"
  fi
fi

# ---------- 5. pip 補 review 模組依賴 ----------
step "[5/7] pip 補 review 模組依賴（python-dotenv）"
if python3 -c "import dotenv" 2>/dev/null; then
  ok "python-dotenv 已裝"
else
  pip3 install --user --quiet python-dotenv || pip3 install --break-system-packages --quiet python-dotenv
  python3 -c "import dotenv" && ok "python-dotenv 已裝"
fi

# ---------- 6. 設定 Hermes NIM provider ----------
step "[6/7] 設定 Hermes NIM provider（從 .env 讀 key）"
if [ ! -f "$PROJECT_ENV" ]; then
  warn "找不到 $PROJECT_ENV，跳過"
else
  set -a; source "$PROJECT_ENV"; set +a
  if [ -z "${NVIDIA_API_KEY:-}" ]; then
    warn ".env 內沒 NVIDIA_API_KEY，跳過"
  elif command -v hermes >/dev/null 2>&1; then
    hermes config set NVIDIA_API_KEY "$NVIDIA_API_KEY" >/dev/null
    hermes config set NVIDIA_BASE_URL "${NVIDIA_BASE_URL:-https://integrate.api.nvidia.com/v1}" >/dev/null
    ok "Hermes config 已設 NVIDIA_API_KEY + NVIDIA_BASE_URL"
  else
    warn "hermes 不在 PATH，待手動設"
  fi
fi

# ---------- 7. 驗證 ----------
step "[7/7] 驗證"
echo
echo "Ubuntu:        $(lsb_release -ds 2>/dev/null || cat /etc/os-release | grep PRETTY | cut -d= -f2)"
echo "Python:        $(python3 --version)"
echo "Tools missing: $(for c in curl git rsync ssh jq pip3 unzip; do command -v "$c" >/dev/null 2>&1 || echo -n "$c "; done)"
echo "Symlink:       $(readlink "$PROJECT_LINK" 2>/dev/null || echo MISSING)"
echo "SSH pubkey:    $HOME/.ssh/id_ed25519.pub ($(wc -c < "$HOME/.ssh/id_ed25519.pub") bytes)"
if command -v hermes >/dev/null 2>&1; then
  echo "Hermes:        $(hermes --version 2>&1 | head -1)"
else
  echo "Hermes:        ${RED}NOT IN PATH${RESET} — 請 'source ~/.bashrc' 或新開 shell 後執行 'hermes --version'"
fi

step "Python 端 smoke test（load_week）"
cd "$PROJECT_LINK/TMFtrader-src"
python3 -m review.tools_for_hermes load_week --week_ending=2026-05-15 --compact \
  | python3 -c "import sys, json; d=json.loads(sys.stdin.read()); print('  ✓ load_week OK:', d['week_start'], '-', d['week_end'], 'net=', d['net_pnl'], 'trades=', d['n_trades'])"

echo
echo "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo "${GREEN}✓ Setup 完成${RESET}"
echo "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo
echo "下一步（手動）："
echo "  1. 把以下 SSH public key 加到 GCP VPS（authorized_keys 或 GCP Console → Metadata → SSH keys）："
echo
echo "${YELLOW}-----BEGIN SSH PUBLIC KEY-----${RESET}"
echo "$PUB_KEY"
echo "${YELLOW}-----END SSH PUBLIC KEY-----${RESET}"
echo
echo "  2. cp ~/vps_trader/scripts/.env.sync.example ~/vps_trader/scripts/.env.sync"
echo "     # 編輯 VPS_HOST、TG_BOT_TOKEN 等"
echo
echo "  3. 跑一次手動覆盤："
echo "     bash ~/vps_trader/scripts/run_weekly_review.sh"
