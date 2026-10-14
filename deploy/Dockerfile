FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY boopang ./boopang
RUN useradd --system --no-create-home --shell /usr/sbin/nologin app
USER app

EXPOSE 8000
# 앱이 요청을 처리할 수 있는지(HTTP 200) 확인한다. 실패 시 exit 1
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# 로그는 stdout 으로 출력된다 → `docker compose logs -f app`
CMD ["uvicorn", "boopang.web.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
