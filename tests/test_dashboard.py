import json
import time

import pytest
from fastapi.testclient import TestClient

from boopang.sample import sample_xlsx
from boopang.web.app import app, manager
from boopang.web.jobs import AGENTS

# `with` 로 하나의 이벤트 루프를 유지해야 백그라운드 태스크가 요청 종료 후에도 계속 실행된다 (uvicorn 과 동일한 조건)
with TestClient(app) as _c:
    pass


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv("BOOPANG_STEP_DELAY", "0")
    manager.jobs.clear()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def start(client, content=None, name="s.xlsx", instruction="테스트 지시"):
    r = client.post("/api/jobs", files={"file": (name, content if content is not None else sample_xlsx())},
                    data={"instruction": instruction})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def read_events(client, job_id):
    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_dashboard_pages_served(client):
    assert "text/html" in client.get("/").headers["content-type"] and "관제" in client.get("/").text
    assert client.get("/converter").status_code == 200
    assert client.get("/static/dashboard.js").status_code == 200


def test_agents_endpoint_has_twelve(client):
    agents = client.get("/api/agents").json()
    assert len(agents) == 12 and {a["key"] for a in agents} == {a[0] for a in AGENTS}


def test_full_job_stream_and_downloads(client):
    job_id = start(client)
    events = read_events(client, job_id)
    types = [e["type"] for e in events]
    assert types[-1] == "done" and "error" not in types
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    # 12개 에이전트가 모두 최종 상태에 도달 (refinement 는 오류 라인이 있어 done)
    final = {}
    for e in events:
        if e["type"] == "agent":
            final[e["agent"]] = e["state"]
    assert set(final) == {a[0] for a in AGENTS}
    assert all(s in ("done", "skipped") for s in final.values())
    logs = [e["message"] for e in events if e["type"] == "log"]
    assert any("테스트 지시" in m for m in logs) and any("합계 대조 통과" in m for m in logs)
    assert any(e["level"] == "error" for e in events if e["type"] == "log")  # 샘플의 ERROR 이슈 보고
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["done"] and status["downloads"] == ["csv", "json", "xlsx"]
    assert status["summary"]["order_count"] == 9
    csv = client.get(f"/api/jobs/{job_id}/download/csv")
    assert csv.status_code == 200 and csv.content.startswith(b"\xef\xbb\xbfORDER_NO")
    assert json.loads(client.get(f"/api/jobs/{job_id}/download/json").content)["schema_version"] == "1.0"
    assert client.get(f"/api/jobs/{job_id}/download/xlsx").content[:2] == b"PK"


def test_resume_with_last_event_id(client):
    job_id = start(client)
    all_events = read_events(client, job_id)
    with client.stream("GET", f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "5"}) as r:
        got = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    assert got[0]["seq"] == 6 and len(got) == len(all_events) - 5


def test_bad_file_reports_error_event_and_blocks_download(client):
    job_id = start(client, b"not a zip", "bad.xlsx")
    events = read_events(client, job_id)
    assert events[-1]["type"] == "error"
    states = {e["agent"]: e["state"] for e in events if e["type"] == "agent"}
    assert states["ideation"] == "error"
    assert client.get(f"/api/jobs/{job_id}/download/csv").status_code == 409


def test_missing_required_columns_error(client):
    job_id = start(client, "품목명,비고\nA,B\n".encode(), "x.csv")
    events = read_events(client, job_id)
    assert events[-1]["type"] == "error" and "필수 컬럼" in events[-1]["message"]


def test_upload_validation_and_unknown_job(client):
    assert client.post("/api/jobs", files={"file": ("a.exe", b"x")}).status_code == 415
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/" + "A" * 22).status_code == 404
    assert client.get("/api/jobs/" + "A" * 22 + "/events").status_code == 404
    job_id = start(client)
    read_events(client, job_id)
    assert client.get(f"/api/jobs/{job_id}/download/exe").status_code == 400


def test_instruction_sanitized_and_truncated(client):
    job_id = start(client, instruction="\x00\x1b[31m빨강" + "x" * 5000)
    job = manager.get(job_id)
    assert "\x00" not in job.instruction and "\x1b" not in job.instruction and len(job.instruction) == 1000
    read_events(client, job_id)


def test_formula_injection_flagged_by_security_agent(client):
    csv = '주문번호,SKU,품목명,주문수량,단가\nA1,S1,"=1+1",1,100\n'.encode()
    events = read_events(client, start(client, csv, "f.csv"))
    sec = [e["message"] for e in events if e["type"] == "log" and e["agent"] == "security"]
    assert any("1건" in m for m in sec)


def test_concurrency_limit_returns_429(client):
    from boopang.web.jobs import Job
    for i in range(3):
        manager.jobs[f"running-job-id-{i:04d}"] = Job(id=f"running-job-id-{i:04d}", instruction="", filename="x")
    r = client.post("/api/jobs", files={"file": ("s.xlsx", sample_xlsx())})
    assert r.status_code == 429


def test_expired_jobs_purged(client):
    job_id = start(client)
    read_events(client, job_id)
    manager.jobs[job_id].created -= 10_000
    start(client)  # create() 시 purge
    assert job_id not in manager.jobs


def test_step_delay_env_validation(client, monkeypatch):
    from boopang.config import Config
    monkeypatch.setenv("BOOPANG_STEP_DELAY", "9")
    with pytest.raises(ValueError, match="STEP_DELAY"):
        Config.from_env()
    monkeypatch.setenv("BOOPANG_STEP_DELAY", "abc")
    with pytest.raises(ValueError, match="STEP_DELAY"):
        Config.from_env()


def test_dashboard_assets_and_controls(client):
    html = client.get("/").text
    for needle in ('id="mode-software"', 'id="request"', 'id="flow"', 'id="file-tree"', 'id="dl-zip"', "/static/avatars.js"):
        assert needle in html, needle
    js = client.get("/static/avatars.js")
    assert js.status_code == 200 and "Avatars" in js.text
    csp = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    # 인라인 style 속성이 없어야 CSP(style-src 'self')에서 아바타가 깨지지 않는다
    assert " style=" not in html and " style=" not in js.text
