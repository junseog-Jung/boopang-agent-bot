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
    factory_enabled: bool = False            # 범용 소프트웨어 팩토리(유료 LLM 호출) 사용 허용
    factory_demo: bool = False               # 키 없이 쓰는 고정 샘플 데모 모드 (요청과 무관한 샘플 생성)
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"               # low | medium | high | xhigh | max
    factory_max_files: int = 25
    factory_max_file_bytes: int = 60_000
    factory_max_total_bytes: int = 400_000
    factory_max_calls: int = 40              # 작업 1건당 LLM 호출 상한 (비용 보호)
    factory_max_jobs_per_hour: int = 10
    step_delay: float = 0.4                  # 관제 대시보드 단계 간 연출 지연(초). 처리 결과와 무관, 0 이면 즉시

    def __post_init__(self):
        if not (Decimal("0") <= self.vat_rate <= Decimal("1")):
            raise ValueError("BOOPANG_VAT_RATE: 0~1 사이여야 합니다 (예: 0.10)")
        if self.bill_basis not in BILL_BASES:
            raise ValueError(f"BOOPANG_BILL_BASIS: {', '.join(BILL_BASES)} 중 하나여야 합니다")
        if self.free_shipping_threshold < 0 or self.shipping_fee < 0:
            raise ValueError("배송비 관련 값은 0 이상이어야 합니다")
        if self.llm_effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ValueError("BOOPANG_LLM_EFFORT: low, medium, high, xhigh, max 중 하나여야 합니다")
        if not self.llm_model.strip():
            raise ValueError("BOOPANG_LLM_MODEL: 비어 있을 수 없습니다")
        for name, v in (("FACTORY_MAX_FILES", self.factory_max_files), ("FACTORY_MAX_FILE_BYTES", self.factory_max_file_bytes),
                        ("FACTORY_MAX_TOTAL_BYTES", self.factory_max_total_bytes), ("FACTORY_MAX_CALLS", self.factory_max_calls),
                        ("FACTORY_MAX_JOBS_PER_HOUR", self.factory_max_jobs_per_hour)):
            if v <= 0:
                raise ValueError(f"BOOPANG_{name}: 1 이상이어야 합니다")
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
            factory_enabled=_env_bool("BOOPANG_FACTORY_ENABLED", False),
            factory_demo=_env_bool("BOOPANG_FACTORY_DEMO", False),
            llm_model=os.environ.get("BOOPANG_LLM_MODEL", "claude-opus-5-5").strip() or "claude-opus-5-5",
            llm_effort=os.environ.get("BOOPANG_LLM_EFFORT", "medium").strip().lower() or "medium",
            factory_max_files=_env_int("BOOPANG_FACTORY_MAX_FILES", 25),
            factory_max_file_bytes=_env_int("BOOPANG_FACTORY_MAX_FILE_BYTES", 60_000),
            factory_max_total_bytes=_env_int("BOOPANG_FACTORY_MAX_TOTAL_BYTES", 400_000),
            factory_max_calls=_env_int("BOOPANG_FACTORY_MAX_CALLS", 40),
            factory_max_jobs_per_hour=_env_int("BOOPANG_FACTORY_MAX_JOBS_PER_HOUR", 10),
        )
