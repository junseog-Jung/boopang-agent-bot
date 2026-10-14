"""소프트웨어 제작 파이프라인 (12 에이전트 역할). 생성 코드는 실행하지 않고 정적 점검만 한다."""
from __future__ import annotations

import hashlib
import logging
import re
import time

from ..config import Config
from ..web.jobs import MAX_LOOPS, Job, Runner, fail_job
from . import prompts as P
from .checks import ERROR, INFO, WARN, Finding, qa_check, security_scan
from .llm import BaseLLM, LLMBudgetError, LLMError
from .mcp_workspace import McpToolError, McpWorkspace
from .project import Project, ProjectError, slugify, validate_path
from .workspace_tools import SANDBOX_PACKAGES, exec_readiness

log = logging.getLogger("boopang.factory")
MIN_REQUEST = 10

GITIGNORE_PY = "__pycache__/\n*.pyc\n.venv/\nvenv/\n.env\n*.db\n.pytest_cache/\n"
GITIGNORE_NODE = "node_modules/\n.env\ndist/\nbuild/\n"


def _s(v) -> str:
    return v if isinstance(v, str) else ""


def validate_prd(raw: dict) -> dict:
    prd = {k: raw.get(k) for k in P.PRD_SCHEMA["properties"]}
    prd["title"] = _s(prd["title"]).strip()[:80] or "소프트웨어 프로젝트"
    prd["slug"] = slugify(_s(prd["slug"]) or prd["title"])
    prd["summary"] = _s(prd["summary"]).strip()[:600]
    for k in ("goals", "nonfunctional", "out_of_scope", "risks", "assumptions"):
        prd[k] = [_s(x)[:300] for x in (prd[k] or []) if isinstance(x, str)][:12]
    prd["features"] = [f for f in (prd["features"] or []) if isinstance(f, dict)][:15]
    ts = prd["tech_stack"] if isinstance(prd["tech_stack"], dict) else {}
    prd["tech_stack"] = {k: _s(ts.get(k))[:100] for k in ("frontend", "backend", "database", "other")}
    prd["refused"] = bool(prd["refused"])
    prd["refusal_reason"] = _s(prd["refusal_reason"])[:300]
    return prd


def validate_arch(raw: dict, cfg: Config) -> dict:
    arch = {k: raw.get(k) for k in P.ARCH_SCHEMA["properties"]}
    arch["overview"] = _s(arch["overview"])[:800]
    for k in ("components", "data_model", "api", "ui_plan"):
        arch[k] = [x for x in (arch[k] or []) if isinstance(x, dict)][:30]
    for k in ("run_commands", "test_commands"):
        arch[k] = [_s(x)[:200] for x in (arch[k] or []) if isinstance(x, str)][:8]
    files, seen = [], set()
    for f in arch["files"] or []:
        path = _s(f.get("path")) if isinstance(f, dict) else ""
        validate_path(path)  # ProjectError
        if path.lower() == "readme.md":
            continue  # README 는 Documentation 단계에서 작성
        if path in seen:
            raise ProjectError(f"설계에 중복된 파일이 있습니다: {path}")
        seen.add(path)
        files.append({"path": path, "purpose": _s(f.get("purpose"))[:200]})
    if not files:
        raise ProjectError("설계에 생성할 파일이 없습니다")
    if len(files) > cfg.factory_max_files:
        raise ProjectError(f"설계의 파일 수({len(files)})가 한도({cfg.factory_max_files})를 초과했습니다")
    arch["files"] = files
    return arch


def _counts(findings) -> dict:
    return {s: sum(1 for f in findings if f.severity == s) for s in (ERROR, WARN, INFO)}


def _errors(findings) -> list:
    return [f for f in findings if f.severity == ERROR]


def fallback_readme(prd: dict, arch: dict, files: dict) -> str:
    cmds = lambda xs: "\n".join(xs) or "(설계에 명시되지 않음)"
    return (f"# {prd['title']}\n\n{prd['summary']}\n\n## 파일 구조\n" + "\n".join(f"- `{p}`" for p in sorted(files)) +
            f"\n\n## 실행\n```bash\n{cmds(arch['run_commands'])}\n```\n\n## 테스트\n```bash\n{cmds(arch['test_commands'])}\n```\n")


