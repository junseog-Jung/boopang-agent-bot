"""관제 대시보드용 잡 매니저 + 12개 에이전트 파이프라인 실행기.

각 에이전트 단계는 실제 처리 함수(parser/inspector/status/pricing/exporter)를 호출하고,
그 결과(행 수, 이슈 수, 합계 대조, 수식 인젝션 의심 건수 등)를 로그로 내보낸다.
단계 사이의 sleep(step_delay)은 화면 연출용이며 처리 결과에는 영향이 없다.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..config import Config
from ..exporter import safe_cell, to_csv, to_json, to_xlsx
from ..inspector import inspect_rows
from ..models import ParseError, Result
from ..parser import parse_file
from ..pricing import price_all
from ..schema import SchemaError, load_schema
from ..status import build_orders, classify_all

log = logging.getLogger("boopang.jobs")

MAX_LOOPS = 3
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# key, emoji, 이름, 역할
AGENTS = [
    ("ceo", "👑", "CEO", "지시 접수 · 마일스톤 · 최종 승인"),
    ("supervisor", "🧐", "Supervisor", "단계별 산출물 감시 · 합계 대조"),
    ("ideation", "💡", "Ideation", "파일 구조 분석 · 헤더 매핑"),
    ("architecture", "🏗️", "Architecture", "정책 설정 · WMS 스키마 확정"),
    ("uiux", "🎨", "UI/UX", "결과 화면 데이터 구성"),
    ("development", "💻", "Development", "정규화 · 상태 분류 · 단가 계산"),
    ("qa", "🔍", "QA / Review", "검수 이슈 집계"),
    ("refinement", "🔧", "Refinement", "오류 라인 수정 필요 항목 정리"),
    ("security", "🛡️", "Security", "수식 인젝션 · 입력 안전성 점검"),
    ("devops", "🚀", "DevOps", "WMS CSV/JSON/XLSX 산출물 생성"),
    ("docs", "📚", "Documentation", "처리 요약 리포트 작성"),
    ("loop", "💰", "Loop Controller", "재작업 루프 · 소요 시간 감시"),
]
AGENT_NAMES = {a[0]: a[2] for a in AGENTS}
# 소프트웨어 제작 모드에서의 역할 설명 (같은 12개 에이전트)
SOFTWARE_ROLES = {
    "ceo": "요구사항 접수 · 위험 요청 게이트 · 최종 승인",
    "supervisor": "PRD/설계 규칙 검토 · 단계별 승인",
    "ideation": "자연어 → PRD(요구사항 명세서) 작성",
    "architecture": "아키텍처 · 기술 스택 · 파일 구조 설계",
    "uiux": "화면/컴포넌트 구성 계획 정리",
    "development": "다중 파일 소스코드 생성",
    "qa": "문법·구조 정적 검사",
    "refinement": "검사 실패 코드 자가 치유(최대 3회)",
    "security": "인젝션·시크릿·위험 API 정적 스캔",
    "devops": ".gitignore 보강 · 프로젝트 ZIP 패키징",
    "docs": "README · 팩토리 리포트 작성",
    "loop": "루프 횟수 · LLM 호출/토큰 사용량 감시",
}
STATES = {"idle", "thinking", "coding", "reviewing", "done", "skipped", "error"}


class JobLimitError(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    instruction: str
    filename: str
    created: float = field(default_factory=time.time)
    events: list = field(default_factory=list)
    done: bool = False
    error: Optional[str] = None
    outputs: dict = field(default_factory=dict)   # fmt -> bytes
    summary: Optional[dict] = None
    kind: str = "logistics"                       # logistics | software
    project: Optional[object] = None              # factory.project.Project (software)
    software: Optional[dict] = None               # PRD/설계/검수 결과 요약 (software)
    loop_count: int = 0
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def emit(self, type_: str, **data) -> None:
        async with self.cond:
            self.events.append({"seq": len(self.events) + 1, "ts": time.time(), "type": type_, **data})
            self.cond.notify_all()

    async def finish(self) -> None:
        async with self.cond:
            self.done = True
            self.cond.notify_all()

    def public(self) -> dict:
        return {
            "job_id": self.id, "filename": self.filename, "done": self.done, "error": self.error,
            "event_count": len(self.events), "summary": self.summary, "kind": self.kind,
            "software": self.software,
            "downloads": sorted(self.outputs) if self.done and not self.error else [],
        }


class JobManager:
    def __init__(self, max_jobs: int = 50, ttl: float = 1800.0, max_running: int = 3):
        self.jobs: "OrderedDict[str, Job]" = OrderedDict()
        self.max_jobs, self.ttl, self.max_running = max_jobs, ttl, max_running

    def purge(self) -> None:
        now = time.time()
        for jid in [j for j, job in self.jobs.items() if job.done and now - job.created > self.ttl]:
            del self.jobs[jid]
        while len(self.jobs) > self.max_jobs:
            oldest = next(iter(self.jobs))
            if not self.jobs[oldest].done:
                break
            del self.jobs[oldest]

    def running(self) -> int:
        return sum(1 for j in self.jobs.values() if not j.done)

    def check_hourly(self, limit: int) -> None:
        """유료 LLM 작업의 시간당 생성 횟수 제한 (인증이 없으므로 비용 보호용)."""
        now = time.time()
        self.hourly = [t for t in getattr(self, "hourly", []) if now - t < 3600]
        if len(self.hourly) >= limit:
            raise JobLimitError(f"시간당 소프트웨어 제작 한도({limit}건)를 초과했습니다. 잠시 후 다시 시도하세요.")
        self.hourly.append(now)

    def create(self, instruction: str, filename: str, kind: str = "logistics", max_len: int = 1000) -> Job:
        self.purge()
        if self.running() >= self.max_running:
            raise JobLimitError("동시에 처리 중인 작업이 너무 많습니다. 잠시 후 다시 시도하세요.")
        job = Job(id=secrets.token_urlsafe(16), instruction=clean_instruction(instruction, max_len), filename=filename, kind=kind)
        self.jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        if not JOB_ID_RE.match(job_id or ""):
            return None
        return self.jobs.get(job_id)


def clean_instruction(text: str, max_len: int = 1000) -> str:
    return _CONTROL.sub("", (text or "").strip())[:max_len]


def _fmt(d) -> str:
    return f"{Decimal(d):,.0f}원"


class Runner:
    def __init__(self, job: Job, cfg: Config):
        self.job, self.cfg = job, cfg
        self.t0 = time.perf_counter()

    async def state(self, agent: str, state: str, task: str = "", **extra) -> None:
        assert agent in AGENT_NAMES and state in STATES
        await self.job.emit("agent", agent=agent, state=state, task=task, loop=self.job.loop_count, **extra)
        await self.pause()

    async def say(self, agent: str, message: str, level: str = "info") -> None:
        await self.job.emit("log", agent=agent, level=level, message=message)

    async def pause(self) -> None:
        if self.cfg.step_delay:
            await asyncio.sleep(self.cfg.step_delay)


async def run_job(job: Job, content: bytes, cfg: Config) -> None:
    r = Runner(job, cfg)
    stage = "ceo"
    try:
        await _pipeline(r, content)
    except ParseError as exc:
        await fail_job(r, "ideation", f"파일을 해석할 수 없습니다: {exc}")
    except SchemaError as exc:
        await fail_job(r, "architecture", f"WMS 스키마 설정 오류: {exc}")
    except UnicodeEncodeError:
        await fail_job(r, "devops", "스키마의 출력 인코딩으로 표현할 수 없는 문자가 포함되어 있습니다.")
    except Exception:  # 내부 예외 상세는 서버 로그에만 남기고 화면에는 노출하지 않는다
        log.exception("job %s failed", job.id)
        await fail_job(r, stage, "처리 중 내부 오류가 발생했습니다. 서버 로그를 확인하세요.")
    finally:
        await job.finish()


async def fail_job(r: Runner, agent: str, message: str) -> None:
    r.job.error = message
    await r.say(agent, message, "error")
    await r.state(agent, "error", message)
    await r.say("supervisor", "산출물을 반려했습니다. 원본 파일/설정을 수정한 뒤 다시 업로드하세요.", "error")
    await r.job.emit("error", message=message)


async def _pipeline(r: Runner, content: bytes) -> None:
    job, cfg = r.job, r.cfg

    # 1. CEO — 지시 접수
    await r.state("ceo", "thinking", "지시 접수 및 마일스톤 수립")
    await r.say("ceo", f"파일 접수: {job.filename} ({len(content):,} bytes)")
    await r.say("ceo", f"업무 지시: {job.instruction or '(입력 없음 — 기본 처리: 검수 → 단가 → 상태 분류 → WMS 변환)'}")
    await r.say("ceo", "마일스톤 M1 구조 분석·설정 → M2 처리 → M3 검수·보안 → M4 산출물·보고")
    await r.state("ceo", "reviewing", "단계 진행 모니터링")

    # 2. Ideation — 파일 파싱
    await r.state("ideation", "thinking", "파일 구조 분석 중")
    rows = await asyncio.to_thread(parse_file, content, job.filename, cfg)
    recognized = list(rows[0].data.keys())
    await r.say("ideation", f"헤더 매핑 완료: {len(recognized)}개 컬럼 인식 ({', '.join(recognized)})")
    await r.say("ideation", f"데이터 행 {len(rows):,}건 확보")
    await r.state("ideation", "done", f"{len(rows):,}행 · {len(recognized)}개 컬럼 인식")

    # 3. Architecture — 정책/스키마
    await r.state("architecture", "thinking", "정책 설정 및 WMS 스키마 확정")
    schema = await asyncio.to_thread(load_schema, cfg.schema_file or None)
    vat = "VAT 미적용" if not cfg.vat_enabled else f"VAT {cfg.vat_rate * 100:.0f}% " + ("포함가" if cfg.price_includes_vat else "별도")
    await r.say("architecture", f"정책: {vat} · 청구 수량 기준 {cfg.bill_basis} · "
                                f"무료배송 {_fmt(cfg.free_shipping_threshold)} 이상 · 배송비 {_fmt(cfg.shipping_fee)}")
    await r.say("architecture", f"WMS 스키마 v{schema.version}: 라인 {len(schema.line_columns)}컬럼 / 주문 {len(schema.order_columns)}컬럼 "
                                f"/ {schema.encoding} / '{schema.delimiter}' 구분")
    await r.state("architecture", "done", f"스키마 v{schema.version} 확정")

    await r.state("supervisor", "reviewing", "Phase 1 산출물 검토")
    await r.say("supervisor", "Phase 1 승인: 필수 컬럼(주문번호·SKU·주문수량·단가) 확인, 설정/스키마 유효")
    await r.state("supervisor", "reviewing", "후속 단계 감시")

    # 4. UI/UX — 결과 화면 구성
    await r.state("uiux", "thinking", "결과 화면 구성")
    await r.say("uiux", "요약 카드(라인/주문/오류/경고/금액)와 다운로드 버튼 구성")
    await r.state("uiux", "done", "결과 화면 준비")

    # 5. Development — 실제 처리
    await r.state("development", "coding", "정규화 · 검수 · 상태 분류")
    lines, issues = await asyncio.to_thread(inspect_rows, rows)
    await r.say("development", f"라인 {len(lines):,}건 정규화 완료 (숫자·날짜·연락처 변환)")
    await asyncio.to_thread(classify_all, lines, issues)
    orders = build_orders(lines)
    await r.say("development", f"배송 상태 분류 완료 → 주문 {len(orders):,}건으로 롤업")
    await r.state("development", "coding", "단가 계산")
    price_all(lines, orders, cfg)
    issues.sort(key=lambda i: (i.row_no, i.severity.value))
    result = Result(source_name=job.filename, lines=lines, orders=orders, issues=issues)
    s = result.summary()
    await r.say("development", f"금액 계산 완료: 공급가 {_fmt(s['supply_amt'])} · 부가세 {_fmt(s['vat_amt'])} · "
                               f"배송비 {_fmt(s['shipping_fee'])} · 합계 {_fmt(s['total_amt'])}")
    await r.state("development", "done", "처리 완료")

    # 6. QA — 검수 집계 + Supervisor 합계 대조
    await r.state("qa", "reviewing", "검수 이슈 집계")
    sev = s["issues"]
    await r.say("qa", f"이슈: ERROR {sev['ERROR']} · WARN {sev['WARN']} · INFO {sev['INFO']}",
                "error" if sev["ERROR"] else ("warn" if sev["WARN"] else "info"))
    for iss in [i for i in issues if i.severity.value == "ERROR"][:5]:
        await r.say("qa", f"행 {iss.row_no} [{iss.code}] {iss.message}", "error")
    for iss in [i for i in issues if i.severity.value == "WARN"][:3]:
        await r.say("qa", f"행 {iss.row_no} [{iss.code}] {iss.message}", "warn")
    await r.state("qa", "done", f"오류 {sev['ERROR']} · 경고 {sev['WARN']}")

    await r.state("supervisor", "reviewing", "금액 합계 대조")
    line_total = sum((l.total_amt for l in lines if l.billable), Decimal("0"))
    order_total = sum((o.total_amt - o.shipping_fee for o in orders), Decimal("0"))
    if line_total != order_total:
        raise RuntimeError(f"합계 불일치: 라인 {line_total} / 주문 {order_total}")
    await r.say("supervisor", f"합계 대조 통과: 라인 합계 {_fmt(line_total)} = 주문 합계(배송비 제외) {_fmt(order_total)}")

    # 7. Refinement
    errored = [l for l in lines if l.has_error]
    if errored:
        await r.state("refinement", "coding", "수정 필요 항목 정리")
        await r.say("refinement", f"오류 라인 {len(errored)}건은 원본 값이 잘못되어 자동 수정하지 않고 HOLD 처리했습니다. "
                                  "원본 수정 후 재업로드가 필요합니다.", "warn")
        await r.state("refinement", "done", f"수동 수정 필요 {len(errored)}건")
    else:
        await r.say("refinement", "수정이 필요한 오류 라인이 없어 재작업을 생략합니다.")
        await r.state("refinement", "skipped", "수정 대상 없음")

    # 8. Security
    await r.state("security", "reviewing", "수식 인젝션 · 입력 점검")
    suspicious = sum(
        1 for l in lines for v in (l.order_id, l.sku, l.name, l.recipient, l.address, l.tracking_no, l.memo)
        if safe_cell(v) != v
    )
    if suspicious:
        await r.say("security", f"수식 인젝션 의심 값 {suspicious}건 발견 — 출력 시 ' 접두 처리로 무력화합니다.", "warn")
    else:
        await r.say("security", "수식 인젝션 의심 값 없음")
    await r.say("security", f"입력 한도 통과 (최대 {cfg.max_upload_bytes // (1024 * 1024)}MB / {cfg.max_rows:,}행), 결과는 메모리에만 임시 보관")
    await r.state("security", "done", f"의심 {suspicious}건 무력화")

    # 9. DevOps — 산출물 생성
    await r.state("devops", "coding", "WMS CSV 생성")
    job.outputs["csv"] = await asyncio.to_thread(to_csv, result, schema)
    await r.say("devops", f"wms_lines.csv {len(job.outputs['csv']):,} bytes ({schema.encoding})")
    await r.state("devops", "coding", "JSON 생성")
    job.outputs["json"] = await asyncio.to_thread(to_json, result, schema)
    await r.say("devops", f"wms_orders.json {len(job.outputs['json']):,} bytes")
    await r.state("devops", "coding", "XLSX 리포트 생성")
    job.outputs["xlsx"] = await asyncio.to_thread(to_xlsx, result, schema)
    await r.say("devops", f"inspection_report.xlsx {len(job.outputs['xlsx']):,} bytes")
    await r.state("devops", "done", "산출물 3종 준비")

    # 10. Documentation
    await r.state("docs", "coding", "처리 요약 작성")
    statuses = ", ".join(f"{k} {v}" for k, v in sorted(s["order_status"].items()))
    await r.say("docs", f"주문 상태 분포: {statuses}")
    job.summary = {**s, "instruction": job.instruction}
    await r.state("docs", "done", "요약 완료")

    # 11. Loop Controller
    await r.state("loop", "reviewing", "루프·소요 시간 점검")
    elapsed = time.perf_counter() - r.t0
    await r.say("loop", f"재작업 루프 {job.loop_count}/{MAX_LOOPS} — 에스컬레이션 없음 · 총 소요 {elapsed:.2f}s")
    await r.state("loop", "done", f"루프 {job.loop_count}/{MAX_LOOPS}")

    # 12. CEO 최종 승인
    await r.state("supervisor", "done", "전 단계 승인")
    note = f"오류 {sev['ERROR']}건은 HOLD 상태로 포함됨" if sev["ERROR"] else "오류 없음"
    await r.say("ceo", f"최종 승인 — 주문 {len(orders):,}건 / 라인 {len(lines):,}건 변환 완료 ({note})")
    await r.state("ceo", "done", "최종 승인")
    await job.emit("done", summary=job.summary, downloads=sorted(job.outputs))
