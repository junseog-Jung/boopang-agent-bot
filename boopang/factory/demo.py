"""데모 모드(BOOPANG_FACTORY_DEMO=1): API 키 없이 파이프라인 전체(PRD→설계→생성→QA→자가 치유→ZIP)를 시연한다.

중요: 사용자의 요청과 **무관하게** 항상 같은 '할 일 관리' 샘플 프로젝트를 만든다. 실제 범용 생성이 아니다.
자가 치유 루프를 보여주기 위해 db.py 첫 버전에 SQL 인젝션 결함을 의도적으로 넣고, 리파인 단계에서 고친다.
"""
from __future__ import annotations

import json

from .llm import BaseLLM, LLMError

PRD = {
    "refused": False, "refusal_reason": "",
    "title": "데모 할 일 관리", "slug": "demo-todo",
    "summary": "[데모] 요청과 무관한 고정 샘플: FastAPI + SQLite 로 할 일을 추가/완료/삭제하는 작은 웹앱",
    "goals": ["할 일을 추가하고 완료/삭제한다", "브라우저에서 바로 사용한다"],
    "features": [
        {"name": "할 일 CRUD", "description": "목록 조회, 추가, 완료 토글, 삭제 REST API", "priority": "must"},
        {"name": "웹 UI", "description": "빌드 없는 단일 HTML 페이지", "priority": "must"},
    ],
    "tech_stack": {"frontend": "Vanilla HTML/JS", "backend": "FastAPI", "database": "SQLite", "other": "pytest"},
    "nonfunctional": ["파라미터 바인딩 SQL", "DOM 삽입은 textContent 사용"],
    "out_of_scope": ["사용자 인증", "다중 사용자"],
    "risks": ["데모용 샘플이므로 운영 용도로 사용하지 마세요"],
    "assumptions": ["단일 사용자 로컬 실행"],
}

ARCH = {
    "overview": "FastAPI 가 REST API 와 정적 HTML 을 제공하고 SQLite 파일에 저장한다.",
    "components": [{"name": "API", "responsibility": "라우팅/검증"}, {"name": "DB", "responsibility": "SQLite 접근"}],
    "data_model": [{"entity": "todo", "fields": ["id INTEGER PK", "title TEXT", "done INTEGER"]}],
    "api": [
        {"method": "GET", "path": "/api/todos", "description": "목록"},
        {"method": "POST", "path": "/api/todos", "description": "추가"},
        {"method": "PATCH", "path": "/api/todos/{id}", "description": "완료 토글"},
        {"method": "DELETE", "path": "/api/todos/{id}", "description": "삭제"},
    ],
    "ui_plan": [{"page": "/", "description": "입력창과 목록, 완료/삭제 버튼"}],
    "files": [
        {"path": "requirements.txt", "purpose": "의존성"},
        {"path": "pytest.ini", "purpose": "pytest 설정 (프로젝트 루트를 import 경로에 추가)"},
        {"path": "app/__init__.py", "purpose": "패키지"},
        {"path": "app/db.py", "purpose": "SQLite 접근 계층"},
        {"path": "app/main.py", "purpose": "FastAPI 앱과 라우트"},
        {"path": "static/index.html", "purpose": "웹 UI"},
        {"path": "tests/test_todos.py", "purpose": "API 테스트"},
    ],
    "run_commands": ["pip install -r requirements.txt", "uvicorn app.main:app --port 8000"],
    "test_commands": ["pytest -q"],
}

DB_V1 = '''import os
import sqlite3

DB_PATH = os.environ.get("TODO_DB", "todos.db")


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)")
    return conn


def list_todos():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT id, title, done FROM todos ORDER BY id")]


def add_todo(title):
    with connect() as c:
        cur = c.execute(f"INSERT INTO todos (title) VALUES ('{title}')")
        return {"id": cur.lastrowid, "title": title, "done": 0}


def toggle_todo(todo_id):
    with connect() as c:
        c.execute("UPDATE todos SET done = 1 - done WHERE id = ?", (todo_id,))
        row = c.execute("SELECT id, title, done FROM todos WHERE id = ?", (todo_id,)).fetchone()
        return dict(row) if row else None


def delete_todo(todo_id):
    with connect() as c:
        return c.execute("DELETE FROM todos WHERE id = ?", (todo_id,)).rowcount > 0
'''
DB_V2 = DB_V1.replace('''c.execute(f"INSERT INTO todos (title) VALUES ('{title}')")''', '''c.execute("INSERT INTO todos (title) VALUES (?)", (title,))''')

