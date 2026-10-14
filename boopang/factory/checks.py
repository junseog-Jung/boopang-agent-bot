"""생성된 코드의 정적 점검 — 문법 검사(QA)와 보안 스캔(Security). 코드를 실행하지 않는다."""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"


@dataclass
class Finding:
    path: str
    line: int
    severity: str
    code: str
    message: str
    source: str  # qa | security
    detail: str = ""        # 테스트 출력 등 자가 치유에 전달할 긴 설명
    paths: tuple = ()       # 이 발견과 관련된 추가 파일(수정 대상 후보)

    def label(self) -> str:
        return f"{self.path}:{self.line} [{self.code}] {self.message}"


# ------------------------------------------------------------------ QA
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.problems = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
        elif any(t == tag for t, _ in self.stack):
            while self.stack and self.stack[-1][0] != tag:
                t, ln = self.stack.pop()
                self.problems.append((ln, f"<{t}> 태그가 닫히지 않았습니다"))
            self.stack.pop()
        else:
            self.problems.append((self.getpos()[0], f"여는 태그 없는 </{tag}>"))


def _py_modules(files: dict) -> set:
    mods = set()
    for p in files:
        if p.endswith(".py"):
            parts = PurePosixPath(p).with_suffix("").parts
            mods.add(parts[0])
            if parts[-1] == "__init__" and len(parts) > 1:
                mods.add(parts[-2])
    return mods


def _req_names(files: dict) -> set:
    names = set()
    for p, c in files.items():
        if PurePosixPath(p).name.startswith("requirements") and p.endswith(".txt"):
            for line in c.splitlines():
                m = re.match(r"\s*([A-Za-z0-9_.\-]+)", line)
                if m and not line.strip().startswith(("#", "-")):
                    names.add(m.group(1).lower().replace("-", "_").replace(".", "_"))
    return names


def qa_check(files: dict) -> list[Finding]:
    out: list[Finding] = []
    local, reqs = _py_modules(files), _req_names(files)
    node = shutil.which("node")
    skipped_js = False
    for path, content in sorted(files.items()):
        ext = PurePosixPath(path).suffix.lower()
        if not content.strip():
            out.append(Finding(path, 1, WARN, "EMPTY_FILE", "빈 파일입니다", "qa"))
            continue
        if ext == ".py":
            try:
                tree = ast.parse(content, filename=path)
            except SyntaxError as exc:
                out.append(Finding(path, exc.lineno or 1, ERROR, "PY_SYNTAX", f"파이썬 문법 오류: {exc.msg}", "qa"))
                continue
            for n in ast.walk(tree):
                names = []
                if isinstance(n, ast.Import):
                    names = [a.name.split(".")[0] for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    names = [n.module.split(".")[0]]
                for name in names:
                    key = name.lower().replace("-", "_")
                    if name in sys.stdlib_module_names or name in local or key in reqs or name == "__future__":
                        continue
                    out.append(Finding(path, n.lineno, WARN, "UNDECLARED_IMPORT",
                                       f"'{name}' 가 requirements 에 없거나 프로젝트에 없는 모듈일 수 있습니다", "qa"))
        elif ext == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                out.append(Finding(path, exc.lineno, ERROR, "JSON_SYNTAX", f"JSON 오류: {exc.msg}", "qa"))
        elif ext in (".js", ".mjs", ".cjs"):
            if node is None:
                skipped_js = True
                continue
            err = _node_check(node, content, ext)
            if err:
                out.append(Finding(path, err[0], ERROR, "JS_SYNTAX", f"자바스크립트 문법 오류: {err[1]}", "qa"))
        elif ext in (".html", ".htm"):
            p = _Html()
            try:
                p.feed(content)
                p.close()
            except Exception:
                out.append(Finding(path, 1, WARN, "HTML_PARSE", "HTML 을 해석할 수 없습니다", "qa"))
                continue
            for ln, msg in p.problems[:5] + [(l, f"<{t}> 태그가 닫히지 않았습니다") for t, l in p.stack[:3]]:
                out.append(Finding(path, ln, WARN, "HTML_TAG", msg, "qa"))
    if skipped_js:
        out.append(Finding("(project)", 0, INFO, "JS_CHECK_SKIPPED", "node 가 없어 자바스크립트 문법 검사를 생략했습니다", "qa"))
    if not any(PurePosixPath(p).name.startswith("requirements") or p == "package.json" for p in files):
        out.append(Finding("(project)", 0, WARN, "NO_DEPENDENCY_FILE", "requirements.txt 또는 package.json 이 없습니다", "qa"))
    return out


def _node_check(node: str, content: str, ext: str):
    """`node --check` 는 파싱만 하고 실행하지 않는다."""
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "check" + (".mjs" if ext == ".mjs" else ".js"))
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(content)
        try:
            r = subprocess.run([node, "--check", f], capture_output=True, text=True, timeout=10, cwd=d,
                               env={"PATH": os.environ.get("PATH", "")})
        except subprocess.TimeoutExpired:
            return (1, "검사 시간 초과")
    if r.returncode == 0:
        return None
    msg = next((l for l in r.stderr.splitlines() if "Error" in l), "문법 오류")
    m = re.search(r":(\d+)\s*$", r.stderr.splitlines()[0]) if r.stderr else None
    return (int(m.group(1)) if m else 1, msg[:120])


