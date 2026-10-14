"""배송 상태 분류 및 주문 단위 롤업."""
from __future__ import annotations

from .models import Issue, Line, Order, Severity, Status

_ACTIVE_EXCLUDED = {Status.CANCELLED}


def _from_raw(raw: str) -> Status | None:
    s = raw.replace(" ", "").lower()
    if not s:
        return None
    if "취소" in s or "cancel" in s:
        return Status.CANCELLED
    if "반품" in s or "return" in s:
        return Status.RETURNED
    if "완료" in s or "delivered" in s or "complete" in s:
        return Status.DELIVERED
    if "배송중" in s or "출고" in s or "shipping" in s or "shipped" in s or "transit" in s:
        return Status.SHIPPING
    if "보류" in s or "hold" in s:
        return Status.HOLD
    return None


def classify_line(line: Line, issues: list[Issue]) -> Status:
    def warn(code, msg):
        issues.append(Issue(line.row_no, Severity.WARN, code, msg, line.order_id, line.sku))
        line.issue_count += 1

    if line.has_error:
        return Status.HOLD

    raw = _from_raw(line.raw_status)
    if line.raw_status and raw is None:
        warn("UNKNOWN_STATUS", f"인식할 수 없는 배송상태 문구입니다: {line.raw_status}")

    if raw in (Status.CANCELLED, Status.RETURNED):
        if line.tracking_no and raw is Status.CANCELLED:
            warn("STATUS_CONFLICT", "취소 건에 운송장번호가 있습니다.")
        return raw
    if raw in (Status.DELIVERED, Status.SHIPPING):
        if not line.tracking_no:
            warn("STATUS_CONFLICT", "배송 상태이지만 운송장번호가 없습니다.")
        return raw
    if raw is Status.HOLD:
        return Status.HOLD
    if line.tracking_no:
        return Status.SHIPPING

    rcv, qty = line.qty_received, line.qty_ordered
    if rcv is None:
        return Status.INSPECT_PENDING
    if rcv == 0:
        return Status.BACKORDER
    if rcv < qty:
        return Status.PARTIAL
    return Status.READY


def rollup_order(lines: list[Line]) -> Status:
    active = {l.status for l in lines} - _ACTIVE_EXCLUDED
    if not active:
        return Status.CANCELLED
    if Status.HOLD in active:
        return Status.HOLD
    if len(active) == 1:
        return next(iter(active))
    if active <= {Status.SHIPPING, Status.DELIVERED}:
        return Status.SHIPPING
    return Status.PARTIAL


def classify_all(lines: list[Line], issues: list[Issue]) -> None:
    for ln in lines:
        ln.status = classify_line(ln, issues)


def build_orders(lines: list[Line]) -> list[Order]:
    orders: dict = {}
    for ln in lines:
        orders.setdefault(ln.order_id, Order(order_id=ln.order_id)).lines.append(ln)
    for o in orders.values():
        o.status = rollup_order(o.lines)
    return list(orders.values())
