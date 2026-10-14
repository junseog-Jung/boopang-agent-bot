# 생성된 코드의 pytest 를 실행하는 샌드박스 이미지 (BOOPANG_FACTORY_EXEC=docker).
# 실행 시 --network none 이므로 pip install 이 불가능하다 → 필요한 패키지를 미리 설치해 둔다.
# 패키지 목록은 boopang/factory/workspace_tools.py 의 SANDBOX_PACKAGES 와 맞춰 유지한다.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir pytest fastapi httpx uvicorn pydantic flask requests jinja2 sqlalchemy
WORKDIR /work