def build_report(job: Job, prd: dict, arch: dict, findings: list, loops: int, escalated: bool, llm: BaseLLM, project: Project,
                 exec_info: dict | None = None, mcp_calls: int = 0) -> str:
    c = _counts(findings)
    mode = "**데모 모드(요청과 무관한 고정 샘플)**" if llm.name == "demo" else f"LLM 생성 ({llm.name}: {getattr(llm, 'model', '')})"
    ex = exec_info or {}
    if ex.get("ran"):
        run_line = f"- 테스트 실행({ex['mode']} 모드): {'통과' if ex.get('passed') else '**실패**'} — {ex.get('summary', '')}"
    else:
        run_line = "- 테스트 실행: 하지 않음 (BOOPANG_FACTORY_EXEC=off — 정적 점검만 수행)"
    lines = [
        f"# 팩토리 리포트 — {prd['title']}", "",
        f"- 생성 방식: {mode}", f"- 파일 {len(project.files)}개 / {project.total_bytes():,} bytes",
        f"- 자가 치유 루프: {loops}/{MAX_LOOPS}" + (" — **한도 내 해결 실패, 사람의 검토 필요**" if escalated else ""),
        f"- 정적 점검 결과: ERROR {c[ERROR]} · WARN {c[WARN]} · INFO {c[INFO]}", run_line,
        f"- MCP 도구 호출 {mcp_calls}회" if mcp_calls else "- MCP: 사용하지 않음(메모리 모드)",
        f"- LLM 호출 {llm.calls}회 · 입력 {llm.input_tokens:,} / 출력 {llm.output_tokens:,} 토큰", "",
        ("> ℹ️ 생성된 테스트가 격리 환경에서 실행되었지만, 이는 품질 보증이 아닙니다. 사용 전에 직접 검토하세요." if ex.get("ran") else
         "> ⚠️ 이 코드는 **실행·테스트되지 않았습니다.** 문법 검사와 정적 보안 스캔만 통과한 초안이므로, 사용 전에 직접 검토하고 테스트하세요."), "",
        "## 요청 (원문)", "", "```text", job.instruction, "```", "", "## PRD", "", prd["summary"], "",
        "### 기능", *[f"- ({f.get('priority', '')}) **{f.get('name', '')}** — {f.get('description', '')}" for f in prd["features"]], "",
        "### 위험/가정", *[f"- 위험: {x}" for x in prd["risks"]], *[f"- 가정: {x}" for x in prd["assumptions"]], "",
        "## 아키텍처", "", arch["overview"], "",
        "## 남은 점검 항목", *([f"- [{f.severity}] {f.label()}" for f in findings] or ["- 없음"]), "",
    ]
    return "\n".join(lines)


async def run_software_job(job: Job, cfg: Config, llm: BaseLLM) -> None:
    r = Runner(job, cfg)
    ws, ws_error = None, ""
    try:
        if cfg.factory_mcp:
            try:
                ws = await McpWorkspace(cfg).__aenter__()
            except Exception as exc:  # mcp 미설치/서버 기동 실패 → 메모리 모드로 계속 (경고 표시)
                log.warning("MCP workspace unavailable: %s", type(exc).__name__)
                ws, ws_error = None, "MCP 서버를 시작하지 못했습니다"
        await _pipeline(r, llm, ws, ws_error)
    except LLMError as exc:
        await fail_job(r, _failing_agent(job), str(exc))
    except (ProjectError, McpToolError) as exc:
        await fail_job(r, "supervisor", f"산출물이 안전/규격 검증을 통과하지 못했습니다: {exc}")
    except Exception:
        log.exception("software job %s failed", job.id)
        await fail_job(r, "ceo", "처리 중 내부 오류가 발생했습니다. 서버 로그를 확인하세요.")
    finally:
        if ws is not None:
            await ws.aclose()  # 작업 디렉터리 삭제 포함
        await job.finish()


def _failing_agent(job: Job) -> str:
    for ev in reversed(job.events):
        if ev["type"] == "agent" and ev["state"] not in ("done", "skipped", "idle", "error"):
            return ev["agent"]
    return "ceo"


