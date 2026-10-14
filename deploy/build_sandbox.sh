#!/usr/bin/env bash
# 샌드박스 이미지 빌드: deploy/build_sandbox.sh  (이미지 이름: boopang-sandbox)
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -f deploy/sandbox.Dockerfile -t "${BOOPANG_SANDBOX_IMAGE:-boopang-sandbox}" deploy
