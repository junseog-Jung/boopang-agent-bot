"""도메인 모델."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


class Status(str, Enum):
    READY = "READY"
    SHIPPING = "SHIPPING"
    DELIVERED = "DELIVERED"
    PARTIAL = "PARTIAL"
    BACKORDER = "BACKORDER"
    INSPECT_PENDING = "INSPECT_PENDING"
    HOLD = "HOLD"
    CANCELLED = "CANCELLED"
    RETURNED = "RETURNED"


STATUS_LABELS = {
    Status.READY: "출고대기",
    Status.SHIPPING: "배송중",
    Status.DELIVERED: "배송완료",
    Status.PARTIAL: "부분입고",
    Status.BACKORDER: "입고대기",
    Status.INSPECT_PENDING: "검수대기",
    Status.HOLD: "보류(오류)",
    Status.CANCELLED: "취소",
    Status.RETURNED: "반품",
}


class ParseError(ValueError):
    """업로드 파일을 해석할 수 없을 때 발생."""


@dataclass
class Issue:
    row_no: int
    severity: Severity
    code: str
    message: str
    order_id: str = ""
    sku: str = ""


@dataclass
class RawRow:
    row_no: int
    data: dict


@dataclass
class Line:
    row_no: int
    line_no: int = 0
    order_id: str = ""
    sku: str = ""
    name: str = ""
    qty_ordered: Optional[int] = None
    qty_received: Optional[int] = None
    unit_price: Optional[Decimal] = None
    discount_rate: Decimal = Decimal("0")
    recipient: str = ""
    phone: str = ""
    address: str = ""
    ship_date: Optional[date] = None
    tracking_no: str = ""
    raw_status: str = ""
    memo: str = ""
    supply_amt: Decimal = Decimal("0")
    vat_amt: Decimal = Decimal("0")
    total_amt: Decimal = Decimal("0")
    billable: bool = True
    has_error: bool = False
    status: Status = Status.INSPECT_PENDING
    issue_count: int = 0


@dataclass
class Order:
    order_id: str
    lines: list = field(default_factory=list)
    status: Status = Status.INSPECT_PENDING
    supply_amt: Decimal = Decimal("0")
    vat_amt: Decimal = Decimal("0")
    shipping_fee: Decimal = Decimal("0")
    total_amt: Decimal = Decimal("0")


@dataclass
class Result:
    source_name: str
    lines: list
    orders: list
    issues: list

    def summary(self) -> dict:
        from collections import Counter

        order_status = Counter(o.status.value for o in self.orders)
        line_status = Counter(l.status.value for l in self.lines)
        sev = Counter(i.severity.value for i in self.issues)
        return {
            "source": self.source_name,
            "line_count": len(self.lines),
            "order_count": len(self.orders),
            "order_status": dict(order_status),
            "line_status": dict(line_status),
            "issues": {"ERROR": sev.get("ERROR", 0), "WARN": sev.get("WARN", 0), "INFO": sev.get("INFO", 0)},
            "supply_amt": str(sum((o.supply_amt for o in self.orders), Decimal("0"))),
            "vat_amt": str(sum((o.vat_amt for o in self.orders), Decimal("0"))),
            "shipping_fee": str(sum((o.shipping_fee for o in self.orders), Decimal("0"))),
            "total_amt": str(sum((o.total_amt for o in self.orders), Decimal("0"))),
        }
