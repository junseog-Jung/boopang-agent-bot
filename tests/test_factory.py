import io
import json
import os
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from boopang.config import Config
from boopang.factory import demo
from boopang.factory.checks import ERROR, WARN, qa_check, security_scan
from boopang.factory.llm import AnthropicLLM, LLMError, ScriptedLLM
from boopang.factory.project import Project, ProjectError, validate_path
from boopang.web.app import app, manager


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOOPANG_STEP_DELAY", "0")
    manager.jobs.clear()
    manager.hourly = []


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def run_job(client, request="재고 관리 웹앱을 만들어 줘 (FastAPI)", expect=201):
    r = client.post("/api/factory/jobs", data={"request": request})
    assert r.status_code == expect, r.text
    if expect != 201:
        return None, []
    jid = r.json()["job_id"]
    events = []
    with client.stream("GET", f"/api/jobs/{jid}/events") as s:
        for line in s.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return jid, events


def logs(events, agent=None):
    return [e["message"] for e in events if e["type"] == "log" and (agent is None or e["agent"] == agent)]


# ---------------------------------------------------------------- 프로젝트 컨테이너
@pytest.mark.parametrize("path", [
    "../etc/passwd", "/etc/passwd", "a/../../b.py", "a//b.py", ".env", "config/.env", ".git/config", "node_modules/x.js",
    "run.exe", "x.bin", "a/b/c/d/e/f/g.py", "한글.py", "a b.py", "dir/", "", ".secret", "a\\b.py", "x" * 200 + ".py",
])
def test_unsafe_paths_rejected(path):
    with pytest.raises(ProjectError):
        validate_path(path)


@pytest.mark.parametrize("path", ["app/main.py", "Dockerfile", ".gitignore", ".env.example", "static/index.html", "tests/test_a.py", "package.json"])
def test_safe_paths_allowed(path):
    assert validate_path(path) == path


def test_project_limits_and_cleaning():
    p = Project(max_files=2, max_file_bytes=10, max_total_bytes=15)
    p.set("a.py", "x = 1\r\n")
    assert p.files["a.py"] == "x = 1\n"
    with pytest.raises(ProjectError, match="너무 큽니다"):
        p.set("b.py", "x" * 11)
    p.set("b.py", "y" * 9)
    with pytest.raises(ProjectError, match="개수"):
        p.set("c.py", "z")
    with pytest.raises(ProjectError, match="전체 용량"):
        p.set("a.py", "w" * 8)  # 8 + 9 = 17 > 15
    with pytest.raises(ProjectError):
        p.set("a.py", 123)
    assert p.clean("a\x00b") == "ab"


def test_zip_is_safe_and_reproducible():
    p = Project(5, 1000, 5000)
    p.set("app/main.py", "print(1)\n")
    p.set("README.md", "# hi\n")
    a, b = p.to_zip("My Project!"), p.to_zip("My Project!")
    assert a == b
    zf = zipfile.ZipFile(io.BytesIO(a))
    names = zf.namelist()
    assert names == ["my-project/README.md", "my-project/app/main.py"]
    assert all(not n.startswith(("/", "..")) for n in names)
    assert all((i.external_attr >> 16) & 0o111 == 0 for i in zf.infolist())  # 실행 비트 없음


# ---------------------------------------------------------------- 정적 검사
def test_qa_findings():
    f = qa_check({
        "a.py": "def x(:\n  pass\n", "b.json": "{bad", "c.js": "foo(;\n", "d.html": "<div><span></div>",
        "e.py": "import requests\nimport os\n", "requirements.txt": "fastapi\n", "empty.txt": "  \n",
    })
    codes = {(x.path, x.code) for x in f}
    assert ("a.py", "PY_SYNTAX") in codes and ("b.json", "JSON_SYNTAX") in codes
    assert ("c.js", "JS_SYNTAX") in codes and ("e.py", "UNDECLARED_IMPORT") in codes
    assert ("empty.txt", "EMPTY_FILE") in codes and any(x.code == "HTML_TAG" for x in f)
    assert not any(x.code == "UNDECLARED_IMPORT" and "os" in x.message for x in f)


