import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from boopang.config import Config
from boopang.factory import demo
from boopang.factory.llm import LLMError
from boopang.factory.mcp_workspace import McpToolError, McpWorkspace
from boopang.factory.workspace_tools import Limits, Workspace, WorkspaceError
from boopang.llm_client import OllamaLLM, ollama_status
from boopang.web.app import app, manager


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOPANG_STEP_DELAY", "0")
    manager.jobs.clear()
    manager.hourly = []


def run(coro):
    return asyncio.run(coro)


# ======================================================================= Ollama 클라이언트
def make_llm(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaLLM("http://ollama.test:11434", kw.pop("model", "qwen2.5-coder:7b-instruct"), 30, 8192, kw.pop("max_calls", 10), client=client)


def chat_ok(obj, **extra):
    return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(obj)}, "done_reason": "stop",
                                     "prompt_eval_count": 12, "eval_count": 5, **extra})


def test_ollama_request_shape_and_usage():
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return chat_ok({"content": "x"})

    llm = make_llm(handler)
    schema = {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}
    assert run(llm.json("SYS", "USER", schema, label="t")) == {"content": "x"}
    b = seen["body"]
    assert seen["url"] == "http://ollama.test:11434/api/chat"
    assert b["model"] == "qwen2.5-coder:7b-instruct" and b["stream"] is False and b["format"] == schema
    assert b["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert b["options"]["temperature"] == 0.2 and b["options"]["num_ctx"] == 8192 and b["options"]["num_predict"] == 16000
    assert (llm.calls, llm.input_tokens, llm.output_tokens) == (1, 12, 5)


def test_ollama_retries_once_on_bad_json_then_succeeds():
    replies = [httpx.Response(200, json={"message": {"content": "```json {oops"}, "done_reason": "stop"}), chat_ok({"content": "ok"})]
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return replies.pop(0)

    llm = make_llm(handler)
    assert run(llm.json("s", "u", {"required": ["content"]}, label="t")) == {"content": "ok"}
    assert len(bodies) == 2 and "invalid" in bodies[1]["messages"][-1]["content"] and llm.calls == 2  # 재시도도 예산에 포함


def test_ollama_missing_required_keys_retries_then_fails():
    llm = make_llm(lambda req: chat_ok({"wrong": 1}))
    with pytest.raises(LLMError, match="올바른 JSON 이 아닙니다.*필수 키 누락"):
        run(llm.json("s", "u", {"required": ["content"]}, label="t"))


@pytest.mark.parametrize("handler,msg", [
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), "ollama serve"),
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), "시간이 초과"),
    (lambda r: httpx.Response(404, json={"error": "model not found"}), "ollama pull qwen2.5-coder:7b-instruct"),
    (lambda r: httpx.Response(400, json={"error": "bad format"}), "0.5 이상"),
    (lambda r: httpx.Response(500, text="boom"), "HTTP 500"),
    (lambda r: httpx.Response(200, text="not json"), "해석"),
    (lambda r: httpx.Response(200, json={"message": {"content": "{}"}, "done_reason": "length"}), "잘렸"),
])
def test_ollama_error_messages(handler, msg):
    with pytest.raises(LLMError, match=msg):
        run(make_llm(handler).json("s", "u", {}, label="t"))


def test_ollama_status():
    ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:7b-instruct"}, {"name": "llama3:8b"}]})))
    assert ollama_status("http://x", "qwen2.5-coder:7b-instruct", ok) == {"reachable": True, "model_installed": True, "models": ["qwen2.5-coder:7b-instruct", "llama3:8b"]}
    assert not ollama_status("http://x", "deepseek-coder:6.7b", ok)["model_installed"]
    assert ollama_status("http://x", "llama3", ok)["model_installed"]  # 태그 없는 이름은 접두 일치
    down = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    assert ollama_status("http://x", "m", down) == {"reachable": False, "model_installed": False, "models": []}


def test_config_provider_and_ollama_env(monkeypatch):
    monkeypatch.setenv("BOOPANG_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11434")
    monkeypatch.setenv("BOOPANG_FACTORY_EXEC", "docker")
    c = Config.from_env()
    assert c.llm_provider == "ollama" and c.ollama_host == "http://127.0.0.1:11434" and c.factory_exec == "docker" and c.factory_mcp
    monkeypatch.setenv("BOOPANG_OLLAMA_HOST", "https://gpu.internal:11434/")
    assert Config.from_env().ollama_host == "https://gpu.internal:11434"
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    assert Config.from_env().demo


