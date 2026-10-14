"""환경변수 기반 설정."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_decimal(name: str, default: str) -> Decimal:
    return Decimal(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    vat_rate: Decimal = Decimal("0.10")
    price_includes_vat: bool = False
    free_shipping_threshold: Decimal = Decimal("50000")
    shipping_fee: Decimal = Decimal("3000")
    max_upload_bytes: int = 5 * 1024 * 1024
    max_rows: int = 20000

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vat_rate=_env_decimal("BOOPANG_VAT_RATE", "0.10"),
            price_includes_vat=_env_bool("BOOPANG_PRICE_INCLUDES_VAT", False),
            free_shipping_threshold=_env_decimal("BOOPANG_FREE_SHIPPING_THRESHOLD", "50000"),
            shipping_fee=_env_decimal("BOOPANG_SHIPPING_FEE", "3000"),
            max_upload_bytes=_env_int("BOOPANG_MAX_UPLOAD_BYTES", 5 * 1024 * 1024),
            max_rows=_env_int("BOOPANG_MAX_ROWS", 20000),
        )
