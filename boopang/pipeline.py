"""전체 처리 파이프라인 — CLI와 웹이 공유하는 단일 진입점."""
from __future__ import annotations

from .config import Config
from .inspector import inspect_rows
from .models import Result
from .parser import parse_file
from .pricing import price_all
from .status import build_orders, classify_all


def process(content: bytes, filename: str, config: Config | None = None) -> Result:
    cfg = config or Config.from_env()
    rows = parse_file(content, filename, cfg)
    lines, issues = inspect_rows(rows)
    classify_all(lines, issues)
    orders = build_orders(lines)
    price_all(lines, orders, cfg)
    issues.sort(key=lambda i: (i.row_no, i.severity.value))
    return Result(source_name=filename, lines=lines, orders=orders, issues=issues)
