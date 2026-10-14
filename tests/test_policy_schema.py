import csv
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest

from boopang.config import Config
from boopang.exporter import to_csv, to_dict, to_json, to_xlsx
from boopang.pipeline import process
from boopang.sample import sample_xlsx
from boopang.schema import SchemaError, load_schema, parse_schema

HEAD = "주문번호,SKU,품목명,주문수량,검수수량,단가,출고일,운송장번호\n"
EXAMPLE = Path(__file__).resolve().parent.parent / "schema_config.example.json"
NOFEE = dict(free_shipping_threshold=Decimal("0"))


def run(body, cfg):
    return process((HEAD + body).encode(), "t.csv", cfg)


# ---- 정책 설정 ----
def test_bill_basis_ordered_vs_received():
    row = "A1,S1,n,10,4,1000,,\n"
    assert run(row, Config(bill_basis="received", **NOFEE)).lines[0].supply_amt == Decimal("4000")
    assert run(row, Config(bill_basis="ordered", **NOFEE)).lines[0].supply_amt == Decimal("10000")


def test_vat_disabled_exclusive_and_inclusive():
    row = "A1,S1,n,1,1,11000,,\n"
    l = run(row, Config(vat_enabled=False, **NOFEE)).lines[0]
    assert (l.supply_amt, l.vat_amt, l.total_amt) == (Decimal("11000"), Decimal("0"), Decimal("11000"))
    l = run(row, Config(vat_enabled=False, price_includes_vat=True, **NOFEE)).lines[0]
    assert (l.supply_amt, l.vat_amt, l.total_amt) == (Decimal("11000"), Decimal("0"), Decimal("11000"))


def test_custom_vat_rate_and_shipping_policy():
    cfg = Config(vat_rate=Decimal("0.05"), free_shipping_threshold=Decimal("100000"), shipping_fee=Decimal("2500"))
    o = run("A1,S1,n,1,1,10000,,\n", cfg).orders[0]
    assert o.vat_amt == Decimal("500") and o.shipping_fee == Decimal("2500") and o.total_amt == Decimal("13000")


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("BOOPANG_VAT_ENABLED", "false")
    monkeypatch.setenv("BOOPANG_BILL_BASIS", "ORDERED")
    monkeypatch.setenv("BOOPANG_SHIPPING_FEE", "2500")
    monkeypatch.setenv("BOOPANG_SCHEMA_FILE", "x.json")
    c = Config.from_env()
    assert not c.vat_enabled and c.bill_basis == "ordered" and c.shipping_fee == Decimal("2500")
    assert c.effective_vat_rate == 0 and c.schema_file == "x.json"


@pytest.mark.parametrize("name,value", [
    ("BOOPANG_VAT_ENABLED", "maybe"), ("BOOPANG_VAT_RATE", "abc"), ("BOOPANG_VAT_RATE", "1.5"),
    ("BOOPANG_BILL_BASIS", "shipped"), ("BOOPANG_SHIPPING_FEE", "-1"), ("BOOPANG_SHIPPING_FEE", "NaN"),
    ("BOOPANG_MAX_ROWS", "0"), ("BOOPANG_MAX_ROWS", "many"),
])
def test_invalid_env_fails_fast(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="BOOPANG|배송비"):
        Config.from_env()


# ---- 스키마 ----
def test_default_schema_matches_legacy_columns():
    s = load_schema()
    assert [c.name for c in s.line_columns][:3] == ["ORDER_NO", "LINE_NO", "SKU"] and len(s.line_columns) == 18


def test_example_schema_custom_mapping_csv_cp949():
    res = process(sample_xlsx(), "s.xlsx", Config())
    out = to_csv(res, load_schema(str(EXAMPLE)))
    assert not out.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(out.decode("cp949"))))
    assert list(rows[0].keys()) == ["WH_CD", "ORD_NO", "ITEM_CD", "ITEM_NM", "QTY", "AMT", "STS", "OUT_DT", "WAYBILL"]
    assert rows[0]["WH_CD"] == "BP01" and rows[0]["STS"] == "10"
    shipped = [r for r in rows if r["STS"] == "20"][0]
    assert shipped["OUT_DT"] == "20261010" and shipped["WAYBILL"] == "CJ1234567890"