@pytest.mark.parametrize("src,code", [
    ("eval('1')", "PY_EVAL"), ("exec('x=1')", "PY_EVAL"), ("import os\nos.system('ls')", "PY_OS_COMMAND"),
    ("import subprocess\nsubprocess.run('ls', shell=True)", "PY_SHELL_TRUE"),
    ("import pickle\npickle.loads(b'')", "PY_UNSAFE_DESERIALIZE"), ("import yaml\nyaml.load(s)", "PY_YAML_LOAD"),
    ("c.execute(f'select {x}')", "SQL_INJECTION"), ("c.execute('select ' + x)", "SQL_INJECTION"),
    ("c.execute('select %s' % x)", "SQL_INJECTION"), ("c.execute('select {}'.format(x))", "SQL_INJECTION"),
    ("import requests\nrequests.get(u, verify=False)", "TLS_VERIFY_OFF"),
    ("API_KEY = 'abcd1234efgh5678'", "HARDCODED_SECRET"), ("k = 'sk-ant-api03-abcdefghijklmnopqrstuvwx'", "HARDCODED_SECRET"),
])
def test_python_security_errors(src, code):
    assert any(f.code == code and f.severity == ERROR for f in security_scan({"m.py": src + "\n"}))


@pytest.mark.parametrize("src", [
    "c.execute('select * from t where id = ?', (x,))", "c.execute('select 1')", "k = os.environ['API_KEY']",
    "password = 'changeme-example'", "x = 1", "import yaml\nyaml.load(s, Loader=yaml.SafeLoader)",
])
def test_python_security_clean(src):
    assert not [f for f in security_scan({"m.py": src + "\n"}) if f.severity == ERROR]


def test_js_security():
    f = security_scan({"a.js": "el.innerHTML = name;\nel.innerHTML = '<b>ok</b>';\neval(x);\nel.textContent = y;\n"})
    assert [(x.line, x.code) for x in f if x.severity == ERROR] == [(1, "XSS_INNERHTML"), (3, "JS_EVAL")]
    assert [f for f in security_scan({"b.py": "app.add_middleware(CORSMiddleware, allow_origins=['*'])\n"}) if f.code == "CORS_ANY"]


def test_example_env_not_flagged_as_secret():
    assert not security_scan({".env.example": "API_KEY='put-your-real-key-here-1234'\n"})


# ---------------------------------------------------------------- LLM 어댑터 (SDK 모킹)
class StubClient:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.kwargs = resp, exc, None
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.resp


def resp(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=11, output_tokens=7))