@pytest.mark.parametrize("name,value", [("BOOPANG_LLM_PROVIDER", "openai"), ("BOOPANG_FACTORY_EXEC", "yes"), ("BOOPANG_OLLAMA_NUM_CTX", "512"),
                                        ("BOOPANG_OLLAMA_TIMEOUT", "0"), ("BOOPANG_EXEC_TIMEOUT", "1"), ("OLLAMA_HOST", "ftp://x")])
def test_config_rejects_bad_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Config.from_env()


# ======================================================================= 워크스페이스 도구 (경로 격리 / 허용 목록)
@pytest.fixture()
def ws(tmp_path):
    return Workspace(tmp_path / "w", exec_mode="off")


def test_ws_write_read_list_and_overwrite(ws):
    assert "wrote app/a.py" in ws.write("app/a.py", "x = 1\r\n")
    assert ws.read("app/a.py") == "x = 1\n"
    ws.write("app/a.py", "x = 2\n")
    listing = ws.list()
    assert [f["path"] for f in listing] == ["app/a.py"] and listing[0]["size"] == 6 and len(listing[0]["sha256"]) == 64


@pytest.mark.parametrize("path", ["../x.py", "/etc/passwd", "a/../../x.py", ".env", "a//b.py", ".git/config", "x.exe", "a/b/c/d/e/f/g.py", ""])
def test_ws_rejects_unsafe_paths(ws, path):
    with pytest.raises(WorkspaceError):
        ws.write(path, "x")
    with pytest.raises(WorkspaceError):
        ws.read(path)


def test_ws_symlink_escape_blocked(ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws.root / "link").symlink_to(outside)
    with pytest.raises(WorkspaceError, match="심볼릭"):
        ws.write("link/pwned.py", "x")
    assert not (outside / "pwned.py").exists()
    (ws.root / "f.py").symlink_to(outside / "secret.py")
    with pytest.raises(WorkspaceError, match="심볼릭"):
        ws.read("f.py")


def test_ws_limits(tmp_path):
    w = Workspace(tmp_path / "l", limits=Limits(max_files=2, max_file_bytes=10, max_total_bytes=15))
    w.write("a.py", "x" * 10)
    with pytest.raises(WorkspaceError, match="너무 큽니다"):
        w.write("b.py", "x" * 11)
    w.write("b.py", "y" * 5)
    with pytest.raises(WorkspaceError, match="개수"):
        w.write("c.py", "z")
    with pytest.raises(WorkspaceError, match="전체 용량"):
        w.write("b.py", "w" * 10)  # a.py(10) + b.py(10) = 20 > 15
    with pytest.raises(WorkspaceError):
        w.write("a.py", 5)


def test_ws_list_ignores_test_artifacts(ws):
    ws.write("a.py", "x=1\n")
    (ws.root / "__pycache__").mkdir()
    (ws.root / "__pycache__" / "a.cpython-313.pyc").write_bytes(b"x")
    (ws.root / "todos.db").write_bytes(b"sqlite")
    (ws.root / ".pytest_cache").mkdir()
    assert [f["path"] for f in ws.list()] == ["a.py"]


@pytest.mark.parametrize("cmd", [
    "rm -rf /", "pytest; ls", "pytest && id", "pytest | cat", "python -c 'import os'", "bash -c id", "sh -c id", "pip install requests",
    "curl http://x", "pytest -p evil_plugin", "pytest --rootdir=/", "pytest ../x.py", "pytest /etc/passwd", "python -m http.server",
    "node -e 1", "python -m pytest --co", "cat /etc/passwd", "pytest ..", "pytest tests/../../x", "pytest tests::$(id)", "pytest ~/x", "pytest $(id)", "", "x" * 400, "pytest\nid", "python -m py_compile ../x.py",
])
def test_command_allowlist_rejects(ws, cmd):
    with pytest.raises(WorkspaceError):
        ws.run(cmd)


@pytest.mark.parametrize("cmd,kind,args", [
    ("pytest", "pytest", []), ("pytest -q", "pytest", ["-q"]), ("python -m pytest -x -v tests/test_a.py", "pytest", ["-x", "-v", "tests/test_a.py"]),
    ("pytest -k foo_bar tests", "pytest", ["-k", "foo_bar", "tests"]), ("pytest tests/unit", "pytest", ["tests/unit"]), ("pytest tests/test_a.py::test_x", "pytest", ["tests/test_a.py::test_x"]),
])
def test_command_allowlist_accepts(ws, cmd, kind, args):
    assert ws.parse_command(cmd) == (kind, args)


