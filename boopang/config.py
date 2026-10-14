"""환경변수 기반 설정. 잘못된 값은 ValueError 로 즉시 실패(fail-fast)한다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

BILL_BASES = ("received", "ordered")
PROVIDERS = ("anthropic", "ollama", "demo")
EXEC_MODES = ("off", "docker", "local")


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


def _normalize_host(raw: str) -> str:
    """OLLAMA_HOST 는 'host:port' 형태로도 쓰이므로 스킴을 보정하고 0.0.0.0 은 클라이언트용으로 바꾼다."""
    h = raw.strip() or "http://localhost:11434"
    if "://" not in h:
        h = "http://" + h
    return h.replace("//0.0.0.0", "//127.0.0.1").rstrip("/")


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
    llm_provider: str = "anthropic"          # anthropic | ollama | demo
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b-instruct"
    ollama_timeout: float = 600.0            # CPU 추론은 느리므로 넉넉히 (초)
    ollama_num_ctx: int = 8192               # Ollama 기본 컨텍스트(2~4K)는 코드 생성에 너무 작다
    factory_mcp: bool = True                 # 파일 기록/조회/실행을 MCP 서버(stdio) 경유로 수행
    factory_exec: str = "off"                # 생성 코드 테스트 실행: off | docker(격리) | local(비격리, 위험)
    sandbox_image: str = "boopang-sandbox"
    exec_timeout: int = 60
    workspace_dir: str = ""                  # 비우면 시스템 임시 폴더 아래 boopang-workspaces
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
        if self.llm_provider not in PROVIDERS:
            raise ValueError(f"BOOPANG_LLM_PROVIDER: {', '.join(PROVIDERS)} 중 하나여야 합니다")
        if self.factory_exec not in EXEC_MODES:
            raise ValueError(f"BOOPANG_FACTORY_EXEC: {', '.join(EXEC_MODES)} 중 하나여야 합니다")
        if not self.ollama_host.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_HOST: http:// 또는 https:// 로 시작해야 합니다")
        if not self.ollama_model.strip():
            raise ValueError("BOOPANG_OLLAMA_MODEL: 비어 있을 수 없습니다")
        if not (1 <= self.ollama_timeout <= 3600) or not (2048 <= self.ollama_num_ctx <= 131072):
            raise ValueError("BOOPANG_OLLAMA_TIMEOUT(1~3600초) / BOOPANG_OLLAMA_NUM_CTX(2048~131072) 범위를 확인하세요")
        if not (5 <= self.exec_timeout <= 300):
            raise ValueError("BOOPANG_EXEC_TIMEOUT: 5~300초 사이여야 합니다")
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
    def demo(self) -> bool:
        return self.llm_provider == "demo" or self.factory_demo

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
            llm_provider="demo" if _env_bool("BOOPANG_FACTORY_DEMO", False)
            else (os.environ.get("BOOPANG_LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"),
            ollama_host=_normalize_host(os.environ.get("BOOPANG_OLLAMA_HOST") or os.environ.get("OLLAMA_HOST") or ""),
            ollama_model=os.environ.get("BOOPANG_OLLAMA_MODEL", "qwen2.5-coder:7b-instruct").strip() or "qwen2.5-coder:7b-instruct",
            ollama_timeout=_env_float("BOOPANG_OLLAMA_TIMEOUT", 600.0),
            ollama_num_ctx=_env_int("BOOPANG_OLLAMA_NUM_CTX", 8192),
            factory_mcp=_env_bool("BOOPANG_FACTORY_MCP", True),
            factory_exec=os.environ.get("BOOPANG_FACTORY_EXEC", "off").strip().lower() or "off",
            sandbox_image=os.environ.get("BOOPANG_SANDBOX_IMAGE", "boopang-sandbox").strip() or "boopang-sandbox",
            exec_timeout=_env_int("BOOPANG_EXEC_TIMEOUT", 60),
            workspace_dir=os.environ.get("BOOPANG_WORKSPACE_DIR", "").strip(),
        )
