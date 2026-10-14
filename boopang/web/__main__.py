"""python -m boopang.web  →  http://127.0.0.1:8899/ (다른 봇과의 포트 충돌 방지를 위한 기본 포트)"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "boopang.web.app:app",
        host=os.environ.get("BOOPANG_HOST", "127.0.0.1"),
        port=int(os.environ.get("BOOPANG_PORT", "8899")),
    )
