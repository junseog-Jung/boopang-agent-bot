"""Modal 실행 모드(SDK 모킹 + 실제 SDK 시그니처 계약) 와 Ollama 안정화 튜닝 테스트."""
import asyncio
import inspect
import io
import json
import os
import subprocess
import sys
import tarfile
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from boopang.config import Config
from boopang.factory import demo, modal_runner
from boopang.factory.llm import LLMError
from boopang.factory.mcp_workspace import McpWorkspace, server_env
from boopang.factory.workspace_tools import SANDBOX_PACKAGES, Workspace, WorkspaceError, exec_readiness
from boopang.llm_client import OllamaLLM, extract_json, validate_schema
from boopang.web.app import app, manager


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOPANG_STEP_DELAY", "0")
    for k in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "MODAL_CONFIG_PATH"):
        monkeypatch.delenv(k, raising=False)
    manager.jobs.clear()
    manager.hourly = []


# ====================================================================== 가짜 modal SDK
class FakeStream:
    def __init__(self):
        self.data = b""
        self.eof = False

    def write(self, b):
        self.data += b

    def write_eof(self):
        self.eof = True

    def drain(self):
        pass


class FakeReader:
    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text


class FakeProc:
    def __init__(self, stdout="", stderr="", code=0, stdin=None, raises=None):
        self.stdin, self.stdout, self.stderr = stdin or FakeStream(), FakeReader(stdout), FakeReader(stderr)
        self.code, self.raises = code, raises

    def wait(self):
        if self.raises:
            raise self.raises
        return self.code


class FakeModal:
    """sys.modules['modal'] 대용. 샌드박스에 대한 모든 호출을 기록한다."""

    class _Exc:
        class AuthError(Exception): ...
        class ExecTimeoutError(Exception): ...
        class SandboxTimeoutError(Exception): ...

    def __init__(self, pytest_result=None, run_real_pytest=False, create_error=None, upload_code=0):
        self.calls, self.created, self.sandboxes = [], [], []
        self.pytest_result = pytest_result or {"stdout": "1 passed in 0.01s", "stderr": "", "code": 0}
        self.run_real_pytest, self.create_error, self.upload_code = run_real_pytest, create_error, upload_code
        fake = self
        self.exception = self._Exc

        class Image:
            @staticmethod
            def debian_slim(python_version=None):
                fake.calls.append(("debian_slim", python_version))
                return Image()

            def pip_install(self, *pkgs):
                fake.calls.append(("pip_install", pkgs))
                return self

        class App:
            @staticmethod
            def lookup(name, create_if_missing=False):
                fake.calls.append(("lookup", name, create_if_missing))
                return "APP"

        class Sandbox:
            @staticmethod
            def create(**kw):
                if fake.create_error:
                    raise fake.create_error
                fake.created.append(kw)
                sb = FakeSandbox(fake)
                fake.sandboxes.append(sb)
                return sb

        self.Image, self.App, self.Sandbox = Image, App, Sandbox


class FakeSandbox:
    def __init__(self, fake):
        self.fake, self.execs, self.terminated, self.tar = fake, [], False, b""

    def exec(self, *args, **kw):
        self.execs.append((args, kw))
        if args[0] == "sh":
            p = FakeProc(code=self.fake.upload_code)
            self.upload_stream = p.stdin
            return p
        res = self.fake.pytest_result
        if isinstance(res, Exception):
            return FakeProc(raises=res)
        if self.fake.run_real_pytest:  # 업로드된 tar 를 실제로 풀어 pytest 를 돌려 '클라우드' 결과를 흉내낸다
            import tempfile
            d = tempfile.mkdtemp()
            tarfile.open(fileobj=io.BytesIO(self.upload_stream.data)).extractall(d)
            r = subprocess.run([sys.executable, "-I", "-m", "pytest", *args[3:], "-p", "no:cacheprovider"], cwd=d, capture_output=True, text=True,
                               env={"PATH": os.environ["PATH"], "PYTHONPATH": d, "HOME": d})
            return FakeProc(r.stdout, r.stderr, r.returncode)
        return FakeProc(res["stdout"], res["stderr"], res["code"])

    def terminate(self):
        self.terminated = True