def exec_note(cfg: Config) -> str:
    if cfg.factory_exec == "off":
        return ""
    return ("The generated pytest tests WILL be executed offline in a sandbox (no network, stdlib + these packages only: "
            f"{', '.join(SANDBOX_PACKAGES)}). Do not import anything else and never use the network in tests.")


_PATH_RE = re.compile(r"([A-Za-z0-9_./\-]+\.py)")
_TEST_RE = re.compile(r"(^|/)test_[^/]*\.py$|_test\.py$")


def _paths_in(output: str, project: Project, root: str) -> list:
    seen = []
    for m in _PATH_RE.findall(output):
        m = m.replace(root + "/", "").replace("/work/", "").lstrip("./")
        if m in project.files and m not in seen:
            seen.append(m)
    return seen


async def _pipeline(r: Runner, llm: BaseLLM, ws, ws_error: str = "") -> None:
    job, cfg = r.job, r.cfg
    t0 = time.perf_counter()
    demo = llm.name == "demo"
    exec_info = {"mode": cfg.factory_exec, "ran": False, "passed": None, "summary": "", "runs": 0}

    # 1. CEO
    await r.state("ceo", "thinking", "요구사항 접수")
    await r.say("ceo", f"요구사항 접수 ({len(job.instruction)}자): {job.instruction[:120]}{'…' if len(job.instruction) > 120 else ''}")
    if demo:
        await r.say("ceo", "⚠ 데모 모드: 요청 내용과 무관하게 고정 샘플('할 일 관리')을 생성합니다. 실제 범용 생성은 ANTHROPIC_API_KEY 설정이 필요합니다.", "warn")
    if len(job.instruction) < MIN_REQUEST:
        raise LLMError(f"요구사항이 너무 짧습니다 (최소 {MIN_REQUEST}자). 만들고 싶은 소프트웨어를 구체적으로 적어 주세요.")
    if ws is not None:
        await r.say("ceo", f"MCP 서버 연결 — 도구: {', '.join(ws.tools)} · 작업 디렉터리 격리(작업 종료 시 삭제)")
    else:
        await r.say("ceo", (ws_error + " — " if ws_error else "MCP 비활성 — ") + "메모리 모드로 진행합니다", "warn" if ws_error else "info")
    if cfg.factory_exec == "local":
        await r.say("ceo", "⚠ 테스트 실행 모드 local: 생성된 코드를 이 서버에서 격리 없이 실행합니다. 신뢰할 수 있는 로컬 개발 환경에서만 사용하세요.", "warn")
    elif cfg.factory_exec == "docker":
        await r.say("ceo", "테스트는 네트워크 차단 Docker 샌드박스에서 실행됩니다")
    elif cfg.factory_exec == "modal":
        await r.say("ceo", "테스트는 Modal 클라우드 샌드박스(네트워크 차단·호스트 환경변수/시크릿 미전달)에서 실행됩니다. Modal 사용량 요금이 발생할 수 있습니다.")
    if cfg.factory_exec in ("docker", "modal"):
        ok, hint = exec_readiness(cfg)
        if not ok:
            await r.say("ceo", f"테스트 실행 환경이 준비되지 않았습니다: {hint}", "warn")
    await r.state("ceo", "reviewing", "단계 진행 모니터링")

    # 2. Ideation — PRD
    await r.state("ideation", "thinking", "자연어 요구사항 분석 → PRD 작성")
    prd = validate_prd(await llm.json(P.PRD_SYSTEM, P.prd_user(job.instruction), P.PRD_SCHEMA, label="prd"))
    if prd["refused"]:
        await r.say("ceo", f"요청을 진행할 수 없습니다: {prd['refusal_reason'] or '안전 정책에 위배되는 요청'}", "error")
        raise LLMError(f"안전 정책상 제작할 수 없는 요청입니다: {prd['refusal_reason'] or '사유 없음'}")
    stack = ", ".join(v for v in prd["tech_stack"].values() if v)
    await r.say("ideation", f"PRD 완성: '{prd['title']}' — 기능 {len(prd['features'])}개, 스택 {stack or '미정'}")
    for f in prd["features"][:6]:
        await r.say("ideation", f"[{f.get('priority', '')}] {f.get('name', '')}")
    for x in prd["risks"][:3]:
        await r.say("ideation", f"위험 요소: {x}", "warn")
    await r.state("ideation", "done", f"기능 {len(prd['features'])}개 정의")

    # 3. Architecture
    await r.state("architecture", "thinking", "아키텍처 · 파일 구조 설계")
    arch = validate_arch(await llm.json(P.ARCH_SYSTEM, P.arch_user(prd, cfg.factory_max_files, exec_note(cfg)), P.ARCH_SCHEMA, label="arch"), cfg)
    need = len(arch["files"]) + 2 + 1 + MAX_LOOPS  # 파일 + (prd, arch) + readme + 리파인
    if need > cfg.factory_max_calls:
        raise LLMBudgetError(f"설계가 LLM 호출 한도({cfg.factory_max_calls}회)보다 큽니다 (필요 최대 {need}회). BOOPANG_FACTORY_MAX_CALLS 를 늘리거나 범위를 줄이세요.")
    await r.say("architecture", f"설계 완료: 컴포넌트 {len(arch['components'])}개, API {len(arch['api'])}개, 파일 {len(arch['files'])}개")
    await r.say("architecture", "구조: " + ", ".join(f['path'] for f in arch["files"]))
    await r.state("architecture", "done", f"파일 {len(arch['files'])}개 설계")

    await r.state("supervisor", "reviewing", "PRD · 설계 규칙 검토")
    await r.say("supervisor", f"승인: 파일 {len(arch['files'])}/{cfg.factory_max_files}개, 경로 검증 통과, 의존성·테스트 파일 계획 확인")
    if not any("test" in f["path"].lower() for f in arch["files"]):
        await r.say("supervisor", "경고: 설계에 테스트 파일이 없습니다", "warn")

    # 4. UI/UX
    if arch["ui_plan"]:
        await r.state("uiux", "thinking", "화면 구성 정리")
        for pg in arch["ui_plan"][:5]:
            await r.say("uiux", f"화면 {pg.get('page', '')}: {pg.get('description', '')}")
        await r.state("uiux", "done", f"화면 {len(arch['ui_plan'])}개")
    else:
        await r.say("uiux", "UI 가 없는 프로젝트라 건너뜁니다")
        await r.state("uiux", "skipped", "UI 없음")

    # 5. Development
    project = Project(cfg.factory_max_files + 3, cfg.factory_max_file_bytes, cfg.factory_max_total_bytes)

    async def put(path: str, content) -> int:
        """검증(Project) 후 MCP write_project_file 로 디스크(작업 디렉터리)에 기록한다."""
        project.set(path, content)
        if ws is not None:
            await ws.write(path, project.files[path])
        return len(project.files[path].encode())

    via = " → MCP write_project_file" if ws is not None else ""
    n = len(arch["files"])
    for i, f in enumerate(arch["files"], 1):
        await r.state("development", "coding", f"파일 {i}/{n}: {f['path']}")
        out = await llm.json(P.DEV_SYSTEM, P.dev_user(prd, arch, f["path"], f["purpose"], project.files, exec_note(cfg)), P.FILE_SCHEMA, label=f"file:{f['path']}")
        size = await put(f["path"], out.get("content"))
        await r.say("development", f"{f['path']} 작성 ({size:,} bytes){via}")
    await r.state("development", "done", f"{n}개 파일 생성")

    if ws is not None:  # 메모리의 프로젝트와 MCP 워크스페이스(디스크)가 같은지 해시로 대조
        await r.state("supervisor", "reviewing", "MCP 디스크 대조")
        disk = {f["path"]: f["sha256"] for f in await ws.list()}
        mem = {p: hashlib.sha256(c.encode("utf-8")).hexdigest() for p, c in project.files.items()}
        if disk != mem:
            raise ProjectError("MCP 워크스페이스(디스크)와 생성 결과가 일치하지 않습니다")
        await r.say("supervisor", f"디스크 대조 통과: list_project_files {len(disk)}개 파일 해시 일치")

    # 6~8. QA / Security / Refinement 루프
    findings = await _check(r, project, ws, exec_info, cfg, first=True)
    loops, escalated = 0, False
    if _errors(findings):
        while _errors(findings) and loops < MAX_LOOPS:
            loops += 1
            job.loop_count = loops
            errs = _errors(findings)
            affected = sorted({p for f in errs for p in (f.path, *f.paths) if p in project.files})
            if not affected and any(f.code.startswith("TEST_") for f in errs):  # 원인 파일을 못 찾으면 소스 파일 일부를 후보로
                affected = [p for p in project.files if p.endswith(".py") and not _TEST_RE.search(p)][:6]
            if not affected:
                break
            await r.state("refinement", "coding", f"자가 치유 {loops}/{MAX_LOOPS}: {len(errs)}건 수정")
            await r.say("loop", f"재작업 루프 {loops}/{MAX_LOOPS} 시작 (LLM 호출 {llm.calls}/{llm.max_calls})", "warn")
            try:
                fix = await llm.json(P.REFINE_SYSTEM, P.refine_user(errs, project.files, affected), P.REFINE_SCHEMA, label=f"refine:{loops}")
            except LLMBudgetError:
                await r.say("loop", "LLM 호출 한도에 도달해 자가 치유를 중단합니다", "error")
                break
            changed = 0
            for item in fix.get("files") or []:
                path = item.get("path") if isinstance(item, dict) else None
                if path in affected:
                    await put(path, item.get("content"))
                    changed += 1
                else:
                    await r.say("refinement", f"허용되지 않은 파일 수정 시도를 무시했습니다: {str(path)[:60]}", "warn")
            await r.say("refinement", f"{changed}개 파일 수정: {_s(fix.get('notes'))[:160]}")
            await r.state("refinement", "reviewing", f"재검사 {loops}/{MAX_LOOPS}")
            findings = await _check(r, project, ws, exec_info, cfg)
        escalated = bool(_errors(findings))
        if escalated:
            await r.state("refinement", "error", f"{loops}회 수정 후에도 오류 {len(_errors(findings))}건")
            await r.say("loop", f"루프 한도({MAX_LOOPS}회) 내 해결 실패 → 사람에게 에스컬레이션", "error")
        else:
            await r.say("refinement", f"모든 ERROR 해결 ({loops}회 만에)")
            await r.state("refinement", "done", f"{loops}회 만에 해결")
    else:
        await r.say("refinement", "수정이 필요한 오류가 없어 생략합니다")
        await r.state("refinement", "skipped", "수정 대상 없음")

    # 10. Documentation
    await r.state("docs", "coding", "README · 리포트 작성")
    if "README.md" not in project.files:
        try:
            doc = await llm.json(P.README_SYSTEM, P.readme_user(prd, arch, project.files), P.FILE_SCHEMA, label="readme")
            readme = doc.get("content")
        except LLMBudgetError:
            readme = None
        if not isinstance(readme, str) or not readme.strip():
            await r.say("docs", "LLM 으로 README 를 만들 수 없어 기본 템플릿을 사용합니다", "warn")
            readme = fallback_readme(prd, arch, project.files)
        await put("README.md", readme)
    await r.say("docs", "README.md 작성")
    await r.state("docs", "coding", "팩토리 리포트 작성")

    # 9. DevOps — 패키징
    await r.state("devops", "coding", ".gitignore 보강 · ZIP 패키징")
    if ".gitignore" not in project.files:
        node = "package.json" in project.files
        await put(".gitignore", GITIGNORE_NODE if node else GITIGNORE_PY)
        await r.say("devops", ".gitignore 추가")
    await put("FACTORY_REPORT.md", build_report(job, prd, arch, findings, loops, escalated, llm, project, exec_info, ws.calls if ws else 0))
    await r.say("docs", "FACTORY_REPORT.md 작성 (요청·PRD·점검 결과·한계 명시)")
    await r.state("docs", "done", "문서 완료")
    zip_bytes = project.to_zip(prd["slug"])
    job.outputs["zip"] = zip_bytes
    job.project = project
    await r.say("devops", f"{prd['slug']}.zip 생성: 파일 {len(project.files)}개, {len(zip_bytes):,} bytes (GitHub 레포지토리 구조: {prd['slug']}/…)")
    await r.state("devops", "done", "ZIP 패키징 완료")

    # 11. Loop Controller
    await r.state("loop", "reviewing", "루프 · 호출량 점검")
    elapsed = time.perf_counter() - t0
    await r.say("loop", f"재작업 루프 {loops}/{MAX_LOOPS} · MCP 호출 {ws.calls if ws else 0}회 · LLM 호출 {llm.calls}회 · 토큰 입력 {llm.input_tokens:,}/출력 {llm.output_tokens:,} · 소요 {elapsed:.1f}s")
    await r.state("loop", "done", f"루프 {loops}/{MAX_LOOPS} · 호출 {llm.calls}회")

    # 12. 최종
    c = _counts(findings)
    job.software = {
        "title": prd["title"], "slug": prd["slug"], "summary": prd["summary"], "mode": llm.name, "demo": demo,
        "prd": prd, "architecture": arch, "files": project.tree(), "loops": loops, "escalated": escalated,
        "findings": c, "remaining": [{"path": f.path, "line": f.line, "severity": f.severity, "code": f.code, "message": f.message}
                                     for f in findings if f.severity != INFO][:40],
        "llm_calls": llm.calls, "input_tokens": llm.input_tokens, "output_tokens": llm.output_tokens,
        "provider": llm.name, "model": getattr(llm, "model", None),
        "mcp": {"enabled": ws is not None, "calls": ws.calls if ws else 0, "tools": ws.tools if ws else []},
        "exec": exec_info,
    }
    await r.state("supervisor", "done", "전 단계 승인" if not escalated else "승인 보류(사람 검토 필요)")
    note = "사람 검토 필요(자가 치유 실패)" if escalated else "정적 점검 통과 — 실행/테스트는 직접 확인하세요"
    await r.say("ceo", f"최종 보고 — '{prd['title']}' 파일 {len(project.files)}개 ({note})", "warn" if escalated else "info")
    await r.state("ceo", "done", "최종 보고")
    await job.emit("done", software=job.software, downloads=["zip"], summary=None)


