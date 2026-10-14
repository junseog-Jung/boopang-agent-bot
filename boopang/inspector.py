"""값 정규화 및 품목별 검수."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from .models import Issue, Line, RawRow, Severity

MAX_MONEY = Decimal("1000000000000")  # 1조 — 비정상 값 차단
MAX_QTY = 10_000_000


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))  # 엑셀 숫자 셀 1001.0 → "1001"
    return str(v).strip()


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def parse_int(v) -> tuple[Optional[int], bool]:
    """(값, 유효여부). 빈 값은 (None, True)."""
    if _blank(v):
        return None, True
    if isinstance(v, bool):
        return None, False
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except InvalidOperation:
        return None, False
    if not d.is_finite() or d != d.to_integral_value() or abs(d) > MAX_QTY:
        return None, False
    return int(d), True


def parse_money(v) -> tuple[Optional[Decimal], bool]:
    if _blank(v):
        return None, True
    if isinstance(v, bool):
        return None, False
    s = re.sub(r"[,\s원₩$￦]|KRW|krw", "", str(v))
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None, False
    if not d.is_finite() or abs(d) > MAX_MONEY:
        return None, False
    return d, True


def parse_discount(v) -> tuple[Decimal, bool]:
    """'10%' / 10 → 0.10, 0.1 → 0.10. 1 초과는 퍼센트로 해석."""
    if _blank(v):
        return Decimal("0"), True
    s = str(v).strip()
    pct = s.endswith("%")
    try:
        d = Decimal(s.rstrip("%").replace(",", "").strip())
    except InvalidOperation:
        return Decimal("0"), False
    if not d.is_finite():
        return Decimal("0"), False
    if pct or d > 1:
        d = d / Decimal(100)
    if d < 0 or d > 1:
        return Decimal("0"), False
    return d, True


def parse_date(v) -> tuple[Optional[date], bool]:
    if _blank(v):
        return None, True
    if isinstance(v, datetime):
        return v.date(), True
    if isinstance(v, date):
        return v, True
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date(), True
        except ValueError:
            continue
    return None, False


def normalize_phone(v) -> tuple[str, bool]:
    """(정규화된 번호, 유효여부). 빈 값은 ('', True)."""
    if _blank(v):
        return "", True
    digits = re.sub(r"\D", "", _text(v))
    if digits.startswith("82") and len(digits) in (11, 12):
        digits = "0" + digits[2:]
    if not (digits.startswith("0") and 9 <= len(digits) <= 11):
        return _text(v), False
    if digits.startswith("02"):
        a, rest = digits[:2], digits[2:]
        return f"{a}-{rest[:-4]}-{rest[-4:]}", True
    a, rest = digits[:3], digits[3:]
    return f"{a}-{rest[:-4]}-{rest[-4:]}", True


def inspect_rows(rows: list[RawRow]) -> tuple[list[Line], list[Issue]]:
    lines: list[Line] = []
    issues: list[Issue] = []
    seen: set = set()
    per_order: dict = {}

    for raw in rows:
        d = raw.data
        ln = Line(row_no=raw.row_no)
        ln.order_id = _text(d.get("order_id"))
        ln.sku = _text(d.get("sku"))
        ln.name = _text(d.get("name"))
        ln.recipient = _text(d.get("recipient"))
        ln.address = _text(d.get("address"))
        ln.tracking_no = _text(d.get("tracking_no"))
        ln.raw_status = _text(d.get("raw_status"))
        ln.memo = _text(d.get("memo"))

        def add(sev, code, msg):
            issues.append(Issue(raw.row_no, sev, code, msg, ln.order_id, ln.sku))
            if sev is Severity.ERROR:
                ln.has_error = True

        if not ln.order_id:
            add(Severity.ERROR, "MISSING_ORDER_ID", "주문번호가 비어 있습니다.")
        if not ln.sku:
            add(Severity.ERROR, "MISSING_SKU", "SKU(품목코드)가 비어 있습니다.")
        if not ln.name:
            add(Severity.WARN, "MISSING_NAME", "품목명이 비어 있습니다.")

        qty, ok = parse_int(d.get("qty_ordered"))
        if not ok or qty is None or qty <= 0:
            add(Severity.ERROR, "INVALID_QTY", f"주문수량이 올바르지 않습니다: {_text(d.get('qty_ordered')) or '(공란)'}")
        else:
            ln.qty_ordered = qty

        rcv, ok = parse_int(d.get("qty_received"))
        if not ok or (rcv is not None and rcv < 0):
            add(Severity.ERROR, "INVALID_RCV_QTY", f"검수수량이 올바르지 않습니다: {_text(d.get('qty_received'))}")
        else:
            ln.qty_received = rcv

        price, ok = parse_money(d.get("unit_price"))
        if not ok or price is None or price < 0:
            add(Severity.ERROR, "INVALID_PRICE", f"단가가 올바르지 않습니다: {_text(d.get('unit_price')) or '(공란)'}")
        else:
            ln.unit_price = price
            if price == 0:
                add(Severity.WARN, "ZERO_PRICE", "단가가 0원입니다.")

        disc, ok = parse_discount(d.get("discount_rate"))
        if not ok:
            add(Severity.ERROR, "INVALID_DISCOUNT", f"할인율이 올바르지 않습니다: {_text(d.get('discount_rate'))}")
        ln.discount_rate = disc

        ln.phone, ok = normalize_phone(d.get("phone"))
        if not ok:
            add(Severity.WARN, "INVALID_PHONE", f"연락처 형식이 올바르지 않습니다: {ln.phone}")

        ln.ship_date, ok = parse_date(d.get("ship_date"))
        if not ok:
            add(Severity.WARN, "INVALID_DATE", f"출고일 형식을 해석할 수 없습니다: {_text(d.get('ship_date'))}")

        # 검수 수량 대조
        if ln.qty_ordered is not None and ln.qty_received is not None:
            if ln.qty_received < ln.qty_ordered:
                add(Severity.WARN, "SHORTAGE", f"입고 부족: 주문 {ln.qty_ordered} / 검수 {ln.qty_received}")
            elif ln.qty_received > ln.qty_ordered:
                add(Severity.WARN, "OVERAGE", f"입고 초과: 주문 {ln.qty_ordered} / 검수 {ln.qty_received}")
        elif ln.qty_ordered is not None and ln.qty_received is None:
            add(Severity.INFO, "NOT_INSPECTED", "검수수량이 비어 있어 미검수로 처리합니다.")

        # 중복 라인
        if ln.order_id and ln.sku:
            key = (ln.order_id, ln.sku)
            if key in seen:
                add(Severity.WARN, "DUPLICATE_LINE", "같은 주문번호에 동일 SKU가 중복되어 있습니다.")
            seen.add(key)

        per_order[ln.order_id] = per_order.get(ln.order_id, 0) + 1
        ln.line_no = per_order[ln.order_id]
        lines.append(ln)

    # 이슈 개수를 라인에 반영
    counts: dict = {}
    for i in issues:
        counts[i.row_no] = counts.get(i.row_no, 0) + 1
    for ln in lines:
        ln.issue_count = counts.get(ln.row_no, 0)
    return lines, issues