# ------------------------------------------------------------------ Security
_SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API 키"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS 액세스 키"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHub 토큰"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"), "개인 키"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "Slack 토큰"),
]
_ASSIGN = re.compile(r"""(?ix)\b(api[_-]?key|secret(?:[_-]?key)?|passwd|password|token|private[_-]?key)\b\s*[:=]\s*["']([^"'\n]{8,})["']""")
_PLACEHOLDER = re.compile(r"(?i)(change.?me|your[_-]|example|placeholder|xxx|dummy|test|sample|<.+>|\$\{|os\.environ|getenv)")
_INNER_HTML = re.compile(r"\.innerHTML\s*\+?=\s*(.+)")
_LITERAL = re.compile(r"""^(["'])[^"'`$]*\1\s*;?\s*$|^`[^`$]*`\s*;?\s*$""")


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _dynamic_sql(arg) -> bool:
    if isinstance(arg, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in arg.values)
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)):
        return not (isinstance(arg.left, ast.Constant) and isinstance(arg.right, ast.Constant))
    if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "format":
        return True
    return False


def _py_security(path: str, tree) -> list[Finding]:
    out = []

    def add(n, sev, code, msg):
        out.append(Finding(path, getattr(n, "lineno", 1), sev, code, msg, "security"))

    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = _dotted(n.func)
        short = name.split(".")[-1]
        kw = {k.arg: k.value for k in n.keywords if k.arg}
        if name in ("eval", "exec"):
            add(n, ERROR, "PY_EVAL", f"{name}() 사용 — 임의 코드 실행 위험")
        elif name in ("os.system", "os.popen"):
            add(n, ERROR, "PY_OS_COMMAND", f"{name}() 사용 — 명령어 인젝션 위험")
        elif name.startswith("subprocess.") and isinstance(kw.get("shell"), ast.Constant) and kw["shell"].value is True:
            add(n, ERROR, "PY_SHELL_TRUE", "subprocess shell=True — 명령어 인젝션 위험")
        elif name in ("pickle.load", "pickle.loads", "marshal.loads", "cPickle.loads"):
            add(n, ERROR, "PY_UNSAFE_DESERIALIZE", f"{name}() — 신뢰할 수 없는 데이터 역직렬화 위험")
        elif name == "yaml.load" and "Loader" not in kw and len(n.args) < 2:
            add(n, ERROR, "PY_YAML_LOAD", "yaml.load() 는 SafeLoader 없이 사용하면 위험 (yaml.safe_load 사용)")
        elif short in ("execute", "executemany", "executescript") and n.args and _dynamic_sql(n.args[0]):
            add(n, ERROR, "SQL_INJECTION", "SQL 문을 문자열 결합/포맷으로 만들고 있습니다 — 매개변수 바인딩 사용")
        if isinstance(kw.get("verify"), ast.Constant) and kw["verify"].value is False:
            add(n, ERROR, "TLS_VERIFY_OFF", "verify=False — TLS 검증 비활성화")
        if isinstance(kw.get("debug"), ast.Constant) and kw["debug"].value is True:
            add(n, WARN, "DEBUG_ON", "debug=True 는 운영 환경에서 위험합니다")
        if short == "add_middleware" or name.endswith("CORSMiddleware"):
            ao = kw.get("allow_origins")
            if isinstance(ao, ast.List) and any(isinstance(e, ast.Constant) and e.value == "*" for e in ao.elts):
                add(n, WARN, "CORS_ANY", "CORS allow_origins=['*']")
        if name in ("hashlib.md5", "hashlib.sha1"):
            add(n, WARN, "WEAK_HASH", f"{name} 는 비밀번호/무결성 용도로 부적합")
        if name == "tempfile.mktemp":
            add(n, WARN, "TEMP_RACE", "tempfile.mktemp 는 경쟁 조건 위험 (mkstemp 사용)")
    return out


def security_scan(files: dict) -> list[Finding]:
    out: list[Finding] = []
    for path, content in sorted(files.items()):
        ext = PurePosixPath(path).suffix.lower()
        is_example = path.endswith(".example") or "example" in PurePosixPath(path).name.lower()
        if ext == ".py":
            try:
                out.extend(_py_security(path, ast.parse(content)))
            except SyntaxError:
                pass  # 문법 오류는 QA 가 보고
        for i, line in enumerate(content.splitlines(), 1):
            if not is_example:
                for rx, label in _SECRET_PATTERNS:
                    if rx.search(line):
                        out.append(Finding(path, i, ERROR, "HARDCODED_SECRET", f"{label} 로 보이는 값이 코드에 있습니다", "security"))
                m = _ASSIGN.search(line)
                if m and not _PLACEHOLDER.search(m.group(2)) and not _PLACEHOLDER.search(line):
                    out.append(Finding(path, i, ERROR, "HARDCODED_SECRET", f"'{m.group(1)}' 에 비밀값이 하드코딩되어 있습니다 (환경변수 사용)", "security"))
            if ext in (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".html", ".vue"):
                m = _INNER_HTML.search(line)
                if m and not _LITERAL.match(m.group(1).strip()):
                    out.append(Finding(path, i, ERROR, "XSS_INNERHTML", "innerHTML 에 동적 값을 대입합니다 — textContent 또는 DOM API 사용", "security"))
                if re.search(r"\beval\s*\(|new\s+Function\s*\(|document\.write\s*\(", line):
                    out.append(Finding(path, i, ERROR, "JS_EVAL", "eval/new Function/document.write 사용", "security"))
                if "dangerouslySetInnerHTML" in line:
                    out.append(Finding(path, i, WARN, "XSS_DANGEROUS_HTML", "dangerouslySetInnerHTML 사용", "security"))
                if re.search(r"child_process|\bexec\s*\(\s*[`\"'].*\$\{", line):
                    out.append(Finding(path, i, ERROR, "JS_COMMAND_INJECTION", "동적 문자열로 셸 명령을 실행합니다", "security"))
    return out