async def _check(r: Runner, project: Project, ws, exec_info: dict, cfg: Config, first: bool = False) -> list:
    """QA(문법 + 선택적 테스트 실행) + Security(스캔) 실행 후 에이전트 상태/로그 갱신."""
    import asyncio

    await r.state("qa", "reviewing", "문법·구조 정적 검사")
    qa = await asyncio.to_thread(qa_check, project.files)
    c = _counts(qa)
    await r.say("qa", f"검사 {len(project.files)}개 파일: ERROR {c[ERROR]} · WARN {c[WARN]}", "error" if c[ERROR] else ("warn" if c[WARN] else "info"))
    for f in [x for x in qa if x.severity == ERROR][:5] + [x for x in qa if x.severity == WARN][:3]:
        await r.say("qa", f.label(), "error" if f.severity == ERROR else "warn")
    tests = []
    if ws is not None and cfg.factory_exec != "off":
        if c[ERROR]:
            await r.say("qa", "문법 오류가 있어 테스트 실행은 수정 후로 미룹니다", "warn")
        else:
            tests = await _run_tests(r, project, ws, exec_info)
    elif first and cfg.factory_exec != "off" and ws is None:
        await r.say("qa", "MCP 가 없어 테스트를 실행하지 않습니다 (메모리 모드)", "warn")
    await r.state("qa", "done", f"ERROR {c[ERROR] + _counts(tests)[ERROR]} · WARN {c[WARN]}")

    await r.state("security", "reviewing", "정적 보안 스캔")
    sec = await asyncio.to_thread(security_scan, project.files)
    c = _counts(sec)
    await r.say("security", f"보안 스캔: ERROR {c[ERROR]} · WARN {c[WARN]}", "error" if c[ERROR] else ("warn" if c[WARN] else "info"))
    for f in [x for x in sec if x.severity == ERROR][:6] + [x for x in sec if x.severity == WARN][:3]:
        await r.say("security", f.label(), "error" if f.severity == ERROR else "warn")
    if not sec:
        await r.say("security", "위험 패턴 없음 (SQL 인젝션·XSS·시크릿·위험 API)")
    await r.state("security", "done", f"ERROR {c[ERROR]} · WARN {c[WARN]}")
    return qa + tests + sec


