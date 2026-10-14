from fastapi.testclient import TestClient

from boopang.sample import sample_xlsx
from boopang.web.app import app

client = TestClient(app)
XLSX = ("sample.xlsx", sample_xlsx(), "application/octet-stream")


def test_health_and_index_security_headers():
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "부팡" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_process_ok():
    r = client.post("/api/process", files={"file": XLSX})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["order_count"] == 9 and body["schema_version"] == "1.0"


def test_convert_formats():
    for fmt, ctype in (("csv", "text/csv"), ("json", "application/json"), ("xlsx", "spreadsheetml")):
        r = client.post(f"/api/convert?format={fmt}", files={"file": XLSX})
        assert r.status_code == 200 and ctype in r.headers["content-type"]
        assert "attachment" in r.headers["content-disposition"]
    assert client.post("/api/convert?format=exe", files={"file": XLSX}).status_code == 400


def test_rejects_bad_extension_garbage_and_oversize(monkeypatch):
    assert client.post("/api/process", files={"file": ("a.exe", b"x")}).status_code == 415
    assert client.post("/api/process", files={"file": ("a.xlsx", b"garbage")}).status_code == 422
    monkeypatch.setenv("BOOPANG_MAX_UPLOAD_BYTES", "10")
    assert client.post("/api/process", files={"file": ("a.csv", b"x" * 100)}).status_code == 413


def test_static_path_traversal_blocked():
    assert client.get("/static/..%2fapp.py").status_code == 404
    assert client.get("/static/app.js").status_code == 200


def test_sample_download():
    r = client.get("/api/sample")
    assert r.status_code == 200 and r.content[:2] == b"PK"
