"""CLI: python -m boopang.cli 입력파일 [--out-dir 출력폴더]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .exporter import default_schema, to_csv, to_json, to_xlsx
from .schema import SchemaError, load_schema
from .models import ParseError
from .pipeline import process


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="boopang", description="부팡 물류 및 주문 처리 자동화 봇")
    ap.add_argument("input", type=Path, help="패킹리스트/주문 데이터 (.xlsx, .csv)")
    ap.add_argument("--out-dir", type=Path, default=Path("out"), help="결과 저장 폴더 (기본: out)")
    ap.add_argument("--schema", type=Path, default=None, help="WMS 스키마 설정 파일 (기본: BOOPANG_SCHEMA_FILE 또는 내장 스키마)")
    args = ap.parse_args(argv)

    try:
        schema = load_schema(str(args.schema)) if args.schema else default_schema()
        result = process(args.input.read_bytes(), args.input.name)
    except (ParseError, SchemaError, ValueError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "wms_lines.csv").write_bytes(to_csv(result, schema))
    (args.out_dir / "wms_orders.json").write_bytes(to_json(result, schema))
    (args.out_dir / "inspection_report.xlsx").write_bytes(to_xlsx(result, schema))

    s = result.summary()
    print(f"라인 {s['line_count']} · 주문 {s['order_count']} · 오류 {s['issues']['ERROR']} · 경고 {s['issues']['WARN']}")
    print(f"공급가 {s['supply_amt']} · 부가세 {s['vat_amt']} · 배송비 {s['shipping_fee']} · 합계 {s['total_amt']}")
    print(f"결과 저장: {args.out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