@pytest.fixture()
def fake_modal(monkeypatch):
    def install(**kw):
        fm = FakeModal(**kw)
        mod = types.ModuleType("modal")
        mod.App, mod.Image, mod.Sandbox, mod.exception = fm.App, fm.Image, fm.Sandbox, fm.exception
        monkeypatch.setitem(sys.modules, "modal", mod)
        monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
        monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-test")
        return fm
    return install


def mws(tmp_path, **kw):
    return Workspace(tmp_path / "w", exec_mode="modal", exec_timeout=30, **kw)


# ====================================================================== Modal 러너 (모킹)
def test_modal_run_uploads_tar_and_collects_results(tmp_path, fake_modal):
    fm = fake_modal(pytest_result={"stdout": "2 passed in 0.03s", "stderr": "warn", "code": 0})
    w = mws(tmp_path, modal_app="my-app", modal_cpu=2.0, modal_memory=2048)
    w.write("app/main.py", "x = 1\n")
    w.write("tests/test_a.py", "def test_a():\n    pass\n")
    (w.root / "todos.db").write_bytes(b"artifact")      # 실행 산출물은 업로드되지 않는다
    res = w.run("pytest -q -x")
    assert res["exit_code"] == 0 and res["stdout"] == "2 passed in 0.03s" and res["stderr"] == "warn" and res["mode"] == "modal" and not res["timed_out"]
    # 앱/이미지: 고정 패키지 목록만 설치
    assert ("lookup", "my-app", True) in fm.calls and ("debian_slim", "3.12") in fm.calls and ("pip_install", tuple(SANDBOX_PACKAGES)) in fm.calls
    # 업로드된 tar: 허용된 일반 파일만, 0644, root 소유
    sb = fm.sandboxes[0]
    tar = tarfile.open(fileobj=io.BytesIO(sb.upload_stream.data))
    names = tar.getnames()
    assert names == ["app/main.py", "tests/test_a.py"] and sb.upload_stream.eof
    assert all(m.isreg() and m.mode == 0o644 and m.uid == 0 and m.gid == 0 for m in tar.getmembers())
    # pytest 실행 인자: -q 한 번, 캐시 플러그인 끔, 사용자 인자 전달
    (args, kw) = sb.execs[1]
    assert args == ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x") and kw["workdir"] == "/work" and kw["timeout"] == 30
    assert sb.terminated


