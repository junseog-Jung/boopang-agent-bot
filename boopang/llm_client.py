"""로컬 Ollama LLM 클라이언트 (무료·로컬). 구조화 출력(JSON 스키마)으로 항상 dict 를 돌려준다.

Claude 어댑터(factory/llm.py)와 같은 BaseLLM 인터페이스를 구현하므로 파이프라인은 공급자를 구분하지 않는다.
Ollama 의 네이티브 /api/chat 의 `format`(JSON 스키마) 기능을 사용한다 (Ollama 0.5 이상).
"""
from __future__ import annotations

import json
import logging

import httpx

from .factory.llm import BaseLLM, LLMError

log = logging.getLogger("boopang.ollama")

CHARS_PER_TOKEN = 2.2        # 보수적 추정 (한글/코드 혼합). 실제 토큰 수는 응답의 prompt_eval_count 로 재확인한다
MIN_OUTPUT_TOKENS = 768      # 이보다 출력 여유가 없으면 호출하지 않는다
KEEP_ALIVE = "15m"           # 호출 사이에 모델이 메모리에서 내려가 매번 다시 로드되는 것을 방지


def extract_json(text: str):
    """모델 출력에서 JSON 객체를 꺼낸다. `format` 을 무시하는 구버전/모델이 ```json 펜스나 앞뒤 설명을 붙여도 허용한다."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except ValueError:
        lo, hi = t.find("{"), t.rfind("}")
        if lo != -1 and hi > lo:
            return json.loads(t[lo:hi + 1])
        raise


_TYPES = {"string": str, "boolean": bool, "array": list, "object": dict, "integer": int, "number": (int, float)}


def validate_schema(value, schema: dict, path: str = "$"):
    """JSON 스키마 부분집합(type/enum/required/properties/items) 검사. 위반 설명 문자열 또는 None.
    구조화 출력을 무시하는 구버전 서버·작은 모델의 '문법은 맞지만 타입이 틀린' 응답을 파이프라인에 넣기 전에 걸러낸다."""
    t = schema.get("type")
    if t:
        py = _TYPES.get(t)
        if py is None or not isinstance(value, py) or (t in ("integer", "number") and isinstance(value, bool)):
            return f"{path}: {t} 이어야 하는데 {type(value).__name__} 입니다"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: 허용되지 않는 값입니다 ({str(value)[:30]})"
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                return f"{path}: 필수 키 누락 ({k})"
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                err = validate_schema(value[k], sub, f"{path}.{k}")
                if err:
                    return err
    if t == "array" and "items" in schema:
        for i, item in enumerate(value[:200]):
            err = validate_schema(item, schema["items"], f"{path}[{i}]")
            if err:
                return err
    return None


class OllamaLLM(BaseLLM):
    name = "ollama"

    def __init__(self, host: str, model: str, timeout: float, num_ctx: int, max_calls: int, client: httpx.Client | None = None):
        super().__init__(max_calls)
        self.host, self.model, self.num_ctx = host.rstrip("/"), model, num_ctx
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0))

    def _post(self, body: dict) -> dict:
        try:
            r = self.client.post(f"{self.host}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise LLMError(f"Ollama 서버({self.host})에 연결할 수 없습니다. `ollama serve` 가 실행 중인지 확인하세요.") from exc
        except httpx.TimeoutException as exc:
            raise LLMError("Ollama 응답 시간이 초과되었습니다. BOOPANG_OLLAMA_TIMEOUT 을 늘리거나 더 작은 범위/모델을 쓰세요.") from exc
        except httpx.HTTPError as exc:
            raise LLMError("Ollama 와 통신하는 중 오류가 발생했습니다.") from exc
        if r.status_code == 404:
            raise LLMError(f"모델 '{self.model}' 이 설치되어 있지 않습니다. `ollama pull {self.model}` 로 받으세요.")
        if r.status_code >= 400:
            log.error("ollama http %s: %s", r.status_code, r.text[:200])
            hint = " (구조화 출력은 Ollama 0.5 이상이 필요합니다)" if r.status_code == 400 else ""
            raise LLMError(f"Ollama 오류 (HTTP {r.status_code}){hint}")
        try:
            return r.json()
        except ValueError as exc:
            raise LLMError("Ollama 응답을 해석할 수 없습니다.") from exc

    def _output_budget(self, system: str, user: str, max_tokens: int, label: str) -> int:
        """출력 토큰 상한을 컨텍스트 안에 맞춘다. Ollama 는 컨텍스트를 넘으면 프롬프트 '앞부분'을 조용히 버리므로
        (시스템 프롬프트가 사라질 수 있음) 넘칠 것 같으면 호출 전에 명확한 오류로 알린다."""
        est_prompt = int((len(system) + len(user)) / CHARS_PER_TOKEN)
        budget = self.num_ctx - est_prompt - 64
        if budget < MIN_OUTPUT_TOKENS:
            raise LLMError(f"프롬프트(약 {est_prompt:,} 토큰)가 컨텍스트({self.num_ctx:,})에 비해 너무 큽니다 ({label}). "
                           "BOOPANG_OLLAMA_NUM_CTX 를 늘리거나 파일 수/요구사항 범위를 줄이세요.")
        return min(max_tokens, max(1024, self.num_ctx // 2), budget)

    def _call(self, system, user, schema, label, max_tokens):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        required = schema.get("required", [])
        num_predict = self._output_budget(system, user, max_tokens, label)
        problem = ""
        for attempt in range(2):  # 작은 모델은 형식을 어기는 경우가 있어 1회 교정 재시도
            body = {"model": self.model, "stream": False, "messages": messages, "format": schema, "keep_alive": KEEP_ALIVE,
                    "options": {"temperature": 0.2, "num_ctx": self.num_ctx, "num_predict": num_predict}}
            data = self._post(body)
            self.input_tokens += int(data.get("prompt_eval_count") or 0)
            self.output_tokens += int(data.get("eval_count") or 0)
            text = (data.get("message") or {}).get("content", "")
            if int(data.get("prompt_eval_count") or 0) >= self.num_ctx - 16:  # 입력이 컨텍스트를 가득 채움 → 앞부분이 잘렸을 가능성
                raise LLMError(f"프롬프트가 컨텍스트 한도({self.num_ctx:,})에서 잘렸을 수 있습니다 ({label}). "
                               "BOOPANG_OLLAMA_NUM_CTX 를 늘리거나 범위를 줄이세요.")
            if data.get("done_reason") == "length":
                raise LLMError(f"모델 출력이 길이 한도에서 잘렸습니다 ({label}). 요구사항 범위를 줄이거나 BOOPANG_OLLAMA_NUM_CTX 를 늘리세요.")
            try:
                obj = extract_json(text)
                if not isinstance(obj, dict):
                    raise ValueError("객체가 아님")
                missing = [k for k in required if k not in obj]
                if missing:
                    raise ValueError(f"필수 키 누락: {', '.join(missing)}")
                bad = validate_schema(obj, schema)
                if bad:
                    raise ValueError(bad)
                return obj
            except ValueError as exc:
                problem = str(exc)
                if attempt == 0:
                    self.calls += 1  # 재시도도 호출 예산에 포함
                    messages = messages + [{"role": "assistant", "content": text[:2000]},
                                           {"role": "user", "content": f"Your previous output was invalid ({problem}). Return ONLY valid JSON that matches the schema."}]
        raise LLMError(f"모델 응답이 올바른 JSON 이 아닙니다 ({label}): {problem}")


def ollama_status(host: str, model: str, client: httpx.Client | None = None) -> dict:
    """서버 도달 가능 여부와 모델 설치 여부 (짧은 타임아웃, 예외 없음)."""
    try:
        c = client or httpx.Client(timeout=httpx.Timeout(1.5))
        r = c.get(f"{host.rstrip('/')}/api/tags")
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return {"reachable": False, "model_installed": False, "models": []}
    base = model.split(":")[0]
    installed = model in names or (":" not in model and any(n.split(":")[0] == base for n in names))
    return {"reachable": True, "model_installed": installed, "models": names[:20]}
