"""Dispatcher + AgentRegistry (Registry + Strategy, §7.1) — Fase 2.

En fase 2 solo existe el handler "eco"; en fase 4 se registra el agente Claude
para 'gasto'/'presupuesto' y en fase 5 el de 'soporte'. El orquestador pide un
intent y recibe el handler registrado o el default.
"""

from __future__ import annotations

from app.domain.models import AgentContext, AgentResult, Intencion
from app.domain.ports import AgentHandler


class InMemoryAgentRegistry:
    """Registro de handlers por intención. El primero registrado es el default."""

    def __init__(self) -> None:
        self._handlers: dict[str, AgentHandler] = {}
        self._default: AgentHandler | None = None

    def register(self, handler: AgentHandler) -> None:
        self._handlers[handler.intent] = handler
        if self._default is None:
            self._default = handler

    def get(self, intent: str) -> AgentHandler:
        handler = self._handlers.get(intent) or self._default
        if handler is None:
            raise LookupError("No hay ningún handler registrado en el AgentRegistry.")
        return handler


class EcoHandler:
    """Handler tonto del walking skeleton: repite el mensaje. Muere en fase 4."""

    intent = "eco"

    async def handle(self, context: AgentContext) -> AgentResult:
        return AgentResult(
            respuesta=f"Eco: {context.incoming.texto}",
            intencion=Intencion.OTRO,
            tool_llamada=None,
        )
