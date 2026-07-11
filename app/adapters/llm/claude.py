"""LLMProvider sobre el SDK de Anthropic (tool use) — Fase 4 (§1.1).

Modelo pinneado a un ID versionado (§1.1), no un alias. Devuelve una respuesta
normalizada (`LLMResponse`) con los tool_use que el agente principal ejecuta y
realimenta. El prompt caching de H3 (§4) se activa por bloque `cache_control`
en el `system` que arma `soporte_rag`.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from anthropic import AsyncAnthropic

from app.domain.models import LLMResponse, ToolCall


class ClaudeProvider:
    def __init__(self, api_key: str, model: str, max_tokens: int = 1024) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        system: Union[str, list[dict], None] = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        resp = await self._client.messages.create(**kwargs)

        texto_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                texto_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, nombre=block.name, argumentos=dict(block.input))
                )

        usage = resp.usage
        return LLMResponse(
            texto="".join(texto_parts) or None,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
        )
