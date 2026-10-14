"""FastAPI 웹 서비스 — 무상태(업로드 파일을 서버에 저장하지 않음)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .. import __version__
from ..config import Config
from ..exporter import default_schema, to_csv, to_json, to_preview, to_xlsx
from ..models import ParseError
from ..pipeline import process
from ..schema import SchemaError, load_schema
from ..sample import sample_xlsx
from .jobs import AGENTS, JobLimitError, JobManager, run_job

STATIC = Path(__file__).parent / "static"
ALLOWED_EXT = (".xlsx", ".csv")
FORMATS = {
    "csv": ("text/csv; charset=utf-8", "wms_lines.csv", to_csv),
    "json": ("application/json", "wms_orders.json", to_json),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "inspection_report.xlsx", to_xlsx),
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)

# 잘못된 환경변수/스키마 파일이면 기동 시점에 바로 실패시킨다 (fail-fast)
Config.from_env()
default_schema()

manager = JobManager()
_tasks: set = set()  # 실행 중인 백그라운드 태스크 참조 (GC 방지)

app = FastAPI(title="부팡 물류 및 주문 처리 자동화 봇", version=__version__, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


async def _read_upload(file: UploadFile, cfg: Config) -> tuple[bytes, str]:
    name = file.filename or ""
    if not name.lower().endswith(ALLOWED_EXT):
        raise HTTPException(415, "지원하지 않는 파일 형식입니다 (.xlsx, .csv 만 가능).")
    chunks, size = [], 0
    while chunk := await file.read(64 * 1024):
        size += len(chunk)
        if size > cfg.max_upload_bytes:
            raise HTTPException(413, f"파일이 너무 큽니다 (최대 {cfg.max_upload_bytes // (1024 * 1024)}MB).")
        chunks.append(chunk)
    return b"".join(chunks), Path(name).name


def _run(content: bytes, name: str, cfg: Config):
    try:
        return process(content, name, cfg)
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/")
def index():
    """멀티 에이전트 관제 대시보드."""
    return FileResponse(STATIC / "dashboard.html", media_type="text/html; charset=utf-8")


@app.get("/converter")
def converter():
    """즉시 변환기(표 형태 미리보기)."""
    return FileResponse(STATIC / "converter.html", media_type="text/html; charset=utf-8")


@app.get("/static/{name}")
def static_file(name: str):
    path = (STATIC / name).resolve()
    if path.parent != STATIC.resolve() or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)


@app.get("/api/sample")
def sample():
    return Response(
        sample_xlsx(),
        media_type=FORMATS["xlsx"][0],
        headers={"Content-Disposition": 'attachment; filename="sample_packing_list.xlsx"'},
    )


@app.post("/api/process")
async def api_process(file: UploadFile = File(...)):
    cfg = Config.from_env()
    content, name = await _read_upload(file, cfg)
    return JSONResponse(to_preview(_run(content, name, cfg)))


@app.post("/api/convert")
async def api_convert(file: UploadFile = File(...), format: str = Query("csv")):
    if format not in FORMATS:
        raise HTTPException(400, "format 은 csv, json, xlsx 중 하나여야 합니다.")
    cfg = Config.from_env()
    content, name = await _read_upload(file, cfg)
    media, out_name, fn = FORMATS[format]
    result = _run(content, name, cfg)
    try:
        body = fn(result, load_schema(cfg.schema_file or None))
    except SchemaError as exc:
        raise HTTPException(500, "서버의 WMS 스키마 설정이 올바르지 않습니다.") from exc
    except UnicodeEncodeError as exc:
        raise HTTPException(422, "스키마의 출력 인코딩으로 표현할 수 없는 문자가 포함되어 있습니다.") from exc
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{out_name}"'})


# ---------------------------------------------------------------- 관제 대시보드 API
FORMAT_NAMES = {"csv": "wms_lines.csv", "json": "wms_orders.json", "xlsx": "inspection_report.xlsx"}


@app.get("/api/agents")
def api_agents():
    return [{"key": k, "emoji": e, "name": n, "role": r} for k, e, n, r in AGENTS]


@app.post("/api/jobs", status_code=201)
async def api_create_job(file: UploadFile = File(...), instruction: str = Form("")):
    """파일을 받아 에이전트 파이프라인을 백그라운드로 시작한다. 진행 상황은 /events (SSE) 로 수신."""
    cfg = Config.from_env()
    content, name = await _read_upload(file, cfg)
    try:
        job = manager.create(instruction, name)
    except JobLimitError as exc:
        raise HTTPException(429, str(exc)) from exc
    task = asyncio.create_task(run_job(job, content, cfg))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job.id, "events_url": f"/api/jobs/{job.id}/events"}


def _job_or_404(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다 (만료되었거나 잘못된 ID).")
    return job


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    return _job_or_404(job_id).public()


@app.get("/api/jobs/{job_id}/events")
async def api_job_events(job_id: str, request: Request):
    """Server-Sent Events. Last-Event-ID 헤더로 끊긴 지점부터 재전송한다."""
    job = _job_or_404(job_id)
    try:
        idx = max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        idx = 0

    async def stream():
        nonlocal idx
        while True:
            async with job.cond:
                if idx >= len(job.events) and not job.done:
                    try:
                        await asyncio.wait_for(job.cond.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        pass
                batch = job.events[idx:]
                finished = job.done
            if batch:
                for ev in batch:
                    yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                idx += len(batch)
            elif not finished:
                yield ": keep-alive\n\n"
            if finished and idx >= len(job.events):
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/jobs/{job_id}/download/{fmt}")
def api_job_download(job_id: str, fmt: str):
    job = _job_or_404(job_id)
    if fmt not in FORMAT_NAMES:
        raise HTTPException(400, "fmt 는 csv, json, xlsx 중 하나여야 합니다.")
    if not job.done or job.error or fmt not in job.outputs:
        raise HTTPException(409, "산출물이 아직 준비되지 않았습니다.")
    return Response(job.outputs[fmt], media_type=FORMATS[fmt][0],
                    headers={"Content-Disposition": f'attachment; filename="{FORMAT_NAMES[fmt]}"'})
