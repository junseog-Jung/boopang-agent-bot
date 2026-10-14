#!/usr/bin/env bash
# 로컬 가상환경 생성 → 의존성 설치 → 테스트 → 서버 실행
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements-dev.txt
python -m pytest -q
exec uvicorn boopang.web.app:app --host 127.0.0.1 --port "${PORT:-8000}"
