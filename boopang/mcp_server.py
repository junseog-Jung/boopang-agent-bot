"""소프트웨어 팩토리 MCP 도구 서버 (stdio).

    python -m boopang.mcp_server --root /작업/디렉터리

에이전트(MCP 클라이언트)는 이 서버의 도구로만 파일을 만들고 테스트를 실행한다.
모든 동작은 --root 안으로 제한되고, 임의 셸 명령은 실행할 수 없다 (workspace_tools 참고).
주의: stdio 전송은 stdout 을 프로토콜에 쓰므로 이 파일에서 print() 를 하면 안 된다.
"""
from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from .config import Config
from .factory.workspace_tools import Workspace, from_config, to_json

mcp = FastMCP("SoftwareFactoryMCP")
_ws: Workspace | None = None


def _workspace() -> Workspace:
    if _ws is None:
        raise RuntimeError("워크스페이스가 초기화되지 않았습니다 (--root 필요)")
    return _ws


@mcp.tool()
def write_project_file(file_path: str, content: str) -> str:
    """프로젝트 워크스페이스 안에 소스 파일을 생성하거나 덮어씁니다. file_path 는 'app/main.py' 같은 상대 경로여야 합니다."""
    return _workspace().write(file_path, content)


@mcp.tool()
def read_project_file(file_path: str) -> str:
    """워크스페이스의 파일 내용을 읽습니다."""
    return _workspace().read(file_path)


@mcp.tool()
def list_project_files() -> str:
    """워크스페이스의 파일 목록을 JSON([{path,size,sha256}])으로 반환합니다."""
    return to_json(_workspace().list())


@mcp.tool()
def run_command(command: str) -> str:
    """허용된 검증 명령만 실행합니다: `pytest [-q -x -v -k 식 경로]`, `python -m py_compile <파일>`, `node --check <파일>`.
    셸이 아니므로 파이프/리다이렉션/기타 명령은 거부됩니다. 결과는 JSON(exit_code, stdout, stderr, timed_out, mode)입니다."""
    return to_json(_workspace().run(command))


def main(argv=None) -> None:
    global _ws
    ap = argparse.ArgumentParser(prog="boopang.mcp_server")
    ap.add_argument("--root", required=True, help="작업 디렉터리(이 안으로만 접근 가능)")
    args = ap.parse_args(argv)
    _ws = from_config(args.root, Config.from_env())
    mcp.run()


if __name__ == "__main__":
    main()
