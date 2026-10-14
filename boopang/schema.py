"""WMS 출력 스키마(schema_config.json) 로더 — 컬럼명/순서/상수/상태코드/구분자/인코딩을 설정으로 매핑한다."""
from __future__ import annotations

import codecs
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .models import Status

DEFAULT_SCHEMA_PATH = Path(__file__).with_name("wms_schema.default.json")

LINE_FIELDS = (
    "order_id", "line_no", "sku", "name", "qty_ordered", "qty_received", "unit_price", "discount_pct",
    "supply_amt", "vat_amt", "total_amt", "status", "status_label", "recipient", "phone", "address",
    "ship_date", "tracking_no", "issue_count",
)
ORDER_FIELDS = (
    "order_id", "status", "status_label", "line_count", "supply_amt", "vat_amt", "shipping_fee", "total_amt",
)
RESERVED_ORDER_COLUMNS = {"lines"}  # JSON 에서 라인 배열이 들어가는 키


class SchemaError(ValueError):
    """schema_config.json 이 올바르지 않을 때 발생."""


@dataclass(frozen=True)
class Column:
    name: str
    source: Optional[str] = None   # 내부 필드명
    value: Optional[str] = None    # 고정 상수 (source 와 택일)


@dataclass(frozen=True)
class Schema:
    version: str
    line_columns: tuple
    order_columns: tuple
    status_codes: dict
    delimiter: str
    encoding: str
    date_format: str


def _columns(items, allowed: tuple, label: str, reserved: set = frozenset()) -> tuple:
    if not isinstance(items, list) or not items:
        raise SchemaError(f"{label}: 비어 있지 않은 배열이어야 합니다")
    cols, seen = [], set()
    for idx, it in enumerate(items):
        where = f"{label}[{idx}]"
        if not isinstance(it, dict):
            raise SchemaError(f"{where}: 객체여야 합니다")
        name = it.get("column")
        if not isinstance(name, str) or not name.strip():
            raise SchemaError(f"{where}: 'column' 이름이 필요합니다")
        if name in seen:
            raise SchemaError(f"{where}: 컬럼명이 중복되었습니다 ({name})")
        if name in reserved:
            raise SchemaError(f"{where}: 예약된 컬럼명입니다 ({name})")
        seen.add(name)
        has_src, has_val = "source" in it, "value" in it
        if has_src == has_val:
            raise SchemaError(f"{where}: 'source' 와 'value' 중 정확히 하나만 지정하세요 ({name})")
        if has_src:
            if it["source"] not in allowed:
                raise SchemaError(f"{where}: 알 수 없는 source '{it['source']}' (가능: {', '.join(allowed)})")
            cols.append(Column(name, source=it["source"]))
        else:
            if not isinstance(it["value"], (str, int)) or isinstance(it["value"], bool):
                raise SchemaError(f"{where}: 'value' 는 문자열 또는 정수여야 합니다")
            cols.append(Column(name, value=it["value"]))
    return tuple(cols)


def parse_schema(data: dict, base: Optional[dict] = None) -> Schema:
    """data 에 없는 항목은 base(기본 스키마)에서 채운다."""
    if not isinstance(data, dict):
        raise SchemaError("최상위는 JSON 객체여야 합니다")
    merged = dict(base or {})
    merged.update(data)

    csv_opts = {**(base or {}).get("csv", {}), **(data.get("csv") or {})}
    delimiter = csv_opts.get("delimiter", ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n\"":
        raise SchemaError("csv.delimiter: 줄바꿈/따옴표가 아닌 한 글자여야 합니다")
    encoding = csv_opts.get("encoding", "utf-8-sig")
    try:
        codecs.lookup(encoding)
    except (LookupError, TypeError) as exc:
        raise SchemaError(f"csv.encoding: 지원하지 않는 인코딩입니다 ({encoding})") from exc

    date_format = merged.get("date_format", "%Y-%m-%d")
    if not isinstance(date_format, str) or not date_format:
        raise SchemaError("date_format: strftime 형식 문자열이어야 합니다")
    try:
        date(2000, 1, 2).strftime(date_format)
    except ValueError as exc:
        raise SchemaError(f"date_format: 올바르지 않은 형식입니다 ({date_format})") from exc

    codes = {s.value: s.value for s in Status}
    for k, v in (merged.get("status_codes") or {}).items():
        if k not in codes:
            raise SchemaError(f"status_codes: 알 수 없는 상태 '{k}' (가능: {', '.join(codes)})")
        if not isinstance(v, str) or not v:
            raise SchemaError(f"status_codes.{k}: 비어 있지 않은 문자열이어야 합니다")
        codes[k] = v

    version = merged.get("schema_version", "1.0")
    if not isinstance(version, str) or not version:
        raise SchemaError("schema_version: 문자열이어야 합니다")

    return Schema(
        version=version,
        line_columns=_columns(merged.get("line_columns"), LINE_FIELDS, "line_columns"),
        order_columns=_columns(merged.get("order_columns"), ORDER_FIELDS, "order_columns", RESERVED_ORDER_COLUMNS),
        status_codes=codes,
        delimiter=delimiter,
        encoding=encoding,
        date_format=date_format,
    )


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaError(f"스키마 파일을 찾을 수 없습니다: {path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise SchemaError(f"스키마 파일을 읽을 수 없습니다 ({path}): {exc}") from exc


def load_schema(path: Optional[str] = None) -> Schema:
    """path 가 없으면 내장 기본 스키마. 있으면 기본 스키마 위에 덮어쓴다."""
    base = _read_json(DEFAULT_SCHEMA_PATH)
    if not path:
        return parse_schema(base)
    return parse_schema(_read_json(Path(path)), base=base)