MAIN = '''from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db

app = FastAPI(title="Demo Todo")
INDEX = Path(__file__).resolve().parent.parent / "static" / "index.html"


class NewTodo(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.get("/api/todos")
def list_todos():
    return db.list_todos()


@app.post("/api/todos", status_code=201)
def add_todo(body: NewTodo):
    return db.add_todo(body.title.strip())


@app.patch("/api/todos/{todo_id}")
def toggle_todo(todo_id: int):
    todo = db.toggle_todo(todo_id)
    if todo is None:
        raise HTTPException(404, "not found")
    return todo


@app.delete("/api/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    if not db.delete_todo(todo_id):
        raise HTTPException(404, "not found")
'''

INDEX = '''<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>Demo Todo</title></head>
<body>
<h1>할 일</h1>
<form id="f"><input id="t" maxlength="200" required><button>추가</button></form>
<ul id="list"></ul>
<script>
async function load() {
  const res = await fetch("/api/todos");
  const items = await res.json();
  const ul = document.getElementById("list");
  ul.replaceChildren();
  for (const it of items) {
    const li = document.createElement("li");
    li.textContent = (it.done ? "[x] " : "[ ] ") + it.title + " ";
    const tog = document.createElement("button");
    tog.textContent = "완료";
    tog.onclick = async () => { await fetch("/api/todos/" + it.id, {method: "PATCH"}); load(); };
    const del = document.createElement("button");
    del.textContent = "삭제";
    del.onclick = async () => { await fetch("/api/todos/" + it.id, {method: "DELETE"}); load(); };
    li.append(tog, del);
    ul.append(li);
  }
}
document.getElementById("f").onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById("t");
  await fetch("/api/todos", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({title: input.value})});
  input.value = "";
  load();
};
load();
</script>
</body>
</html>
'''

TESTS = '''import os
import tempfile

os.environ["TODO_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_crud_flow():
    r = client.post("/api/todos", json={"title": "우유 사기"})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert client.get("/api/todos").json()[0]["title"] == "우유 사기"
    assert client.patch(f"/api/todos/{tid}").json()["done"] == 1
    assert client.delete(f"/api/todos/{tid}").status_code == 204
    assert client.get("/api/todos").json() == []


def test_sql_injection_is_inert():
    evil = "x'); DROP TABLE todos; --"
    assert client.post("/api/todos", json={"title": evil}).status_code == 201
    assert any(t["title"] == evil for t in client.get("/api/todos").json())
'''

FILES = {
    "requirements.txt": "fastapi>=0.115\nuvicorn>=0.30\nhttpx>=0.27\npytest>=8\n",
    "pytest.ini": "[pytest]\npythonpath = .\ntestpaths = tests\n",
    "app/__init__.py": "",
    "app/db.py": DB_V1,
    "app/main.py": MAIN,
    "static/index.html": INDEX,
    "tests/test_todos.py": TESTS,
}


class DemoLLM(BaseLLM):
    name = "demo"

    def _call(self, system, user, schema, label, max_tokens):
        if label == "prd":
            return PRD
        if label == "arch":
            return ARCH
        if label.startswith("file:"):
            return {"content": FILES[label[5:]]}
        if label.startswith("refine"):
            data = json.loads(user)
            fixed = [{"path": "app/db.py", "content": DB_V2}] if "app/db.py" in data["files"] else []
            return {"files": fixed, "notes": "[데모] SQL 문자열 결합을 매개변수 바인딩으로 교체"}
        if label == "readme":
            return {"content": "# 데모 할 일 관리\n\n> 이 프로젝트는 **데모 모드**의 고정 샘플입니다 (요청과 무관).\n\n"
                               "## 실행\n```bash\npip install -r requirements.txt\nuvicorn app.main:app --port 8000\n```\n\n"
                               "## 테스트\n```bash\npytest -q\n```\n"}
        raise LLMError(f"데모 모드가 처리할 수 없는 단계입니다: {label}")
