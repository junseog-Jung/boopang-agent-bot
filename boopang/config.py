"""환경변수 기반 설정. 잘못된 값은 ValueError 로 즉시 실패(fail-fast)한다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

BILL_BASES = ("received", "ordered")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    v = value.strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name}: true/false 로 지정하세요 (현재 값: {value!r})")


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.environ.get(name, default)
    try:
        d = Decimal(raw.strip() or default)
    except InvalidOperation as exc:
        raise ValueError(f"{name}: 숫자가 아닙니다 (현재 값: {raw!r})") from exc
    if not d.is_finite():
        raise ValueError(f"{name}: 유한한 숫자여야 합니다 (현재 값: {raw!r})")
    return d


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}: 숫자가 아닙니다 (현재 값: {raw!r})") from exc
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{name}: 유한한 숫자여야 합니다 (현재 값: {raw!r})")
    return v


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}: 정수가 아닙니다 (현재 값: {raw!r})") from exc


@dataclass(frozen=True)
class Config:
    vat_enabled: bool = True                 # False 면 부가세 0 (면세/영세율)
    vat_rate: Decimal = Decimal("0.10")
    price_includes_vat: bool = False
    bill_basis: str = "received"             # received: min(검수, 주문) / ordered: 주문수량
    free_shipping_threshold: Decimal = Decimal("50000")   # 이 금액 이상 무료. 0 이면 배송비 없음
    shipping_fee: Decimal = Decimal("3000")
    max_upload_bytes: int = 5 * 1024 * 1024
    max_rows: int = 20000
    schema_file: str = ""                    # 빈 값이면 내장 기본 WMS 스키마
    step_delay: float = 0.4                  # 관제 대시보드 단계 간 연출 지연(초). 처리 결과와 무관, 0 이면 즉시

    def __post_init__(self):
        if not (Decimal("0") <= self.vat_rate <= Decimal("1")):
            raise ValueError("BOOPANG_VAT_RATE: 0~1 사이여야 합니다 (예: 0.10)")
        if self.bill_basis not in BILL_BASES:
            raise ValueError(f"BOOPANG_BILL_BASIS: {', '.join(BILL_BASES)} 중 하나여야 합니다")
        if self.free_shipping_threshold < 0 or self.shipping_fee < 0:
            raise ValueError("배송비 관련 값은 0 이상이어야 합니다")
        if not (0 <= self.step_delay <= 5):
            raise ValueError("BOOPANG_STEP_DELAY: 0~5 초 사이여야 합니다")
        if self.max_upload_bytes <= 0 or self.max_rows <= 0:
            raise ValueError("BOOPANG_MAX_UPLOAD_BYTES / BOOPANG_MAX_ROWS 는 1 이상이어야 합니다")

    @property
    def effective_vat_rate(self) -> Decimal:
        return self.vat_rate if self.vat_enabled else Decimal("0")

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vat_enabled=_env_bool("BOOPANG_VAT_ENABLED", True),
            vat_rate=_env_decimal("BOOPANG_VAT_RATE", "0.10"),
            price_includes_vat=_env_bool("BOOPANG_PRICE_INCLUDES_VAT", False),
            bill_basis=os.environ.get("BOOPANG_BILL_BASIS", "received").strip().lower() or "received",
            free_shipping_threshold=_env_decimal("BOOPANG_FREE_SHIPPING_THRESHOLD", "50000"),
            shipping_fee=_env_decimal("BOOPANG_SHIPPING_FEE", "3000"),
            max_upload_bytes=_env_int("BOOPANG_MAX_UPLOAD_BYTES", 5 * 1024 * 1024),
            max_rows=_env_int("BOOPANG_MAX_ROWS", 20000),
            schema_file=os.environ.get("BOOPANG_SCHEMA_FILE", "").strip(),
            step_delay=_env_float("BOOPANG_STEP_DELAY", 0.4),
        )
