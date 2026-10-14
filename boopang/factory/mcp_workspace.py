"""MCP(stdio) 클라이언트 — 파이프라인이 파일 기록/조회/테스트 실행을 MCP 도구로 수행한다.

작업마다 격리된 임시 디렉터리를 만들고, 그 디렉터리를 --root 로 하는 boopang.mcp_server 를 자식 프로세스로 띄운다.
anyio 취소 범위 제약 때문에 `async with` 의 진입/종료는 같은 태스크에서 이뤄져야 한다.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path

from ..config import Config


class McpToolError(RuntimeError):
    """MCP 도구가 오류를 반환했을 때 (메시지는 서버가 만든 안전한 문장)."""


class McpWorkspace:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root: Path | None = None
        self.tools: list[str] = []
        self.calls = 0
        self._stack: AsyncExitStack | None = None
        self._session = None

    async def __aenter__(self) -> "McpWorkspace":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        base = Path(self.cfg.workspace_dir) if self.cfg.workspace_dir else Path(tempfile.gettempdir()) / "boopang-workspaces"
        base.mkdir(parents=True, exist_ok=True)
        os.chmod(base, 0o700)
        self.root = Path(tempfile.mkdtemp(prefix="job-", dir=base))
        os.chmod(self.root, 0o777 if self.cfg.factory_exec == "docker" else 0o700)
        c = self.cfg
        env = {  # 서버 프로세스에는 필요한 설정만 전달한다 (API 키 등은 넘기지 않음)
            "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
            "BOOPANG_FACTORY_EXEC": c.factory_exec, "BOOPANG_SANDBOX_IMAGE": c.sandbox_image, "BOOPANG_EXEC_TIMEOUT": str(c.exec_timeout),
            "BOOPANG_FACTORY_MAX_FILES": str(c.factory_max_files), "BOOPANG_FACTORY_MAX_FILE_BYTES": str(c.factory_max_file_bytes),
            "BOOPANG_FACTORY_MAX_TOTAL_BYTES": str(c.factory_max_total_bytes),
        }
        params = StdioServerParameters(command=sys.executable, args=["-m", "boopang.mcp_server", "--root", str(self.root)], env=env,
                                       cwd=str(Path(__file__).resolve().parents[2]))
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
            self.tools = sorted(t.name for t in (await self._session.list_tools()).tools)
        except BaseException:
            await self.aclose()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            try:
                await stack.aclose()
            except BaseException:  # 서버 종료 중 오류는 정리를 막지 않는다
                pass
        if self.root is not None:
            shutil.rmtree(self.root, ignore_errors=True)

    async def call(self, name: str, **arguments) -> str:
        if self._session is None:
            raise McpToolError("MCP 세션이 닫혀 있습니다")
        self.calls += 1
        result = await self._session.call_tool(name, arguments, read_timeout_seconds=timedelta(seconds=self.cfg.exec_timeout + 60))
        text = "".join(getattr(b, "text", "") for b in result.content)
        if result.isError:
            raise McpToolError(text or f"{name} 실패")
        return text

    async def write(self, path: str, content: str) -> str:
        return await self.call("write_project_file", file_path=path, content=content)

    async def read(self, path: str) -> str:
        return await self.call("read_project_file", file_path=path)

    async def list(self) -> list[dict]:
        return json.loads(await self.call("list_project_files"))

    async def run(self, command: str) -> dict:
        return json.loads(await self.call("run_command", command=command))