def test_run_off_mode_skips_pytest_but_allows_syntax_checks(ws):
    ws.write("a.py", "x = 1\n")
    ws.write("bad.py", "def x(:\n")
    off = ws.run("pytest -q")
    assert off["skipped"] and off["exit_code"] is None and "비활성" in off["stderr"]
    assert ws.run("python -m py_compile a.py")["exit_code"] == 0
    assert ws.run("python -m py_compile bad.py")["exit_code"] != 0
    assert not list(ws.root.rglob("*.pyc")) and not (ws.root / "__pycache__").exists()  # 문법 검사는 파일을 남기지 않는다


def test_run_local_executes_pytest_with_stripped_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SERVER-SECRET")
    monkeypatch.setenv("SOME_TOKEN", "leak-me")
    w = Workspace(tmp_path / "x", exec_mode="local", exec_timeout=30)
    w.write("tests/test_env.py", "import os\n\ndef test_secrets_not_visible():\n    assert 'ANTHROPIC_API_KEY' not in os.environ\n    assert 'SOME_TOKEN' not in os.environ\n\n\ndef test_ok():\n    assert 1 + 1 == 2\n")
    res = w.run("pytest -q")
    assert res["exit_code"] == 0 and "2 passed" in res["stdout"] and res["mode"] == "local", res
    w.write("tests/test_fail.py", "def test_bad():\n    assert 1 == 2\n")
    bad = w.run("pytest -q")
    assert bad["exit_code"] == 1 and "1 failed" in bad["stdout"]


def test_run_local_kills_runaway_tests(tmp_path):
    w = Workspace(tmp_path / "t", exec_mode="local", exec_timeout=5)
    w.write("tests/test_slow.py", "import time\n\ndef test_sleep():\n    time.sleep(60)\n")
    t0 = time.time()
    res = w.run("pytest -q")
    assert res["timed_out"] and res["exit_code"] is None and time.time() - t0 < 20


def test_run_local_blocks_runaway_file_writes(tmp_path):
    w = Workspace(tmp_path / "f", exec_mode="local", exec_timeout=20)
    w.write("tests/test_big.py", "def test_big():\n    with open('big.bin', 'wb') as f:\n        f.write(b'x' * (40 << 20))\n")
    res = w.run("pytest -q")
    assert res["exit_code"] != 0  # RLIMIT_FSIZE(20MB) 초과


# ======================================================================= MCP (stdio 실제 프로토콜)
def test_mcp_roundtrip_and_cleanup():
    async def go():
        async with McpWorkspace(Config()) as m:
            assert m.tools == ["list_project_files", "read_project_file", "run_command", "write_project_file"]
            root = m.root
            await m.write("app/main.py", "print('hi')\n")
            assert (root / "app" / "main.py").read_text() == "print('hi')\n"  # 실제 디스크에 기록됨
            assert await m.read("app/main.py") == "print('hi')\n"
            assert [f["path"] for f in await m.list()] == ["app/main.py"]
            assert (await m.run("python -m py_compile app/main.py"))["exit_code"] == 0
            for call in (lambda: m.write("../evil.py", "x"), lambda: m.read("/etc/passwd"), lambda: m.run("rm -rf /"), lambda: m.run("pytest; id")):
                with pytest.raises(McpToolError):
                    await call()
            assert not (root.parent / "evil.py").exists() and m.calls == 8
            return root
        # (정리 확인은 아래)
    root = run(go())
    assert not root.exists()


def test_mcp_server_does_not_receive_api_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SERVER-SECRET")

    async def go():
        async with McpWorkspace(Config()) as m:
            await m.write("tests/test_env.py", "import os\n\ndef test_x():\n    assert 'ANTHROPIC_API_KEY' not in os.environ\n")
            return await m.run("python -m py_compile tests/test_env.py")

    assert run(go())["exit_code"] == 0
    # 서버 프로세스 환경 자체에도 키가 없음을 직접 확인
    async def env_of_server():
        async with McpWorkspace(Config(factory_exec="local")) as m:
            await m.write("tests/test_srv.py", "import os\n\ndef test_x():\n    assert not any('SECRET' in v for v in os.environ.values())\n")
            return await m.run("pytest -q")
    assert run(env_of_server())["exit_code"] == 0


