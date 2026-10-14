"""물류 DB(WMS) 연동 포맷 출력 — CSV / JSON / XLSX 리포트.

내부 표준 레코드(canon_*)를 만든 뒤 schema(schema_config.json)에 따라 컬럼명·순서·상수·상태코드를 매핑한다.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .config import Config
from .models import STATUS_LABELS, Line, Order, Result, Status
from .schema import Schema, load_schema

PREVIEW_VERSION = "1.0"  # UI 미리보기 응답 형식 버전 (WMS 스키마와 무관)
_DANGEROUS_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """엑셀/CSV 수식 인젝션 방어: 위험한 접두 문자가 있는 문자열 앞에 ' 를 붙인다."""
    if isinstance(value, str) and value.startswith(_DANGEROUS_PREFIX):
        return "'" + value
    return value


def default_schema() -> Schema:
    return load_schema(Config.from_env().schema_file or None)


def _money(d: Decimal) -> str:
    return str(d.quantize(Decimal("1")))


def canon_line(l: Line, date_format: str = "%Y-%m-%d") -> dict:
    """내부 표준 라인 레코드. 키는 schema 의 source 이름과 동일."""
    return {
        "order_id": l.order_id,
        "line_no": l.line_no,
        "sku": l.sku,
        "name": l.name,
        "qty_ordered": l.qty_ordered if l.qty_ordered is not None else "",
        "qty_received": l.qty_received if l.qty_received is not None else "",
        "unit_price": _money(l.unit_price) if l.unit_price is not None else "",
        "discount_pct": format((l.discount_rate * 100).normalize(), "f") if l.discount_rate else "0",
        "supply_amt": _money(l.supply_amt),
        "vat_amt": _money(l.vat_amt),
        "total_amt": _money(l.total_amt),
        "status": l.status.value,
        "status_label": STATUS_LABELS[l.status],
        "recipient": l.recipient,
        "phone": l.phone,
        "address": l.address,
        "ship_date": l.ship_date.strftime(date_format) if l.ship_date else "",
        "tracking_no": l.tracking_no,
        "issue_count": l.issue_count,
    }


def canon_order(o: Order) -> dict:
    return {
        "order_id": o.order_id,
        "status": o.status.value,
        "status_label": STATUS_LABELS[o.status],
        "line_count": len(o.lines),
        "supply_amt": _money(o.supply_amt),
        "vat_amt": _money(o.vat_amt),
        "shipping_fee": _money(o.shipping_fee),
        "total_amt": _money(o.total_amt),
    }


def _map(canon: dict, columns: tuple, status_codes: dict) -> dict:
    out = {}
    for c in columns:
        if c.source is None:
            out[c.name] = c.value
        elif c.source == "status":
            out[c.name] = status_codes[canon["status"]]
        else:
            out[c.name] = canon[c.source]
    return out


def line_rows(result: Result, schema: Schema) -> list:
    return [_map(canon_line(l, schema.date_format), schema.line_columns, schema.status_codes) for l in result.lines]


def order_rows(result: Result, schema: Schema) -> list:
    return [_map(canon_order(o), schema.order_columns, schema.status_codes) for o in result.orders]


def to_csv(result: Result, schema: Optional[Schema] = None) -> bytes:
    """WMS 라인 CSV. 구분자/인코딩은 스키마 설정(기본: , / UTF-8 BOM)."""
    schema = schema or default_schema()
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=[c.name for c in schema.line_columns],
                       delimiter=schema.delimiter, lineterminator="\r\n")
    w.writeheader()
    for row in line_rows(result, schema):
        w.writerow({k: safe_cell(v) for k, v in row.items()})
    return buf.getvalue().encode(schema.encoding)  # 표현 불가 문자는 UnicodeEncodeError (조용히 깨뜨리지 않음)


def to_preview(result: Result) -> dict:
    """웹 UI/미리보기용 — 스키마와 무관한 고정 키(내부 표준 필드명)."""
    return {
        "schema_version": PREVIEW_VERSION,
        "summary": result.summary(),
        "orders": [dict(canon_order(o), lines=[canon_line(l) for l in o.lines]) for o in result.orders],
        "issues": _issues(result),
    }


def _issues(result: Result) -> list:
    return [
        {"row": i.row_no, "severity": i.severity.value, "code": i.code, "message": i.message,
         "order_id": i.order_id, "sku": i.sku}
        for i in result.issues
    ]


def to_dict(result: Result, schema: Optional[Schema] = None) -> dict:
    """WMS JSON — 스키마에 따라 컬럼명이 매핑된 주문(라인 중첩) 구조."""
    schema = schema or default_schema()
    orders = []
    for o in result.orders:
        rec = _map(canon_order(o), schema.order_columns, schema.status_codes)
        rec["lines"] = [_map(canon_line(l, schema.date_format), schema.line_columns, schema.status_codes)
                        for l in o.lines]
        orders.append(rec)
    return {
        "schema_version": schema.version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": result.summary(),
        "orders": orders,
        "issues": _issues(result),
    }


def to_json(result: Result, schema: Optional[Schema] = None) -> bytes:
    return json.dumps(to_dict(result, schema), ensure_ascii=False, indent=2).encode("utf-8")


def _write_sheet(ws, headers: list, rows: list) -> None:
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in rows:
        ws.append([safe_cell(v) for v in row])
    for idx, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[idx - 1])) for r in rows[:200]]) + 2
        ws.column_dimensions[get_column_letter(idx)].width = min(width, 40)
    ws.freeze_panes = "A2"


def to_xlsx(result: Result, schema: Optional[Schema] = None) -> bytes:
    schema = schema or default_schema()
    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    s = result.summary()
    summary_rows = [
        ["원본 파일", s["source"]], ["라인 수", s["line_count"]], ["주문 수", s["order_count"]],
        ["오류(ERROR)", s["issues"]["ERROR"]], ["경고(WARN)", s["issues"]["WARN"]], ["정보(INFO)", s["issues"]["INFO"]],
        ["공급가 합계", s["supply_amt"]], ["부가세 합계", s["vat_amt"]],
        ["배송비 합계", s["shipping_fee"]], ["총 합계", s["total_amt"]],
    ]
    for status, cnt in sorted(s["order_status"].items()):
        summary_rows.append([f"주문 상태 · {STATUS_LABELS[Status(status)]}", cnt])
    _write_sheet(ws, ["항목", "값"], summary_rows)

    ow = wb.create_sheet("주문")
    _write_sheet(ow, [c.name for c in schema.order_columns], [list(r.values()) for r in order_rows(result, schema)])

    lw = wb.create_sheet("WMS라인")
    _write_sheet(lw, [c.name for c in schema.line_columns], [list(r.values()) for r in line_rows(result, schema)])

    iw = wb.create_sheet("검수이슈")
    rank = {"ERROR": 0, "WARN": 1, "INFO": 2}
    issues = sorted(result.issues, key=lambda i: (rank[i.severity.value], i.row_no))
    _write_sheet(iw, ["행", "심각도", "코드", "메시지", "주문번호", "SKU"],
                 [[i.row_no, i.severity.value, i.code, i.message, i.order_id, i.sku] for i in issues])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