async def _run_tests(r: Runner, project: Project, ws, exec_info: dict) -> list:
    """MCP run_command('pytest -q') 로 생성된 테스트를 실행하고 결과를 발견 사항(Finding)으로 바꾼다."""
    test_files = [p for p in project.files if _TEST_RE.search(p)]
    if not test_files:
        await r.say("qa", "테스트 파일이 없어 pytest 를 실행하지 않습니다", "warn")
        return [Finding("(project)", 0, WARN, "NO_TESTS", "자동화 테스트가 없습니다", "qa")]
    await r.state("qa", "reviewing", "pytest 실행 (MCP run_command)")
    try:
        res = await ws.run("pytest -q")
    except McpToolError as exc:  # 실행 환경 문제(자격 증명/이미지 등)는 코드 수정으로 해결할 수 없으므로 경고로 처리
        msg = str(exc).split(": ", 1)[-1][:200]
        exec_info["summary"] = f"실행 불가: {msg}"
        await r.say("qa", f"테스트를 실행하지 못했습니다 — {msg}", "error")
        return [Finding("(project)", 0, WARN, "TEST_EXEC_UNAVAILABLE", f"테스트 실행 환경 문제: {msg}", "qa")]
    exec_info["runs"] += 1
    if res.get("skipped"):
        await r.say("qa", res.get("stderr") or "테스트 실행이 건너뛰어졌습니다")
        return []
    exec_info["ran"] = True
    out = ((res.get("stdout") or "") + "\n" + (res.get("stderr") or "")).strip()
    last = next((l for l in reversed(out.splitlines()) if l.strip()), "")
    exec_info["summary"] = last[:160]
    code = res.get("exit_code")
    if res.get("timed_out"):
        exec_info["passed"] = False
        await r.say("qa", f"pytest 시간 초과({r.cfg.exec_timeout}s)", "error")
        return [Finding(test_files[0], 1, ERROR, "TEST_TIMEOUT", f"테스트가 {r.cfg.exec_timeout}초 안에 끝나지 않았습니다", "qa", detail=out[-2500:])]
    if code == 0:
        exec_info["passed"] = True
        await r.say("qa", f"pytest 통과 ({res.get('mode')}): {last}")
        return []
    if code == 5:
        exec_info["passed"] = False
        await r.say("qa", "pytest 가 테스트를 하나도 수집하지 못했습니다", "warn")
        return [Finding(test_files[0], 1, WARN, "NO_TESTS_COLLECTED", "수집된 테스트가 없습니다", "qa")]
    exec_info["passed"] = False
    await r.say("qa", f"pytest 실패(exit {code}): {last}", "error")
    for line in [l for l in out.splitlines() if l.startswith(("FAILED", "ERROR"))][:4]:
        await r.say("qa", line[:200], "error")
    related = _paths_in(out, project, str(ws.root) if ws.root else "")
    src = [p for p in related if not _TEST_RE.search(p)]
    primary = (src or related or test_files)[0]
    return [Finding(primary, 1, ERROR, "TEST_FAILED", f"pytest 실패: {last[:140]}", "qa", detail=out[-2500:], paths=tuple(related))]


def make_llm(cfg: Config) -> BaseLLM:
    """설정의 공급자(anthropic | ollama | demo)에 맞는 LLM 어댑터를 만든다."""
    if cfg.demo:
        from .demo import DemoLLM

        return DemoLLM(cfg.factory_max_calls)
    if cfg.llm_provider == "ollama":
        from ..llm_client import OllamaLLM

        return OllamaLLM(cfg.ollama_host, cfg.ollama_model, cfg.ollama_timeout, cfg.ollama_num_ctx, cfg.factory_max_calls)
    from .llm import AnthropicLLM

    return AnthropicLLM(cfg.llm_model, cfg.llm_effort, cfg.factory_max_calls)