# ======================================================================= 파이프라인: MCP + 실제 테스트 실행 + 자가 치유
@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def job_events(client, request="할 일 관리 웹앱을 만들어 줘 (테스트)"):
    r = client.post("/api/factory/jobs", data={"request": request})
    assert r.status_code == 201, r.text
    jid, ev = r.json()["job_id"], []
    with client.stream("GET", f"/api/jobs/{jid}/events") as s:
        for line in s.iter_lines():
            if line.startswith("data: "):
                ev.append(json.loads(line[6:]))
    return jid, ev


def msgs(ev, agent=None):
    return [e["message"] for e in ev if e["type"] == "log" and (agent is None or e["agent"] == agent)]


def test_pipeline_writes_via_mcp_and_verifies_disk(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    jid, ev = job_events(client)
    assert ev[-1]["type"] == "done"
    assert any("MCP 서버 연결" in m and "write_project_file" in m for m in msgs(ev, "ceo"))
    assert sum("→ MCP write_project_file" in m for m in msgs(ev, "development")) == 7
    assert any("디스크 대조 통과" in m for m in msgs(ev, "supervisor"))
    sw = ev[-1]["software"]
    assert sw["mcp"]["enabled"] and sw["mcp"]["calls"] > 10 and sw["exec"]["ran"] is False
    assert not list(Path(os.environ.get("TMPDIR", "/tmp"), "boopang-workspaces").glob("job-*"))  # 작업 종료 후 워크스페이스 삭제


def test_pipeline_runs_real_tests_and_self_heals_from_test_failure(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.setenv("BOOPANG_FACTORY_EXEC", "local")
    jid, ev = job_events(client)
    assert ev[-1]["type"] == "done"
    qa = msgs(ev, "qa")
    assert any("pytest 실패" in m for m in qa)            # 결함 있는 첫 버전: 실제 테스트가 실패
    assert any("pytest 통과" in m for m in qa)            # 자가 치유 후: 실제 테스트가 통과
    assert any("⚠" in m and "local" in m for m in msgs(ev, "ceo"))   # 비격리 실행 경고
    sw = ev[-1]["software"]
    assert sw["loops"] == 1 and not sw["escalated"] and sw["exec"] == {**sw["exec"], "mode": "local", "ran": True, "passed": True, "runs": 2}
    report = client.get(f"/api/jobs/{jid}/file", params={"path": "FACTORY_REPORT.md"}).text
    assert "테스트 실행(local 모드): 통과" in report


def test_pipeline_falls_back_to_memory_when_mcp_unavailable(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")

    async def boom(self):
        raise RuntimeError("no mcp")

    monkeypatch.setattr(McpWorkspace, "__aenter__", boom)
    jid, ev = job_events(client)
    assert ev[-1]["type"] == "done" and ev[-1]["software"]["mcp"]["enabled"] is False
    assert any("MCP 서버를 시작하지 못했습니다" in m and "메모리 모드" in m for m in msgs(ev, "ceo"))


def test_mcp_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.setenv("BOOPANG_FACTORY_MCP", "false")
    _, ev = job_events(client)
    assert ev[-1]["software"]["mcp"] == {"enabled": False, "calls": 0, "tools": []}


# ======================================================================= 가짜 Ollama 서버 (실제 HTTP 경로 end-to-end)
class FakeOllama:
    def __init__(self, models=("qwen2.5-coder:7b-instruct",)):
        self.requests, self.models = [], list(models)
        api = FastAPI()

        @api.get("/api/tags")
        def tags():
            return {"models": [{"name": m} for m in self.models]}

        @api.post("/api/chat")
        async def chat(req: Request):
            body = await req.json()
            self.requests.append(body)
            sys_, user = body["messages"][0]["content"], body["messages"][-1]["content"]
            if "senior product manager" in sys_:
                out = demo.PRD
            elif "software architect" in sys_:
                out = demo.ARCH
            elif "ONE file" in sys_:
                path = user.rsplit("path: ", 1)[1].split("\n")[0]
                out = {"content": demo.FILES[path]}
            elif "repair" in sys_:
                out = {"files": [{"path": "app/db.py", "content": demo.DB_V2}], "notes": "fixed"}
            else:
                out = {"content": "# readme\n"}
            return {"message": {"role": "assistant", "content": json.dumps(out)}, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 50}

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


def test_end_to_end_with_fake_ollama_server(client, monkeypatch):
    with FakeOllama() as fake:
        monkeypatch.setenv("BOOPANG_FACTORY_ENABLED", "true")
        monkeypatch.setenv("BOOPANG_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("BOOPANG_OLLAMA_HOST", fake.url)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        st = client.get("/api/factory/status").json()
        assert st["mode"] == "llm" and st["provider"] == "ollama" and st["cost"] == "free-local" and st["model"] == "qwen2.5-coder:7b-instruct"
        jid, ev = job_events(client)
        assert ev[-1]["type"] == "done"
        sw = ev[-1]["software"]
        assert sw["provider"] == "ollama" and sw["model"] == "qwen2.5-coder:7b-instruct" and sw["loops"] == 1 and not sw["escalated"]
        assert sw["input_tokens"] == 100 * sw["llm_calls"] and sw["output_tokens"] == 50 * sw["llm_calls"]
        # 모든 요청이 Ollama 규격: 모델/스키마/옵션 + 스트리밍 끔
        assert all(r["model"] == "qwen2.5-coder:7b-instruct" and r["stream"] is False and isinstance(r["format"], dict)
                   and r["options"]["num_ctx"] == 8192 for r in fake.requests)
        assert len(fake.requests) == sw["llm_calls"]
        assert "VALUES (?)" in client.get(f"/api/jobs/{jid}/file", params={"path": "app/db.py"}).text


def test_status_when_ollama_down_or_model_missing(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_ENABLED", "true")
    monkeypatch.setenv("BOOPANG_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("BOOPANG_OLLAMA_HOST", "http://127.0.0.1:9")  # 닫힌 포트
    st = client.get("/api/factory/status").json()
    assert st["mode"] == "unconfigured" and "ollama serve" in st["hint"]
    with FakeOllama(models=["llama3:8b"]) as fake:
        monkeypatch.setenv("BOOPANG_OLLAMA_HOST", fake.url)
        st = client.get("/api/factory/status").json()
        assert st["mode"] == "unconfigured" and "ollama pull qwen2.5-coder:7b-instruct" in st["hint"]


def test_ollama_unreachable_job_reports_helpful_error(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_ENABLED", "true")
    monkeypatch.setenv("BOOPANG_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("BOOPANG_OLLAMA_HOST", "http://127.0.0.1:9")
    _, ev = job_events(client)
    assert ev[-1]["type"] == "error" and "ollama serve" in ev[-1]["message"]


# ======================================================================= Docker 샌드박스 (이미지가 있을 때만)
def _sandbox_ready():
    import shutil, subprocess
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", "boopang-sandbox"], capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(not _sandbox_ready(), reason="docker 데몬 또는 boopang-sandbox 이미지가 없음 (deploy/build_sandbox.sh)")
def test_docker_sandbox_isolation(tmp_path):
    w = Workspace(tmp_path / "d", exec_mode="docker", exec_timeout=60)
    w.write("tests/test_iso.py", """import os, socket, pytest

def test_no_network():
    s = socket.socket(); s.settimeout(3)
    with pytest.raises(OSError):
        s.connect(('1.1.1.1', 80))

def test_not_root_and_readonly_rootfs():
    assert os.getuid() == 65534
    with pytest.raises(OSError):
        open('/usr/evil', 'w')

def test_host_secrets_absent():
    assert 'ANTHROPIC_API_KEY' not in os.environ
    assert not os.path.exists('/home/user')

def test_workspace_writable():
    open('/work/out.txt', 'w').write('ok')
""")
    res = w.run("pytest -q")
    assert res["exit_code"] == 0 and "4 passed" in res["stdout"], res


@pytest.mark.skipif(not _sandbox_ready(), reason="docker 데몬 또는 boopang-sandbox 이미지가 없음")
def test_pipeline_with_docker_sandbox_self_heals(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.setenv("BOOPANG_FACTORY_EXEC", "docker")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-HOST-SECRET")
    _, ev = job_events(client)
    assert ev[-1]["type"] == "done"
    qa = msgs(ev, "qa")
    assert any("pytest 실패" in m for m in qa) and any("pytest 통과 (docker)" in m for m in qa)
    sw = ev[-1]["software"]
    assert sw["exec"]["mode"] == "docker" and sw["exec"]["passed"] is True and sw["loops"] == 1
    assert any("Docker 샌드박스" in m for m in msgs(ev, "ceo"))