def test_custom_schema_json_and_xlsx():
    res = process(sample_xlsx(), "s.xlsx", Config())
    schema = load_schema(str(EXAMPLE))
    data = json.loads(to_json(res, schema))
    assert data["schema_version"] == "2.0-boopang"
    o = data["orders"][0]
    assert set(o) == {"ORD_NO", "STS", "TOT_AMT", "lines"} and set(o["lines"][0]) >= {"WH_CD", "ITEM_CD"}
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(to_xlsx(res, schema)))
    assert [c.value for c in wb["WMS라인"][1]] == [c["column"] for c in json.loads(EXAMPLE.read_text())["line_columns"]]


def test_partial_override_inherits_defaults():
    s = parse_schema({"line_columns": [{"column": "X", "source": "sku"}]}, base=json.loads(
        (Path(__file__).resolve().parent.parent / "boopang" / "wms_schema.default.json").read_text()))
    assert [c.name for c in s.line_columns] == ["X"] and len(s.order_columns) == 8 and s.delimiter == ","


def test_semicolon_delimiter_and_formula_in_constant():
    s = parse_schema({"csv": {"delimiter": ";"}, "line_columns": [
        {"column": "A", "source": "sku"}, {"column": "B", "value": "=1+1"}]},
        base=json.loads((Path(__file__).resolve().parent.parent / "boopang" / "wms_schema.default.json").read_text()))
    text = to_csv(run("A1,S1,n,1,1,100,,\n", Config()), s).decode("utf-8-sig")
    assert text.splitlines()[1] == "S1;'=1+1"


@pytest.mark.parametrize("patch,msg", [
    ({"line_columns": []}, "비어 있지 않은"),
    ({"line_columns": [{"column": "A", "source": "nope"}]}, "알 수 없는 source"),
    ({"line_columns": [{"column": "A", "source": "sku", "value": "x"}]}, "정확히 하나"),
    ({"line_columns": [{"column": "A"}]}, "정확히 하나"),
    ({"line_columns": [{"column": "A", "source": "sku"}, {"column": "A", "source": "name"}]}, "중복"),
    ({"order_columns": [{"column": "lines", "source": "order_id"}]}, "예약"),
    ({"status_codes": {"BOGUS": "1"}}, "알 수 없는 상태"),
    ({"status_codes": {"READY": ""}}, "문자열"),
    ({"csv": {"delimiter": "ab"}}, "delimiter"),
    ({"csv": {"encoding": "nope-9"}}, "인코딩"),
    ({"date_format": ""}, "date_format"),
])
def test_invalid_schema_rejected(patch, msg):
    base = json.loads((Path(__file__).resolve().parent.parent / "boopang" / "wms_schema.default.json").read_text())
    with pytest.raises(SchemaError, match=msg):
        parse_schema(patch, base=base)


def test_schema_file_errors(tmp_path):
    with pytest.raises(SchemaError, match="찾을 수 없"):
        load_schema(str(tmp_path / "missing.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(SchemaError, match="읽을 수 없"):
        load_schema(str(bad))


def test_unencodable_char_is_not_silently_replaced():
    s = load_schema(str(EXAMPLE))  # cp949
    res = run("A1,S1,😀,1,1,100,,\n", Config())
    with pytest.raises(UnicodeEncodeError):
        to_csv(res, s)


# ---- 웹 연동 ----
def test_web_uses_schema_file_env(monkeypatch):
    from fastapi.testclient import TestClient
    from boopang.web.app import app
    c = TestClient(app)
    monkeypatch.setenv("BOOPANG_SCHEMA_FILE", str(EXAMPLE))
    r = c.post("/api/convert?format=csv", files={"file": ("s.xlsx", sample_xlsx())})
    assert r.status_code == 200 and r.content.decode("cp949").startswith("WH_CD,ORD_NO")
    # UI 미리보기는 스키마와 무관하게 고정 키
    assert "order_id" in c.post("/api/process", files={"file": ("s.xlsx", sample_xlsx())}).json()["orders"][0]
    monkeypatch.setenv("BOOPANG_SCHEMA_FILE", "/nonexistent.json")
    assert c.post("/api/process", files={"file": ("s.xlsx", sample_xlsx())}).status_code == 200  # 미리보기는 영향 없음
    assert c.post("/api/convert?format=csv", files={"file": ("s.xlsx", sample_xlsx())}).status_code == 500
