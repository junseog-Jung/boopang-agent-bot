"""생성된 프로젝트 파일 컨테이너 — 경로/용량 검증과 ZIP 패키징.

LLM 이 만든 경로와 내용은 신뢰할 수 없는 입력이므로 모든 파일은 여기를 거쳐야 한다.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath

ALLOWED_EXT = {
    ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md", ".txt", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".sql", ".sh", ".svg", ".vue", ".env.example", ".lock",
}
ALLOWED_NAMES = {"Dockerfile", "Makefile", "LICENSE", ".gitignore", ".dockerignore", ".env.example", ".editorconfig"}
BLOCKED_PARTS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
_PATH_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")
MAX_DEPTH = 6
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ProjectError(ValueError):
    pass


def slugify(text: str, default: str = "project") -> str:
    s = _SLUG_RE.sub("-", (text or "").lower()).strip("-")[:40]
    return s or default


def validate_path(path: str) -> str:
    """안전한 상대 POSIX 경로만 허용. 위반 시 ProjectError."""
    if not isinstance(path, str) or not path or len(path) > 120:
        raise ProjectError("경로가 비어 있거나 너무 깁니다")
    if not _PATH_RE.match(path) or path.startswith("/") or "//" in path or path.endswith("/"):
        raise ProjectError(f"허용되지 않는 경로 문자입니다: {path[:60]}")
    p = PurePosixPath(path)
    parts = p.parts
    if len(parts) > MAX_DEPTH or any(x in ("", ".", "..") for x in parts):
        raise ProjectError(f"허용되지 않는 경로입니다: {path}")
    if any(x in BLOCKED_PARTS for x in parts):
        raise ProjectError(f"허용되지 않는 디렉터리입니다: {path}")
    name = p.name
    if name in ALLOWED_NAMES:
        return path
    if name.startswith(".") and name not in ALLOWED_NAMES:
        raise ProjectError(f"허용되지 않는 숨김 파일입니다: {path}")
    if name == ".env" or name.endswith(".env"):
        raise ProjectError("실제 .env 파일은 생성할 수 없습니다 (.env.example 사용)")
    if p.suffix.lower() not in ALLOWED_EXT:
        raise ProjectError(f"허용되지 않는 파일 형식입니다: {path}")
    return path


class Project:
    def __init__(self, max_files: int, max_file_bytes: int, max_total_bytes: int):
        self.files: dict[str, str] = {}
        self.max_files, self.max_file_bytes, self.max_total_bytes = max_files, max_file_bytes, max_total_bytes

    @staticmethod
    def clean(content) -> str:
        if not isinstance(content, str):
            raise ProjectError("파일 내용이 문자열이 아닙니다")
        return content.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")

    def total_bytes(self, extra_path: str = "", extra: str = "") -> int:
        total = sum(len(v.encode("utf-8")) for k, v in self.files.items() if k != extra_path)
        return total + len(extra.encode("utf-8"))

    def set(self, path: str, content: str) -> None:
        validate_path(path)
        text = self.clean(content)
        size = len(text.encode("utf-8"))
        if size > self.max_file_bytes:
            raise ProjectError(f"파일이 너무 큽니다 ({path}: {size:,} bytes > {self.max_file_bytes:,})")
        if path not in self.files and len(self.files) >= self.max_files:
            raise ProjectError(f"파일 개수 한도({self.max_files}개)를 초과했습니다")
        if self.total_bytes(path, text) > self.max_total_bytes:
            raise ProjectError(f"프로젝트 전체 용량 한도({self.max_total_bytes:,} bytes)를 초과했습니다")
        self.files[path] = text

    def tree(self) -> list[dict]:
        return [{"path": p, "size": len(c.encode("utf-8"))} for p, c in sorted(self.files.items())]

    def to_zip(self, root: str) -> bytes:
        """고정 타임스탬프·비실행 권한(0644)으로 ZIP 생성 → 재현 가능하고 setuid 등 위험 비트 없음."""
        root = slugify(root)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.files):
                info = zipfile.ZipInfo(f"{root}/{path}", date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, self.files[path].encode("utf-8"))
        return buf.getvalue()
