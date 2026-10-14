"""금액 계산 (Decimal, 원 단위 HALF_UP)."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .config import Config
from .models import Line, Order, Status

_NON_BILLABLE = {Status.CANCELLED, Status.RETURNED, Status.HOLD}
ONE = Decimal("1")


def _won(v: Decimal) -> Decimal:
    return v.quantize(ONE, rounding=ROUND_HALF_UP)


def billable_qty(line: Line) -> int:
    """청구 수량: 검수수량(주문수량 상한), 미검수면 주문수량."""
    if line.qty_received is None:
        return line.qty_ordered
    return min(line.qty_received, line.qty_ordered)


def price_line(line: Line, cfg: Config) -> None:
    line.billable = line.status not in _NON_BILLABLE
    if not line.billable:
        line.supply_amt = line.vat_amt = line.total_amt = Decimal("0")
        return
    gross = Decimal(billable_qty(line)) * line.unit_price * (Decimal("1") - line.discount_rate)
    if cfg.price_includes_vat:
        total = _won(gross)
        supply = _won(total / (Decimal("1") + cfg.vat_rate))
        vat = total - supply
    else:
        supply = _won(gross)
        vat = _won(supply * cfg.vat_rate)
        total = supply + vat
    line.supply_amt, line.vat_amt, line.total_amt = supply, vat, total


def price_order(order: Order, cfg: Config) -> None:
    billable = [l for l in order.lines if l.billable]
    order.supply_amt = sum((l.supply_amt for l in billable), Decimal("0"))
    order.vat_amt = sum((l.vat_amt for l in billable), Decimal("0"))
    goods = order.supply_amt + order.vat_amt
    order.shipping_fee = Decimal("0")
    if billable and goods < cfg.free_shipping_threshold:
        order.shipping_fee = cfg.shipping_fee
    order.total_amt = goods + order.shipping_fee


def price_all(lines: list[Line], orders: list[Order], cfg: Config) -> None:
    for ln in lines:
        price_line(ln, cfg)
    for o in orders:
        price_order(o, cfg)