def test_modal_sandbox_is_isolated_no_secrets_leak(tmp_path, fake_modal, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-HOST-SECRET")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-HOST-SECRET")
    fm = fake_modal()
    w = mws(tmp_path, modal_cpu=1.5, modal_memory=512)
    w.write("tests/test_a.py", "def test_a():\n    pass\n")
    w.run("pytest")
    kw = fm.created[0]
    assert kw["block_network"] is True and kw["cpu"] == 1.5 and kw["memory"] == 512 and kw["timeout"] == 30 + 120
    assert set(kw) == {"app", "image", "timeout", "block_network", "cpu", "memory"}          # secrets/env/volumes 미전달
    sb = fm.sandboxes[0]
    for args, k in sb.execs:
        assert "secrets" not in k and set(k.get("env", {})) <= set(modal_runner.SANDBOX_ENV)
    assert sb.execs[1][1]["env"] == {"PYTHONPATH": "/work", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"}
    blob = repr((fm.calls, fm.created, sb.execs)) + sb.upload_stream.data.decode("latin1")
    assert "HOST-SECRET" not in blob and "ak-test" not in blob and "as-test" not in blob           # Modal 토큰도 샌드박스로 가지 않는다
    assert sb.terminated


def test_modal_failures_terminate_sandbox_and_map_errors(tmp_path, fake_modal):
    fm = fake_modal(pytest_result=FakeModal._Exc.ExecTimeoutError("t"))
    w = mws(tmp_path)
    w.write("tests/test_a.py", "def test_a():\n    pass\n")
    res = w.run("pytest")
    assert res["timed_out"] and res["exit_code"] is None and fm.sandboxes[0].terminated
    fm = fake_modal(pytest_result={"stdout": "1 failed", "stderr": "", "code": 1})
    r = mws(tmp_path).run("pytest")
    assert r["exit_code"] == 1 and "1 failed" in r["stdout"] and fm.sandboxes[0].terminated
    fm = fake_modal(upload_code=2)
    with pytest.raises(WorkspaceError, match="업로드"):
        mws(tmp_path).run("pytest")
    assert fm.sandboxes[0].terminated
    fm = fake_modal(create_error=FakeModal._Exc.AuthError("bad token sk-LEAK"))
    with pytest.raises(WorkspaceError, match="인증") as e:
        mws(tmp_path).run("pytest")
    assert "LEAK" not in str(e.value)
    fm = fake_modal(create_error=RuntimeError("https://secret.example/?token=SECRET-ABC"))
    with pytest.raises(WorkspaceError, match="RuntimeError") as e:
        mws(tmp_path).run("pytest")
    assert "SECRET-ABC" not in str(e.value)  # 원문 오류(토큰/URL 가능성)는 사용자에게 노출하지 않는다


def test_modal_requires_sdk_and_credentials(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "modal", None)  # import 실패
    with pytest.raises(WorkspaceError, match="pip install modal"):
        mws(tmp_path).run("pytest")
    fake = types.ModuleType("modal")
    monkeypatch.setitem(sys.modules, "modal", fake)
    monkeypatch.setenv("HOME", str(tmp_path))  # ~/.modal.toml 없음
    with pytest.raises(WorkspaceError, match="자격 증명"):
        mws(tmp_path).run("pytest")
    (tmp_path / ".modal.toml").write_text("[default]\n")
    assert modal_runner.credentials_available()


def test_modal_mode_still_enforces_command_allowlist(tmp_path, fake_modal):
    fm = fake_modal()
    w = mws(tmp_path)
    for cmd in ("rm -rf /", "pytest; curl x", "python -c 'import os'", "pip install x", "pytest ../x"):
        with pytest.raises(WorkspaceError):
            w.run(cmd)
    assert not fm.created  # 거부된 명령은 샌드박스를 만들지도 않는다


def test_modal_off_never_touches_modal(tmp_path, fake_modal):
    fm = fake_modal()
    w = Workspace(tmp_path / "o", exec_mode="off")
    w.write("tests/test_a.py", "def test_a():\n    pass\n")
    assert w.run("pytest")["skipped"] and not fm.created and not fm.calls


def test_build_tar_rejects_bad_paths():
    for bad in ("../x.py", "/abs.py", ".env", "a//b.py"):
        with pytest.raises(modal_runner.ModalError):
            modal_runner.build_tar({bad: b"x"})


def test_exec_readiness(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    ok, hint = exec_readiness(Config(factory_exec="modal"))
    assert not ok and ("modal" in hint)
    monkeypatch.setitem(sys.modules, "modal", types.ModuleType("modal"))
    ok, hint = exec_readiness(Config(factory_exec="modal"))
    assert not ok and "자격 증명" in hint
    monkeypatch.setenv("MODAL_TOKEN_ID", "a")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "b")
    assert exec_readiness(Config(factory_exec="modal")) == (True, "")
    assert exec_readiness(Config(factory_exec="off")) == (True, "")


# ====================================================================== 실제 modal SDK 시그니처 계약 (네트워크 불필요)
modal_sdk = pytest.importorskip("modal", reason="modal 패키지 미설치")


def test_runner_matches_real_modal_sdk_signatures():
    params = set(inspect.signature(modal_sdk.Sandbox.create).parameters)
    assert set(modal_runner.CREATE_KWARGS) <= params and {"secrets", "env"} <= params   # (존재하지만 우리는 넘기지 않는다)
    ex = set(inspect.signature(modal_sdk.Sandbox.exec).parameters)
    assert {"timeout", "workdir", "env"} <= ex
    assert "create_if_missing" in inspect.signature(modal_sdk.App.lookup).parameters
    assert "python_version" in inspect.signature(modal_sdk.Image.debian_slim).parameters
    assert hasattr(modal_sdk.Image, "pip_install") and hasattr(modal_sdk.Sandbox, "terminate")
    from modal.container_process import ContainerProcess
    from modal.io_streams import StreamReader, StreamWriter
    assert {"stdin", "stdout", "stderr", "wait"} <= set(dir(ContainerProcess))
    assert {"write", "write_eof", "drain"} <= set(dir(StreamWriter)) and "read" in dir(StreamReader)
    assert {"AuthError", "ExecTimeoutError", "SandboxTimeoutError"} <= set(dir(modal_sdk.exception))


# ====================================================================== MCP 서버 환경 (시크릿 경계)
def test_server_env_boundaries(monkeypatch):
    for k, v in {"ANTHROPIC_API_KEY": "sk-ant-X", "MODAL_TOKEN_ID": "ak-1", "MODAL_TOKEN_SECRET": "as-1", "AWS_SECRET_ACCESS_KEY": "aws", "GITHUB_TOKEN": "gh"}.items():
        monkeypatch.setenv(k, v)
    for mode in ("off", "docker", "local"):
        e = server_env(Config(factory_exec=mode))
        assert not any(k in e for k in ("ANTHROPIC_API_KEY", "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN")), mode
    e = server_env(Config(factory_exec="modal"))
    assert e["MODAL_TOKEN_ID"] == "ak-1" and e["MODAL_TOKEN_SECRET"] == "as-1" and e["BOOPANG_FACTORY_EXEC"] == "modal"
    assert "ANTHROPIC_API_KEY" not in e and "AWS_SECRET_ACCESS_KEY" not in e and "GITHUB_TOKEN" not in e


def test_config_modal_and_provider_defaults(monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_EXEC", "modal")
    c = Config.from_env()
    assert c.factory_exec == "modal" and c.modal_app == "boopang-factory" and c.modal_cpu == 1.0 and c.modal_memory == 1024 and c.factory_max_files == 25
    monkeypatch.setenv("BOOPANG_LLM_PROVIDER", "ollama")
    assert Config.from_env().factory_max_files == 10                       # 로컬 소형 모델 기본값
    monkeypatch.setenv("BOOPANG_FACTORY_MAX_FILES", "6")
    assert Config.from_env().factory_max_files == 6                        # 명시값 우선
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.delenv("BOOPANG_FACTORY_MAX_FILES")
    assert Config.from_env().factory_max_files == 25                       # 데모는 샘플이 7개 파일이므로 영향 없음
    monkeypatch.setenv("BOOPANG_MODAL_APP", "  ")
    assert Config.from_env().modal_app == "boopang-factory"               # 공백은 기본값으로 대체
    monkeypatch.delenv("BOOPANG_MODAL_APP")
    for name, value in (("BOOPANG_MODAL_CPU", "0"), ("BOOPANG_MODAL_MEMORY", "10")):
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError):
            Config.from_env()
        monkeypatch.delenv(name)


# ====================================================================== 파이프라인 (modal 모드) — 프로세스 내 워크스페이스로 MCP 서브프로세스 대체
class InProcWorkspace:
    """McpWorkspace 대역: 같은 Workspace 로직을 프로세스 안에서 호출한다 (SDK 모킹이 서브프로세스에는 닿지 않으므로)."""
    cfg_holder = {}

    def __init__(self, cfg):
        import tempfile
        self.ws = Workspace(tempfile.mkdtemp(), exec_mode=cfg.factory_exec, exec_timeout=cfg.exec_timeout, modal_app=cfg.modal_app)
        self.root, self.tools, self.calls = self.ws.root, ["list_project_files", "read_project_file", "run_command", "write_project_file"], 0

    async def __aenter__(self):
        return self

    async def aclose(self):
        self.ws.cleanup()

    async def write(self, p, c):
        self.calls += 1
        return self.ws.write(p, c)

    async def list(self):
        self.calls += 1
        return self.ws.list()

    async def run(self, cmd):
        from boopang.factory.mcp_workspace import McpToolError
        self.calls += 1
        try:
            return self.ws.run(cmd)
        except WorkspaceError as exc:
            raise McpToolError(f"Error executing tool run_command: {exc}") from exc


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def job_events(client):
    r = client.post("/api/factory/jobs", data={"request": "할 일 관리 웹앱을 만들어 줘 (테스트)"})
    assert r.status_code == 201, r.text
    ev = []
    with client.stream("GET", f"/api/jobs/{r.json()['job_id']}/events") as s:
        for line in s.iter_lines():
            if line.startswith("data: "):
                ev.append(json.loads(line[6:]))
    return r.json()["job_id"], ev


def msgs(ev, agent=None):
    return [e["message"] for e in ev if e["type"] == "log" and (agent is None or e["agent"] == agent)]


def test_pipeline_modal_mode_self_heals_from_cloud_test_results(client, monkeypatch, fake_modal):
    fm = fake_modal(run_real_pytest=True)   # '클라우드'가 업로드된 파일로 실제 pytest 를 돌려 결과를 돌려준다
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.setenv("BOOPANG_FACTORY_EXEC", "modal")
    monkeypatch.setattr("boopang.factory.pipeline.McpWorkspace", InProcWorkspace)
    jid, ev = job_events(client)
    assert ev[-1]["type"] == "done"
    qa = msgs(ev, "qa")
    assert any("pytest 실패" in m for m in qa) and any("pytest 통과 (modal)" in m for m in qa)   # 실패 → 자가 치유 → 통과
    assert any("Modal 클라우드 샌드박스" in m and "요금" in m for m in msgs(ev, "ceo"))
    sw = ev[-1]["software"]
    assert sw["loops"] == 1 and not sw["escalated"] and sw["exec"]["mode"] == "modal" and sw["exec"]["passed"] and sw["exec"]["runs"] == 2
    assert len(fm.sandboxes) == 2 and all(sb.terminated for sb in fm.sandboxes)                 # 실행마다 새 샌드박스, 항상 종료
    assert all(c["block_network"] is True for c in fm.created)
    assert "테스트 실행(modal 모드): 통과" in client.get(f"/api/jobs/{jid}/file", params={"path": "FACTORY_REPORT.md"}).text


def test_pipeline_modal_without_credentials_degrades_gracefully(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.setenv("BOOPANG_FACTORY_EXEC", "modal")
    monkeypatch.setitem(sys.modules, "modal", types.ModuleType("modal"))
    monkeypatch.setenv("HOME", "/nonexistent")
    monkeypatch.setattr("boopang.factory.pipeline.McpWorkspace", InProcWorkspace)
    _, ev = job_events(client)
    assert ev[-1]["type"] == "done"                      # 실행 환경 문제로 작업 전체가 실패하지 않는다 (정적 점검 + 자가 치유는 계속)
    assert any("준비되지 않았습니다" in m for m in msgs(ev, "ceo"))
    assert any("테스트를 실행하지 못했습니다" in m for m in msgs(ev, "qa"))
    sw = ev[-1]["software"]
    assert sw["exec"]["ran"] is False and sw["loops"] == 1 and any(r["code"] == "TEST_EXEC_UNAVAILABLE" for r in sw["remaining"])


def test_status_reports_modal_readiness(client, monkeypatch, tmp_path):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.setenv("BOOPANG_FACTORY_EXEC", "modal")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "modal", types.ModuleType("modal"))
    st = client.get("/api/factory/status").json()
    assert st["exec"] == "modal" and st["exec_ready"] is False and "자격 증명" in st["exec_hint"]
    monkeypatch.setenv("MODAL_TOKEN_ID", "a")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "b")
    assert client.get("/api/factory/status").json()["exec_ready"] is True


