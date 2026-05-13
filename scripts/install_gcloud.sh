#!/usr/bin/env bash
# 在 WSL2 內安裝 gcloud CLI（apt 路線、最穩）
set -euo pipefail

if command -v gcloud >/dev/null 2>&1; then
  echo "✓ gcloud 已裝: $(gcloud --version | head -1)"
  exit 0
fi

echo "━━━ 安裝 gcloud CLI ━━━"
sudo apt-get update -qq
sudo apt-get install -y -qq apt-transport-https ca-certificates gnupg curl

# Google Cloud signing key
curl -sS https://packages.cloud.google.com/apt/doc/apt-key.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg

echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
  | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list >/dev/null

sudo apt-get update -qq
sudo apt-get install -y google-cloud-cli

echo
echo "━━━ 完成 ━━━"
gcloud --version | head -3
echo
echo "下一步請執行（互動式、會開瀏覽器登入）："
echo "   gcloud init"
echo "選項："
echo "  - 帳號：你的 Google Cloud 帳號"
echo "  - Project：選你 GCP 上的 project（或新建）"
echo "  - 預設 zone：選 asia-east1-b（台灣彰化）"
