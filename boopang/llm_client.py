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

    def _call(self, system, user, schema, label, max_tokens):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        required = schema.get("required", [])
        problem = ""
        for attempt in range(2):  # 작은 모델은 형식을 어기는 경우가 있어 1회 교정 재시도
            body = {"model": self.model, "stream": False, "messages": messages, "format": schema,
                    "options": {"temperature": 0.2, "num_ctx": self.num_ctx, "num_predict": max_tokens}}
            data = self._post(body)
            self.input_tokens += int(data.get("prompt_eval_count") or 0)
            self.output_tokens += int(data.get("eval_count") or 0)
            text = (data.get("message") or {}).get("content", "")
            if data.get("done_reason") == "length":
                raise LLMError(f"모델 출력이 길이 한도에서 잘렸습니다 ({label}). 요구사항 범위를 줄이거나 BOOPANG_OLLAMA_NUM_CTX 를 늘리세요.")
            try:
                obj = json.loads(text)
                if not isinstance(obj, dict):
                    raise ValueError("객체가 아님")
                missing = [k for k in required if k not in obj]
                if missing:
                    raise ValueError(f"필수 키 누락: {', '.join(missing)}")
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
