"""LLM 어댑터. 구조화 출력(JSON 스키마)으로 항상 dict 를 돌려준다.

- AnthropicLLM: Claude Messages API (공식 SDK). 키는 환경변수(ANTHROPIC_API_KEY 등)에서만 읽고 절대 로그/응답에 쓰지 않는다.
- 호출 횟수 상한(비용 보호)과 토큰 사용량 집계를 BaseLLM 이 담당한다.
"""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger("boopang.factory.llm")


class LLMError(RuntimeError):
    """사용자에게 그대로 보여줘도 안전한 메시지를 담는다 (키/응답 본문 미포함)."""


class LLMBudgetError(LLMError):
    pass


class BaseLLM:
    name = "base"

    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def remaining_calls(self) -> int:
        return self.max_calls - self.calls

    async def json(self, system: str, user: str, schema: dict, *, label: str, max_tokens: int = 16000) -> dict:
        if self.calls >= self.max_calls:
            raise LLMBudgetError(f"LLM 호출 한도({self.max_calls}회)에 도달했습니다")
        self.calls += 1
        return await asyncio.to_thread(self._call, system, user, schema, label, max_tokens)

    def _call(self, system: str, user: str, schema: dict, label: str, max_tokens: int) -> dict:  # pragma: no cover
        raise NotImplementedError


class AnthropicLLM(BaseLLM):
    name = "claude"

    def __init__(self, model: str, effort: str, max_calls: int, client=None):
        super().__init__(max_calls)
        import anthropic

        self._anthropic = anthropic
        self.model, self.effort = model, effort
        self.client = client or anthropic.Anthropic(max_retries=2, timeout=300.0)

    def _call(self, system, user, schema, label, max_tokens):
        a = self._anthropic
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            )
        except a.AuthenticationError as exc:
            raise LLMError("Claude API 인증에 실패했습니다. ANTHROPIC_API_KEY 설정을 확인하세요.") from exc
        except a.PermissionDeniedError as exc:
            raise LLMError("Claude API 권한이 없습니다 (키 권한/모델 접근 확인).") from exc
        except a.RateLimitError as exc:
            raise LLMError("Claude API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.") from exc
        except a.BadRequestError as exc:
            log.error("llm bad request label=%s: %s", label, getattr(exc, "message", ""))
            raise LLMError("Claude API 가 요청을 거부했습니다 (400). 서버 로그를 확인하세요.") from exc
        except a.APIConnectionError as exc:
            raise LLMError("Claude API 에 연결할 수 없습니다. 네트워크를 확인하세요.") from exc
        except a.APIStatusError as exc:
            log.error("llm status error label=%s status=%s", label, exc.status_code)
            raise LLMError(f"Claude API 오류 (HTTP {exc.status_code}). 잠시 후 다시 시도하세요.") from exc
        except Exception as exc:  # 키가 해석되지 않는 경우 등
            if "authentication" in str(exc).lower() or "api_key" in str(exc).lower():
                raise LLMError("Claude API 자격 증명을 찾을 수 없습니다. ANTHROPIC_API_KEY 를 설정하세요.") from exc
            raise

        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            raise LLMError("모델이 이 요청의 처리를 거절했습니다 (안전 정책).")
        if resp.stop_reason == "max_tokens":
            raise LLMError(f"모델 출력이 길이 한도에서 잘렸습니다 ({label}). 요구사항 범위를 줄여 보세요.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"모델 응답이 올바른 JSON 이 아닙니다 ({label}).") from exc
        if not isinstance(data, dict):
            raise LLMError(f"모델 응답 형식이 올바르지 않습니다 ({label}).")
        return data


class ScriptedLLM(BaseLLM):
    """테스트/데모용: label → 응답(dict 또는 callable(user)->dict) 매핑으로 동작한다."""

    name = "scripted"

    def __init__(self, handler, max_calls: int = 100):
        super().__init__(max_calls)
        self.handler = handler
        self.log: list = []

    def _call(self, system, user, schema, label, max_tokens):
        self.log.append(label)
        out = self.handler(label, user)
        if isinstance(out, Exception):
            raise out
        return out
