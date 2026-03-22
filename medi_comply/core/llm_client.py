"""Universal LLM client with provider abstraction and JSON repair."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

from pydantic import BaseModel

from medi_comply.core.json_repair import JSONRepair


class LLMResponse(BaseModel):
    """Normalized response payload returned by LLMClient."""

    content: str
    parsed_json: Optional[dict] = None
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    provider: str
    success: bool = True
    error: Optional[str] = None


class LLMClient:
    """Universal client capable of talking to multiple LLM providers."""

    SUPPORTED_PROVIDERS = ["openai", "anthropic", "ollama", "mock"]

    def __init__(
        self,
        provider: str = "openai",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        timeout_seconds: int = 60,
        max_context_tokens: int = 12000,
    ) -> None:
        provider = provider.lower()
        if provider not in self.SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")

        self.provider = provider
        self.model = model or self._default_model(provider)
        self.api_key = api_key or os.environ.get(self._env_key(provider))
        self.base_url = base_url
        self.max_retries = max_retries
        self.timeout = timeout_seconds
        self.max_context_tokens = max_context_tokens

        self.total_calls = 0
        self.total_tokens = 0
        self.total_latency_ms = 0.0
        self.errors = 0

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        response_format: str = "json",
    ) -> LLMResponse:
        """Send a chat-style prompt to the configured LLM provider."""

        system_prompt = system_prompt or ""
        user_prompt = user_prompt or ""

        sys_tokens = self._estimate_tokens(system_prompt)
        usr_tokens = self._estimate_tokens(user_prompt)
        if sys_tokens + usr_tokens > self.max_context_tokens:
            allowable = max(self.max_context_tokens - sys_tokens, int(self.max_context_tokens * 0.5))
            user_prompt = self._truncate_prompt(user_prompt, allowable)

        attempt = 0
        error_text = None
        while attempt < self.max_retries:
            attempt += 1
            start_time = time.perf_counter()
            try:
                payload = await self._dispatch_call(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )
                latency = (time.perf_counter() - start_time) * 1000
                parsed = JSONRepair.extract_json(payload.get("content", "")) if response_format == "json" else None

                response = LLMResponse(
                    content=payload.get("content", ""),
                    parsed_json=parsed,
                    model=self.model,
                    prompt_tokens=payload.get("prompt_tokens", 0),
                    completion_tokens=payload.get("completion_tokens", 0),
                    latency_ms=latency,
                    provider=self.provider,
                    success=True,
                )

                self.total_calls += 1
                self.total_tokens += response.prompt_tokens + response.completion_tokens
                self.total_latency_ms += latency
                return response
            except Exception as exc:  # pylint: disable=broad-except
                error_text = str(exc)
                self.errors += 1
                if not self._should_retry(exc):
                    break
                await asyncio.sleep(2 ** (attempt - 1))

        return LLMResponse(
            content="",
            parsed_json=None,
            model=self.model,
            provider=self.provider,
            success=False,
            error=error_text or "Unknown LLM error",
        )

    async def _dispatch_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        response_format: str,
    ) -> dict:
        if self.provider == "openai":
            return await self._call_openai(system_prompt, user_prompt, temperature, max_tokens, response_format)
        if self.provider == "anthropic":
            return await self._call_anthropic(system_prompt, user_prompt, temperature, max_tokens, response_format)
        if self.provider == "ollama":
            return await self._call_ollama(system_prompt, user_prompt, temperature, max_tokens, response_format)
        return await self._call_mock(system_prompt, user_prompt)

    async def _call_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        response_format: str,
    ) -> dict:
        try:
            import openai  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("openai package not installed") from exc

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not provided")

        client = openai.AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        return {
            "content": message.content or "",
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
        }

    async def _call_anthropic(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        response_format: str,
    ) -> dict:
        try:
            import anthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("anthropic package not installed") from exc

        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not provided")

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        adjusted_prompt = user_prompt
        if response_format == "json":
            adjusted_prompt += "\n\nRespond with valid JSON only. No markdown, no explanation outside JSON."

        response = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": adjusted_prompt}],
        )
        content = response.content[0].text if response.content else ""
        return {
            "content": content,
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
        }

    async def _call_ollama(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        response_format: str,
    ) -> dict:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("httpx is required for Ollama provider") from exc

        base = self.base_url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
        adjusted_prompt = user_prompt
        if response_format == "json":
            adjusted_prompt += "\n\nRespond with valid JSON only."

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            response = await http.post(
                f"{base}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": adjusted_prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
            )
            response.raise_for_status()
            data = response.json()
            message = data.get("message", {})
            return {
                "content": message.get("content", ""),
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            }

    async def _call_mock(self, system_prompt: str, user_prompt: str) -> dict:
        """Deterministic mock provider used for tests."""
        content = (
            '{"echo":"mock","selected_code":"TEST-CODE","reasoning":["Mock response"],'
            '"confidence":0.85,"system":"' + system_prompt[:10].replace('"', '') + '"}'
        )
        return {"content": content, "prompt_tokens": 10, "completion_tokens": 5}

    def _should_retry(self, error: Exception) -> bool:
        status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
        if status in (401, 400):
            return False
        if status is not None:
            return status >= 429
        message = str(error).lower()
        retryable_markers = ["rate limit", "timeout", "temporarily unavailable", "server error"]
        return any(marker in message for marker in retryable_markers)

    def _truncate_prompt(self, text: str, max_tokens: int) -> str:
        """Truncate text by keeping the start and end segments."""
        if max_tokens <= 0 or not text:
            return text
        est_tokens = self._estimate_tokens(text)
        if est_tokens <= max_tokens:
            return text
        max_chars = max_tokens * 4
        keep_start = int(max_chars * 0.6)
        keep_end = int(max_chars * 0.3)
        return text[:keep_start] + "\n\n[...content truncated for length...]\n\n" + text[-keep_end:]

    def _estimate_tokens(self, text: str) -> int:
        return max(len(text) // 4, 1)

    def _default_model(self, provider: str) -> str:
        return {
            "openai": "gpt-4o",
            "anthropic": "claude-3-5-sonnet-20241022",
            "ollama": "llama3:8b",
            "mock": "mock-model",
        }.get(provider, "gpt-4o")

    def _env_key(self, provider: str) -> str:
        return {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "ollama": "OLLAMA_URL",
        }.get(provider, "OPENAI_API_KEY")

    def get_stats(self) -> dict:
        """Return aggregate usage statistics."""
        avg_latency = self.total_latency_ms / self.total_calls if self.total_calls else 0.0
        return {
            "total_calls": self.total_calls,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "errors": self.errors,
            "avg_latency_ms": avg_latency,
        }