# ====================================================================== 실제 Modal 연동 (자격 증명이 있을 때만)
@pytest.mark.skipif(not (os.environ.get("BOOPANG_TEST_MODAL") == "1" and modal_runner.credentials_available()),
                    reason="실제 Modal 연동 테스트: BOOPANG_TEST_MODAL=1 과 Modal 자격 증명이 필요 (요금 발생 가능)")
def test_live_modal_sandbox_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-HOST-SECRET")
    w = Workspace(tmp_path / "live", exec_mode="modal", exec_timeout=120)
    w.write("tests/test_iso.py", """import os, socket, pytest

def test_no_network():
    s = socket.socket(); s.settimeout(3)
    with pytest.raises(OSError):
        s.connect(('1.1.1.1', 80))

def test_host_secrets_absent():
    blob = ' '.join(f'{k}={v}' for k, v in os.environ.items())
    assert 'HOST-SECRET' not in blob and 'MODAL_TOKEN' not in blob and 'ANTHROPIC' not in blob

def test_basic():
    import fastapi, httpx
    assert 1 + 1 == 2
""")
    res = w.run("pytest")
    assert res["exit_code"] == 0 and "3 passed" in res["stdout"], res


# ====================================================================== Ollama 안정화
def ollama(handler, num_ctx=8192, max_calls=10):
    return OllamaLLM("http://o.test", "qwen2.5-coder:7b-instruct", 30, num_ctx, max_calls, client=httpx.Client(transport=httpx.MockTransport(handler)))


