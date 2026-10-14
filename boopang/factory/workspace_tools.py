"""작업별 격리 워크스페이스: 안전한 파일 입출력 + 허용 목록 기반 명령 실행.

MCP 서버(boopang/mcp_server.py)가 이 모듈을 도구로 노출한다. LLM 이 만든 경로/명령은 신뢰할 수 없으므로
- 모든 경로는 workspace root 안으로 제한하고(심볼릭 링크 거부, project.validate_path 재사용),
- 명령은 셸 없이 argv 로 실행하며 아래 허용 목록의 형태만 받는다.
실행 모드(exec_mode): off(기본, pytest 실행 안 함) | docker(네트워크 차단 컨테이너) | local(비격리 — 위험).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import secrets
from dataclasses import dataclass
from pathlib import Path

from . import modal_runner
from .project import ALLOWED_EXT, ALLOWED_NAMES, BLOCKED_PARTS, validate_path, ProjectError

OUTPUT_LIMIT = 8000
_SAFE_FLAGS = {"-q", "-x", "-v", "-vv", "--tb=short", "--tb=line", "--tb=no", "--maxfail=1"}
_K_EXPR = re.compile(r"^[A-Za-z0-9_ ]{1,80}$")
# 샌드박스 이미지에 미리 설치된 패키지 (docker 모드는 네트워크가 없어 pip install 불가)
SANDBOX_PACKAGES = ["pytest", "fastapi", "httpx", "uvicorn", "pydantic", "flask", "requests", "jinja2", "sqlalchemy"]


class WorkspaceError(RuntimeError):
    """사용자/LLM 에게 그대로 보여줘도 안전한 메시지."""


@dataclass
class Limits:
    max_files: int = 28
    max_file_bytes: int = 60_000
    max_total_bytes: int = 400_000


def _tail(text: str) -> str:
    return text if len(text) <= OUTPUT_LIMIT else "…(앞부분 생략)…\n" + text[-OUTPUT_LIMIT:]


class Workspace:
    def __init__(self, root, *, limits: Limits | None = None, exec_mode: str = "off", sandbox_image: str = "boopang-sandbox",
                 exec_timeout: int = 60, modal_app: str = "boopang-factory", modal_cpu: float = 1.0, modal_memory: int = 1024):
        self.root = Path(root).resolve()
        self.limits = limits or Limits()
        self.exec_mode, self.sandbox_image, self.exec_timeout = exec_mode, sandbox_image, exec_timeout
        self.modal_app, self.modal_cpu, self.modal_memory = modal_app, modal_cpu, modal_memory
        if exec_mode not in ("off", "docker", "modal", "local"):
            raise WorkspaceError("exec_mode 는 off, docker, modal, local 중 하나여야 합니다")
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 경로
    def _resolve(self, path: str) -> Path:
        try:
            validate_path(path)
        except ProjectError as exc:
            raise WorkspaceError(str(exc)) from exc
        target = self.root / path
        cur = self.root
        for part in Path(path).parts:  # 경로 구성요소 중 심볼릭 링크가 있으면 거부
            cur = cur / part
            if cur.is_symlink():
                raise WorkspaceError(f"심볼릭 링크는 허용되지 않습니다: {path}")
        resolved = target.resolve()
        if self.root not in resolved.parents and resolved != self.root:
            raise WorkspaceError(f"워크스페이스 밖의 경로입니다: {path}")
        return target

    def _files(self):
        for dirpath, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in BLOCKED_PARTS and d != ".pytest_cache" and not Path(dirpath, d).is_symlink()]
            for n in names:
                p = Path(dirpath, n)
                rel = p.relative_to(self.root).as_posix()
                if p.is_symlink() or p.suffix == ".pyc":
                    continue
                if n not in ALLOWED_NAMES and p.suffix.lower() not in ALLOWED_EXT:
                    continue  # 테스트 실행이 만든 산출물(.db 등)은 목록에서 제외
                yield rel, p

    # ------------------------------------------------------------ 파일 도구
    def write(self, path: str, content: str) -> str:
        if not isinstance(content, str):
            raise WorkspaceError("content 는 문자열이어야 합니다")
        text = content.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
        data = text.encode("utf-8")
        if len(data) > self.limits.max_file_bytes:
            raise WorkspaceError(f"파일이 너무 큽니다 ({len(data):,} > {self.limits.max_file_bytes:,} bytes)")
        target = self._resolve(path)
        if target.is_dir():
            raise WorkspaceError(f"디렉터리입니다: {path}")
        existing = {rel: p.stat().st_size for rel, p in self._files()}
        if path not in existing and len(existing) >= self.limits.max_files:
            raise WorkspaceError(f"파일 개수 한도({self.limits.max_files}개)를 초과했습니다")
        if sum(v for k, v in existing.items() if k != path) + len(data) > self.limits.max_total_bytes:
            raise WorkspaceError(f"전체 용량 한도({self.limits.max_total_bytes:,} bytes)를 초과했습니다")
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.exec_mode == "docker":  # 비루트 컨테이너 사용자가 쓸 수 있어야 함 (부모 디렉터리는 0700 으로 보호)
            for d in [target.parent, *target.parent.parents]:
                if d == self.root.parent:
                    break
                os.chmod(d, 0o777)
        tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        return f"wrote {path} ({len(data):,} bytes)"

    def read(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"파일이 없습니다: {path}")
        if target.stat().st_size > self.limits.max_file_bytes:
            raise WorkspaceError("파일이 너무 큽니다")
        return target.read_text(encoding="utf-8", errors="replace")

    def list(self) -> list[dict]:
        out = []
        for rel, p in sorted(self._files()):
            out.append({"path": rel, "size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        return out

    # ------------------------------------------------------------ 명령 (허용 목록)
    def parse_command(self, command: str) -> tuple[str, list[str]]:
        """허용된 형태만 통과. 반환: (종류, 정규화된 사용자 인자)."""
        if not isinstance(command, str) or not command.strip() or len(command) > 300 or any(c in command for c in "\x00\n\r"):
            raise WorkspaceError("허용되지 않는 명령입니다")
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            raise WorkspaceError("명령을 해석할 수 없습니다") from exc
        if argv[:3] == ["python", "-m", "pytest"] or argv[:3] == ["python3", "-m", "pytest"]:
            rest = argv[3:]
        elif argv[:1] == ["pytest"]:
            rest = argv[1:]
        else:
            rest = None
        if rest is not None:
            clean, i = [], 0
            while i < len(rest):
                t = rest[i]
                if t in _SAFE_FLAGS:
                    clean.append(t)
                elif t == "-k" and i + 1 < len(rest) and _K_EXPR.match(rest[i + 1]):
                    clean += ["-k", rest[i + 1]]
                    i += 1
                elif not t.startswith("-"):
                    self._safe_target(t)
                    clean.append(t)
                else:
                    raise WorkspaceError(f"허용되지 않는 pytest 옵션입니다: {t[:40]}")
                i += 1
            return "pytest", clean
        if len(argv) == 4 and argv[:3] in (["python", "-m", "py_compile"], ["python3", "-m", "py_compile"]):
            return "py_syntax", [self._src(argv[3], ".py")]
        if len(argv) == 3 and argv[:2] == ["node", "--check"]:
            return "node_check", [self._src(argv[2], ".js", ".mjs", ".cjs")]
        raise WorkspaceError("허용되지 않는 명령입니다 (허용: pytest, python -m py_compile <파일>, node --check <파일>)")

    @staticmethod
    def _safe_target(token: str) -> None:
        """pytest 대상(파일, 디렉터리, 파일::테스트): 워크스페이스 안의 상대 경로만."""
        path = token.split("::", 1)[0]
        parts = Path(path).parts
        if (not path or len(path) > 120 or not re.fullmatch(r"[A-Za-z0-9_./\-]+", path) or path.startswith("/") or "//" in path
                or len(parts) > 6 or any(p in ("", ".", "..") or p in BLOCKED_PARTS for p in parts)):
            raise WorkspaceError(f"허용되지 않는 테스트 경로입니다: {token[:60]}")
        if not re.fullmatch(r"[A-Za-z0-9_.:\[\]\-]*", token[len(path):]):
            raise WorkspaceError(f"허용되지 않는 테스트 지정입니다: {token[:60]}")

    def _src(self, path: str, *suffixes: str) -> str:
        target = self._resolve(path)
        if target.suffix not in suffixes or not target.is_file():
            raise WorkspaceError(f"검사할 수 없는 파일입니다: {path}")
        return path

    def run(self, command: str) -> dict:
        kind, args = self.parse_command(command)
        base = {"command": command, "mode": self.exec_mode, "skipped": False, "timed_out": False}
        if kind == "py_syntax":  # 파싱만 하고 실행하지 않으므로 모든 모드에서 허용
            code = "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read(),sys.argv[1])"
            return {**base, "mode": "static", **self._exec([sys.executable, "-I", "-B", "-c", code, args[0]], 10)}
        if kind == "node_check":
            node = shutil.which("node")
            if not node:
                return {**base, "mode": "static", "skipped": True, "exit_code": None, "stdout": "", "stderr": "node 가 설치되어 있지 않습니다"}
            return {**base, "mode": "static", **self._exec([node, "--check", args[0]], 10)}
        if self.exec_mode == "off":
            return {**base, "skipped": True, "exit_code": None, "stdout": "",
                    "stderr": "테스트 실행이 비활성화되어 있습니다 (BOOPANG_FACTORY_EXEC=off)"}
        # 서버가 -q 를 한 번 붙인다. 사용자가 또 주면 -q -q(초정숙)가 되어 요약 줄이 사라지므로 제거한다.
        pytest_args = ["-q", "-p", "no:cacheprovider", *[a for a in args if a != "-q"]]
        if self.exec_mode == "local":
            return {**base, **self._exec([sys.executable, "-m", "pytest", *pytest_args], self.exec_timeout, limited=True)}
        if self.exec_mode == "modal":
            return {**base, **self._run_modal(pytest_args)}
        return {**base, **self._run_docker(pytest_args)}

    # ------------------------------------------------------------ 실행기
    def _run_modal(self, pytest_args: list[str]) -> dict:
        files = {rel: p.read_bytes() for rel, p in self._files()}  # 허용된 파일만 (실행 산출물·캐시 제외)
        try:
            out = modal_runner.run_pytest(files, pytest_args, app_name=self.modal_app, timeout=self.exec_timeout, cpu=self.modal_cpu,
                                          memory=self.modal_memory, packages=SANDBOX_PACKAGES)
        except modal_runner.ModalError as exc:
            raise WorkspaceError(str(exc)) from exc
        out["stdout"], out["stderr"] = _tail(out["stdout"]), _tail(out["stderr"])
        return out

    def _env(self) -> dict:
        # 서버 환경변수(API 키 등)는 절대 넘기지 않는다
        return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(self.root), "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(self.root), "PYTHONHASHSEED": "0"}

    def _exec(self, argv: list[str], timeout: int, limited: bool = False) -> dict:
        def limits():  # 자식 프로세스에만 적용 (CPU/메모리/파일크기/FD)
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (timeout + 5, timeout + 5))
            resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
            resource.setrlimit(resource.RLIMIT_FSIZE, (20 << 20, 20 << 20))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))

        try:
            proc = subprocess.Popen(argv, cwd=self.root, env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    errors="replace", start_new_session=True, preexec_fn=limits if limited else None)
        except OSError as exc:
            raise WorkspaceError(f"실행할 수 없습니다: {exc.strerror}") from exc
        try:
            out, err = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # 자식이 만든 프로세스까지 함께 종료
            out, err = proc.communicate()
            timed_out = True
        return {"exit_code": None if timed_out else proc.returncode, "stdout": _tail(out or ""), "stderr": _tail(err or ""), "timed_out": timed_out}

    def _run_docker(self, pytest_args: list[str]) -> dict:
        if not shutil.which("docker"):
            raise WorkspaceError("docker 실행 파일을 찾을 수 없습니다 (BOOPANG_FACTORY_EXEC=docker)")
        name = "bpsb-" + secrets.token_hex(6)
        cmd = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
               "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "128", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--user", "65534:65534", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
               "-e", "PYTHONPATH=/work", "-v", f"{self.root}:/work:rw", "-w", "/work", self.sandbox_image,
               "python", "-m", "pytest", *pytest_args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=self.exec_timeout + 20)
            out = {"exit_code": r.returncode, "stdout": _tail(r.stdout), "stderr": _tail(r.stderr), "timed_out": False}
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            return {"exit_code": None, "stdout": "", "stderr": "시간 초과로 컨테이너를 종료했습니다", "timed_out": True}
        if out["exit_code"] == 125 or "Unable to find image" in out["stderr"] or "No such image" in out["stderr"]:
            raise WorkspaceError(f"샌드박스 이미지 '{self.sandbox_image}' 를 사용할 수 없습니다. deploy/build_sandbox.sh 로 빌드하세요.")
        return out

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def from_config(root, cfg) -> Workspace:
    return Workspace(root, limits=Limits(cfg.factory_max_files + 3, cfg.factory_max_file_bytes, cfg.factory_max_total_bytes),
                     exec_mode=cfg.factory_exec, sandbox_image=cfg.sandbox_image, exec_timeout=cfg.exec_timeout,
                     modal_app=cfg.modal_app, modal_cpu=cfg.modal_cpu, modal_memory=cfg.modal_memory)


def exec_readiness(cfg) -> tuple[bool, str]:
    """선택한 실행 모드가 지금 사용 가능한지 (상태 화면 안내용, 네트워크 호출 없음)."""
    mode = cfg.factory_exec
    if mode == "modal":
        if not modal_runner.sdk_available():
            return False, "modal 패키지가 필요합니다 (pip install modal)"
        if not modal_runner.credentials_available():
            return False, "Modal 자격 증명이 없습니다 (`modal token set` 또는 MODAL_TOKEN_ID/MODAL_TOKEN_SECRET)"
    elif mode == "docker" and not shutil.which("docker"):
        return False, "docker 실행 파일을 찾을 수 없습니다"
    return True, ""


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