def test_anthropic_llm_request_shape_and_usage():
    import asyncio
    stub = StubClient(resp('{"content": "x"}'))
    llm = AnthropicLLM("claude-opus-5-5", "high", max_calls=3, client=stub)
    out = asyncio.run(llm.json("SYS", "USER", {"type": "object"}, label="t"))
    assert out == {"content": "x"} and (llm.calls, llm.input_tokens, llm.output_tokens) == (1, 11, 7)
    kw = stub.kwargs
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == "SYS" and kw["messages"] == [{"role": "user", "content": "USER"}]
    assert kw["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert "temperature" not in kw and "tool_choice" not in kw and "thinking" not in kw  # Opus 5.5 에서 400 이 되는 파라미터 미사용


@pytest.mark.parametrize("response,msg", [
    (resp("{}", "refusal"), "거절"), (resp("{}", "max_tokens"), "잘렸"), (resp("not json"), "JSON"), (resp("[1]"), "형식"),
])
def test_anthropic_llm_error_mapping(response, msg):
    import asyncio
    llm = AnthropicLLM("m", "medium", 5, client=StubClient(response))
    with pytest.raises(LLMError, match=msg):
        asyncio.run(llm.json("s", "u", {}, label="t"))


def test_anthropic_llm_api_errors_do_not_leak_secrets():
    import asyncio
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    for exc, msg in [(anthropic.AuthenticationError("sk-ant-SECRET", response=httpx2.Response(401, request=req), body=None), "인증"),
                     (anthropic.RateLimitError("sk-ant-SECRET", response=httpx2.Response(429, request=req), body=None), "한도"),
                     (anthropic.InternalServerError("sk-ant-SECRET", response=httpx2.Response(500, request=req), body=None), "HTTP 500")]:
        llm = AnthropicLLM("m", "medium", 5, client=StubClient(exc=exc))
        with pytest.raises(LLMError, match=msg) as info:
            asyncio.run(llm.json("s", "u", {}, label="t"))
        assert "SECRET" not in str(info.value)


def test_llm_call_budget():
    import asyncio
    llm = ScriptedLLM(lambda l, u: {}, max_calls=1)
    asyncio.run(llm.json("s", "u", {}, label="a"))
    with pytest.raises(LLMError, match="한도"):
        asyncio.run(llm.json("s", "u", {}, label="b"))


# ---------------------------------------------------------------- 파이프라인 (데모 모드 · E2E)
def test_status_and_gating(client, monkeypatch):
    s = client.get("/api/factory/status").json()
    assert s["mode"] == "disabled" and "BOOPANG_FACTORY" in s["hint"]
    assert client.post("/api/factory/jobs", data={"request": "x" * 20}).status_code == 403
    monkeypatch.setenv("BOOPANG_FACTORY_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert client.get("/api/factory/status").json()["mode"] == "unconfigured"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    s = client.get("/api/factory/status").json()
    assert s["mode"] == "llm" and s["model"] == "claude-opus-5-5" and "sk-ant" not in json.dumps(s)
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    assert client.get("/api/factory/status").json()["mode"] == "demo"
    assert client.get("/api/agents?mode=software").json()[2]["role"].startswith("자연어")


def test_demo_end_to_end_with_self_healing(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    jid, ev = run_job(client)
    assert ev[-1]["type"] == "done"
    final = {}
    for e in ev:
        if e["type"] == "agent":
            final[e["agent"]] = e["state"]
    assert len(final) == 12 and all(s in ("done", "skipped") for s in final.values())
    assert any("데모 모드" in m for m in logs(ev, "ceo"))
    # 보안 스캔이 의도적 결함을 찾고, 자가 치유 1회로 해결
    assert any("SQL_INJECTION" in m for m in logs(ev, "security"))
    assert any("재작업 루프 1/3" in m for m in logs(ev, "loop"))
    assert any("모든 ERROR 해결" in m for m in logs(ev, "refinement"))
    sw = ev[-1]["software"]
    assert sw["loops"] == 1 and not sw["escalated"] and sw["findings"]["ERROR"] == 0 and sw["demo"]
    # 파일 뷰어 API
    tree = client.get(f"/api/jobs/{jid}/files").json()
    paths = [t["path"] for t in tree]
    assert {"README.md", "FACTORY_REPORT.md", ".gitignore", "app/db.py", "app/main.py"} <= set(paths)
    db = client.get(f"/api/jobs/{jid}/file", params={"path": "app/db.py"})
    assert db.headers["content-type"].startswith("text/plain") and "VALUES (?)" in db.text and "{title}" not in db.text
    assert client.get(f"/api/jobs/{jid}/file", params={"path": "../../etc/passwd"}).status_code == 404
    assert client.get(f"/api/jobs/{jid}/file", params={"path": "nope.py"}).status_code == 404
    # ZIP
    z = client.get(f"/api/jobs/{jid}/download/zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip" and "demo-todo.zip" in z.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert all(n.startswith("demo-todo/") for n in names) and "demo-todo/app/db.py" in names
    report = zipfile.ZipFile(io.BytesIO(z.content)).read("demo-todo/FACTORY_REPORT.md").decode()
    assert "데모 모드" in report and "실행·테스트되지 않았습니다" in report


def test_generated_demo_project_actually_runs(client, monkeypatch, tmp_path):
    """팩토리가 만든 ZIP 을 풀어 그 안의 테스트를 실제로 실행 — 정적 점검 통과 ≠ 동작이므로 별도로 확인."""
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    jid, _ = run_job(client)
    zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{jid}/download/zip").content)).extractall(tmp_path)
    proj = tmp_path / "demo-todo"
    # README 의 `pytest -q` 와 같은 조건: 콘솔 스크립트처럼 현재 디렉터리가 sys.path 에 없는 상태로 실행
    r = subprocess.run([sys.executable, "-I", "-c", "import sys, pytest; sys.exit(pytest.console_main())", "-q", "-p", "no:cacheprovider"],
                       cwd=proj, capture_output=True, text=True, timeout=120, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- 시나리오 (ScriptedLLM)
def scripted(monkeypatch, handler, max_calls=100):
    holder = {}

    def make(cfg):
        holder["llm"] = ScriptedLLM(handler, max_calls)
        return holder["llm"]

    monkeypatch.setattr("boopang.web.app.make_llm", make)
    monkeypatch.setenv("BOOPANG_FACTORY_ENABLED", "true")
    return holder


def base_handler(label, user, **over):
    if label in over:
        return over[label]
    if label == "prd":
        return demo.PRD
    if label == "arch":
        return demo.ARCH
    if label.startswith("file:"):
        return {"content": demo.FILES[label[5:]]}
    if label == "readme":
        return {"content": "# readme\n"}
    raise AssertionError(label)


def test_refusal_stops_job(client, monkeypatch):
    scripted(monkeypatch, lambda l, u: base_handler(l, u, prd={**demo.PRD, "refused": True, "refusal_reason": "악성코드 요청"}))
    jid, ev = run_job(client, "비밀번호를 훔치는 프로그램을 만들어 줘 (테스트)")
    assert ev[-1]["type"] == "error" and "안전 정책" in ev[-1]["message"] and "악성코드" in ev[-1]["message"]
    assert client.get(f"/api/jobs/{jid}/download/zip").status_code == 409
    assert client.get(f"/api/jobs/{jid}/files").status_code == 409


def test_short_request_rejected_without_llm_call(client, monkeypatch):
    h = scripted(monkeypatch, lambda l, u: base_handler(l, u))
    _, ev = run_job(client, "앱")
    assert ev[-1]["type"] == "error" and "너무 짧" in ev[-1]["message"] and h["llm"].calls == 0


def test_escalation_after_three_failed_loops(client, monkeypatch):
    h = scripted(monkeypatch, lambda l, u: (
        {"files": [{"path": "app/db.py", "content": demo.DB_V1}], "notes": "고치지 못함"} if l.startswith("refine")
        else base_handler(l, u)))
    jid, ev = run_job(client)
    assert ev[-1]["type"] == "done"
    sw = ev[-1]["software"]
    assert sw["loops"] == 3 and sw["escalated"] and sw["findings"]["ERROR"] >= 1
    assert any("에스컬레이션" in m for m in logs(ev, "loop"))
    assert sum(1 for l in h["llm"].log if l.startswith("refine")) == 3
    states = {e["agent"]: e["state"] for e in ev if e["type"] == "agent"}
    assert states["refinement"] == "error" and states["supervisor"] == "done"
    report = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{jid}/download/zip").content)).read("demo-todo/FACTORY_REPORT.md").decode()
    assert "사람의 검토 필요" in report and "SQL_INJECTION" in report


def test_refine_cannot_touch_unlisted_or_new_files(client, monkeypatch):
    scripted(monkeypatch, lambda l, u: (
        {"files": [{"path": "app/db.py", "content": demo.DB_V2}, {"path": "evil.py", "content": "import os"},
                   {"path": "../x.py", "content": "x"}], "notes": "ok"} if l.startswith("refine") else base_handler(l, u)))
    jid, ev = run_job(client)
    assert ev[-1]["type"] == "done"
    assert sum("무시했습니다" in m for m in logs(ev, "refinement")) == 2
    assert "evil.py" not in [t["path"] for t in client.get(f"/api/jobs/{jid}/files").json()]


@pytest.mark.parametrize("arch_files,msg", [
    ([{"path": "../../etc/x.py", "purpose": "p"}], "허용되지 않는"),
    ([{"path": "a.py", "purpose": "p"}, {"path": "a.py", "purpose": "p"}], "중복"),
    ([{"path": ".env", "purpose": "p"}], ".env"),
    ([], "생성할 파일이 없습니다"),
    ([{"path": f"f{i}.py", "purpose": "p"} for i in range(30)], "한도"),
])
def test_bad_architecture_rejected(client, monkeypatch, arch_files, msg):
    scripted(monkeypatch, lambda l, u: base_handler(l, u, arch={**demo.ARCH, "files": arch_files}))
    _, ev = run_job(client)
    assert ev[-1]["type"] == "error" and msg in ev[-1]["message"]


def test_oversized_generated_file_rejected(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_MAX_FILE_BYTES", "500")
    scripted(monkeypatch, lambda l, u: base_handler(l, u))
    _, ev = run_job(client)
    assert ev[-1]["type"] == "error" and "너무 큽니다" in ev[-1]["message"]


def test_call_budget_checked_up_front(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_MAX_CALLS", "8")
    scripted(monkeypatch, lambda l, u: base_handler(l, u), max_calls=8)
    _, ev = run_job(client)
    assert ev[-1]["type"] == "error" and "호출 한도" in ev[-1]["message"]


def test_readme_falls_back_when_budget_exhausted(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_MAX_CALLS", "13")  # 사전 점검(파일 7 + 2 + 1 + 3 = 13)은 통과
    # LLM 어댑터 자체의 상한은 더 작게: prd, arch, 파일 7개, refine 1회 = 10회 → README 호출(11번째)이 한도 초과
    scripted(monkeypatch, lambda l, u: (
        {"files": [{"path": "app/db.py", "content": demo.DB_V2}], "notes": "fix"} if l.startswith("refine") else base_handler(l, u)),
        max_calls=10)
    jid, ev = run_job(client)
    assert ev[-1]["type"] == "done"
    readme = client.get(f"/api/jobs/{jid}/file", params={"path": "README.md"}).text
    assert "기본 템플릿" in " ".join(logs(ev, "docs")) and "## 파일 구조" in readme


def test_hourly_limit(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    monkeypatch.setenv("BOOPANG_FACTORY_MAX_JOBS_PER_HOUR", "2")
    run_job(client)
    run_job(client)
    r = client.post("/api/factory/jobs", data={"request": "x" * 20})
    assert r.status_code == 429


def test_request_length_and_control_chars(client, monkeypatch):
    monkeypatch.setenv("BOOPANG_FACTORY_DEMO", "true")
    assert client.post("/api/factory/jobs", data={"request": "x" * 4001}).status_code == 413
    jid, _ = run_job(client, "\x00\x1b[31m" + "재고 관리 웹앱 만들어줘")
    assert "\x00" not in manager.get(jid).instruction and "\x1b" not in manager.get(jid).instruction


def test_llm_error_midway_reports_error(client, monkeypatch):
    scripted(monkeypatch, lambda l, u: LLMError("Claude API 요청 한도를 초과했습니다.") if l == "arch" else base_handler(l, u))
    _, ev = run_job(client)
    assert ev[-1]["type"] == "error" and "한도" in ev[-1]["message"]
    assert {e["agent"]: e["state"] for e in ev if e["type"] == "agent"}["architecture"] == "error"