def ok(obj, **extra):
    return httpx.Response(200, json={"message": {"content": json.dumps(obj) if not isinstance(obj, str) else obj}, "done_reason": "stop",
                                     "prompt_eval_count": 100, "eval_count": 20, **extra})


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '설명입니다.\n{"a": 1}\n끝', '  {"a": 1}  '])
def test_extract_json_lenient(text):
    assert extract_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json", "{oops", "```json\n{bad"])
def test_extract_json_rejects_garbage(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_fenced_json_accepted_without_retry():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return ok('```json\n{"content": "x"}\n```')

    llm = ollama(h)
    assert asyncio.run(llm.json("s", "u", {"required": ["content"]}, label="t")) == {"content": "x"} and n["c"] == 1 and llm.calls == 1


def test_num_predict_never_exceeds_context_budget():
    seen = []

    def h(req):
        seen.append(json.loads(req.content)["options"])
        return ok({"content": "x"})

    asyncio.run(ollama(h, num_ctx=8192).json("s", "u", {}, label="t", max_tokens=16000))
    assert seen[-1]["num_predict"] == 4096 and seen[-1]["num_ctx"] == 8192
    asyncio.run(ollama(h, num_ctx=32768).json("s", "u", {}, label="t", max_tokens=16000))
    assert seen[-1]["num_predict"] == 16000
    asyncio.run(ollama(h, num_ctx=8192).json("s", "u" * 12000, {}, label="t"))          # 프롬프트가 크면 출력 여유를 줄인다
    assert seen[-1]["num_predict"] < 4096 and seen[-1]["num_predict"] >= 768


def test_oversized_prompt_fails_fast_instead_of_silent_truncation():
    called = []
    llm = ollama(lambda r: called.append(1) or ok({"content": "x"}), num_ctx=4096)
    with pytest.raises(LLMError, match="컨텍스트.*너무 큽니다.*NUM_CTX"):
        asyncio.run(llm.json("s", "u" * 12000, {}, label="file:app/main.py"))
    assert not called   # 서버를 호출하지 않는다


def test_detects_server_side_prompt_truncation():
    llm = ollama(lambda r: ok({"content": "x"}, prompt_eval_count=8190), num_ctx=8192)
    with pytest.raises(LLMError, match="잘렸을 수 있습니다"):
        asyncio.run(llm.json("s", "u", {}, label="t"))


def test_retry_message_and_budget_accounting():
    bodies, replies = [], [httpx.Response(200, json={"message": {"content": ""}, "done_reason": "stop"}), ok({"content": "ok"})]

    def h(req):
        bodies.append(json.loads(req.content))
        return replies.pop(0)

    llm = ollama(h, max_calls=2)
    assert asyncio.run(llm.json("s", "u", {"required": ["content"]}, label="t")) == {"content": "ok"}
    assert llm.calls == 2
    with pytest.raises(LLMError, match="한도"):   # 재시도가 호출 예산을 소모했으므로 다음 호출은 거절
        asyncio.run(llm.json("s", "u", {}, label="t2"))


# ====================================================================== 결함 주입 Ollama 서버: 불안정한 실제 모델 흉내
from tests.test_ollama_mcp import FakeOllama  # noqa: E402


class FlakyOllama(FakeOllama):
    """단계별로 처음 N번은 깨진 출력을 내보낸다 (작은 모델의 형식 이탈 흉내)."""

    def __init__(self, plan):
        super().__init__()
        self.plan = dict(plan)


def test_pipeline_survives_flaky_small_model(client, monkeypatch):
    plan = {"senior product manager": ["```json\n{ broken"],                                  # PRD: 1회 깨짐 → 교정 재시도
            "ONE file": ['{"content": 123}'],                                                  # 파일: 1회 잘못된 형식
            "software architect": ['```json\n' + json.dumps(demo.ARCH) + '\n```']}           # 설계: 펜스로 감싸 응답(재시도 불필요)
    with FlakyOllama(plan) as fake:
        monkeypatch.setenv("BOOPANG_FACTORY_ENABLED", "true")
        monkeypatch.setenv("BOOPANG_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("BOOPANG_OLLAMA_HOST", fake.url)
        monkeypatch.setenv("BOOPANG_FACTORY_MAX_FILES", "10")
        jid, ev = job_events(client)
    assert ev[-1]["type"] == "done", ev[-1]
    sw = ev[-1]["software"]
    assert sw["provider"] == "ollama" and not sw["escalated"] and len(sw["files"]) >= 8
    # PRD 1회 + 파일 1회 재시도가 호출 예산에 포함됨 (= 정상 11회 + 재시도 2회... 설계는 펜스 허용이라 재시도 없음)
    assert sw["llm_calls"] >= 12


def test_pipeline_gives_clear_error_when_model_always_breaks(client, monkeypatch):
    with FlakyOllama({"senior product manager": ["{oops", "{oops again"]}) as fake:
        monkeypatch.setenv("BOOPANG_FACTORY_ENABLED", "true")
        monkeypatch.setenv("BOOPANG_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("BOOPANG_OLLAMA_HOST", fake.url)
        _, ev = job_events(client)
    assert ev[-1]["type"] == "error" and "올바른 JSON 이 아닙니다" in ev[-1]["message"]


@pytest.mark.parametrize("obj,msg", [
    ({"content": 123}, "string"), ({"content": None}, "string"), ({}, "필수 키"),
    ({"files": [{"path": "a.py", "content": 5}], "notes": ""}, r"files\[0\].content"), ({"files": "x", "notes": ""}, "array"),
    ({"refused": "no", "p": 1}, "boolean"), ({"pr": "urgent"}, "허용되지 않는 값"), ({"n": True}, "integer"),
])
def test_validate_schema_catches_type_errors(obj, msg):
    schema = {"type": "object", "required": [], "properties": {
        "content": {"type": "string"}, "refused": {"type": "boolean"}, "pr": {"type": "string", "enum": ["must", "should"]}, "n": {"type": "integer"},
        "files": {"type": "array", "items": {"type": "object", "required": ["path", "content"], "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}}}
    if msg == "필수 키":
        schema["required"] = ["content"]
    import re
    assert re.search(msg, validate_schema(obj, schema) or "")


def test_validate_schema_accepts_real_pipeline_schemas():
    from boopang.factory import prompts as P
    assert validate_schema(demo.PRD, P.PRD_SCHEMA) is None and validate_schema(demo.ARCH, P.ARCH_SCHEMA) is None
    assert validate_schema({"content": "x"}, P.FILE_SCHEMA) is None
    assert validate_schema({"files": [{"path": "a.py", "content": "x"}], "notes": ""}, P.REFINE_SCHEMA) is None


def test_wrong_type_triggers_corrective_retry():
    bodies, replies = [], [ok({"content": 123}), ok({"content": "fixed"})]

    def h(req):
        bodies.append(json.loads(req.content))
        return replies.pop(0)

    schema = {"type": "object", "required": ["content"], "properties": {"content": {"type": "string"}}}
    assert asyncio.run(ollama(h).json("s", "u", schema, label="t")) == {"content": "fixed"}
    assert "string" in bodies[1]["messages"][-1]["content"]      # 무엇이 틀렸는지 모델에게 알려준다


def test_real_mcp_subprocess_with_real_modal_sdk_degrades_without_credentials(client, monkeypatch, tmp_path):
    """모킹 없음: 실제 MCP 서버 프로세스가 실제 modal SDK 를 import 하고, 자격 증명이 없을 때 안전하게 안내한다."""
    monkeypatch.setenv("HOME", str(tmp_path))  # ~/.modal.toml 없음, 토큰 환경변수 없음
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.setenv("BOOPANG_FACTORY_EXEC", "modal")
    _, ev = job_events(client)
    assert ev[-1]["type"] == "done"
    qa = msgs(ev, "qa")
    assert any("테스트를 실행하지 못했습니다" in m and "자격 증명" in m for m in qa), qa
    assert ev[-1]["software"]["mcp"]["enabled"] and ev[-1]["software"]["exec"]["ran"] is False
