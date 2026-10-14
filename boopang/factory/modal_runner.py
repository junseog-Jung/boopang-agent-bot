"""Modal 클라우드 서버리스 샌드박스에서 생성된 pytest 를 실행한다 (BOOPANG_FACTORY_EXEC=modal).

왜 Sandbox 인가: 신뢰할 수 없는 임의 코드를 실행하라고 만든 기능이며, Function 은 배포/직렬화가 필요해 맞지 않는다.

보안 설계 (생성 코드는 우리 서버가 아니라 Modal 의 격리 환경에서만 실행된다):
- 네트워크 차단(block_network=True). 필요한 패키지는 이미지 빌드 시 '고정된 우리 목록'만 설치한다 (LLM 의 requirements 는 설치하지 않는다).
- 호스트 환경변수/시크릿 비전달: Sandbox.create 에 secrets/env/volumes 를 넘기지 않고, exec 에는 상수 환경변수 3개만 명시한다.
- 업로드는 우리가 검증한 일반 파일만 담은 tar 를 stdin 으로 한 번에 전달한다 (경로 재검증, 권한 0644, 소유자 root).
- 명령은 pytest 한 가지(인자는 허용 목록 검증을 통과한 것만). 쉘 문자열에 사용자 입력이 들어가지 않는다.
- 작업이 끝나면(오류 포함) 항상 sandbox.terminate().
Modal 사용량 요금이 발생할 수 있다. 실제 클라우드 호출은 Modal 계정이 있어야 하며 이 모듈의 단위 테스트는 SDK 를 모킹한다.
"""
from __future__ import annotations

import io
import logging
import os
import tarfile
from pathlib import Path

from .project import ProjectError, validate_path

log = logging.getLogger("boopang.modal")

# exec 에 전달하는 유일한 환경변수 (호스트 값이 아님)
SANDBOX_ENV = {"PYTHONPATH": "/work", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"}
# Sandbox.create 에 넘기는 키 (실제 SDK 시그니처와 일치하는지 테스트로 고정)
CREATE_KWARGS = ("app", "image", "timeout", "block_network", "cpu", "memory")
# MCP 서버 프로세스에 (modal 모드에서만) 전달하는 환경변수: Modal 클라이언트 인증 + 네트워크 설정. 샌드박스로는 전달되지 않는다.
CLIENT_ENV_PASSTHROUGH = ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "MODAL_PROFILE", "MODAL_ENVIRONMENT", "MODAL_CONFIG_PATH",
                          "SSL_CERT_FILE", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")


class ModalError(RuntimeError):
    """사용자에게 그대로 보여줘도 안전한 메시지 (토큰/원문 오류 미포함)."""


def credentials_available() -> bool:
    if os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"):
        return True
    return Path(os.environ.get("MODAL_CONFIG_PATH") or (Path.home() / ".modal.toml")).is_file()


def sdk_available() -> bool:
    try:
        import modal  # noqa: F401
        return True
    except ImportError:
        return False


def build_tar(files: dict) -> bytes:
    """검증된 일반 파일만 담은 tar. 디렉터리/링크/장치 항목은 만들지 않는다."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in sorted(files):
            try:
                validate_path(path)
            except ProjectError as exc:
                raise ModalError(f"업로드할 수 없는 경로입니다: {path[:60]}") from exc
            data = files[path]
            info = tarfile.TarInfo(name=path)
            info.size, info.mode, info.mtime = len(data), 0o644, 1767225600
            info.uid = info.gid = 0
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else (value or "")


def run_pytest(files: dict, pytest_args: list, *, app_name: str, timeout: int, cpu: float, memory: int, packages: list) -> dict:
    """files: {상대경로: bytes}. 반환: exit_code / stdout / stderr / timed_out (workspace_tools 와 같은 형태)."""
    try:
        import modal
    except ImportError as exc:
        raise ModalError("modal 패키지가 설치되어 있지 않습니다 (pip install modal).") from exc
    if not credentials_available():
        raise ModalError("Modal 자격 증명이 없습니다. `modal token set` 으로 설정하거나 MODAL_TOKEN_ID / MODAL_TOKEN_SECRET 환경변수를 지정하세요.")
    tar_bytes = build_tar(files)

    sb = None
    try:
        app = modal.App.lookup(app_name, create_if_missing=True)
        image = modal.Image.debian_slim(python_version="3.12").pip_install(*packages)  # 고정 목록만 (네트워크 허용은 빌드 단계뿐)
        sb = modal.Sandbox.create(app=app, image=image, timeout=timeout + 120, block_network=True, cpu=cpu, memory=memory)

        up = sb.exec("sh", "-c", "mkdir -p /work && tar xf - -C /work --no-same-owner", timeout=60)  # 고정 문자열 (입력 없음)
        up.stdin.write(tar_bytes)
        up.stdin.write_eof()
        up.stdin.drain()
        if up.wait() != 0:
            raise ModalError("샌드박스에 파일을 업로드하지 못했습니다.")

        proc = sb.exec("python", "-m", "pytest", *pytest_args, timeout=timeout, workdir="/work", env=dict(SANDBOX_ENV))
        try:
            out, err = _text(proc.stdout.read()), _text(proc.stderr.read())
            code = proc.wait()
        except modal.exception.ExecTimeoutError:
            return {"exit_code": None, "stdout": "", "stderr": "시간 초과로 샌드박스 실행을 중단했습니다", "timed_out": True}
        return {"exit_code": code, "stdout": out, "stderr": err, "timed_out": False}
    except ModalError:
        raise
    except modal.exception.AuthError as exc:
        raise ModalError("Modal 인증에 실패했습니다. 토큰을 확인하세요.") from exc
    except modal.exception.ExecTimeoutError:
        return {"exit_code": None, "stdout": "", "stderr": "시간 초과로 샌드박스 실행을 중단했습니다", "timed_out": True}
    except modal.exception.SandboxTimeoutError:
        return {"exit_code": None, "stdout": "", "stderr": "샌드박스 수명 제한으로 실행이 중단되었습니다", "timed_out": True}
    except Exception as exc:  # 원문(토큰/URL 가능성)은 서버 로그에만
        log.error("modal run failed: %s", type(exc).__name__)
        raise ModalError(f"Modal 샌드박스 실행에 실패했습니다 ({type(exc).__name__}). 서버 로그를 확인하세요.") from exc
    finally:
        if sb is not None:
            try:
                sb.terminate()
            except Exception:  # 종료 실패가 결과를 가리지 않도록
                log.warning("modal sandbox terminate failed")
