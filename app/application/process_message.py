"""Orquestador central (§7.2) — Fase 2.

Pipeline por mensaje entrante:
  (0) consentimiento  → usuario nuevo recibe el aviso legal; sin consentimiento
      no se procesa nada más.
  (1) guardrail       → stub en fase 2 (siempre sensible=False); real en fase 3.
  (2) router/dispatch → handler registrado ('eco' en fase 2, Claude en fase 4).
  (3) audit           → input → intención → tool → respuesta en `messages`
      (§7.4). Entra AHORA, no después: es estructural y las fases siguientes
      quedan auditadas gratis.
  (4) responder       → por el ChannelAdapter (API REST, no TwiML).
"""

from __future__ import annotations

from app.domain.models import (
    AgentContext,
    AgentResult,
    GuardrailResult,
    IncomingMessage,
    Intencion,
    Message,
    Rol,
)
from app.domain.ports import AgentRegistry, ChannelAdapter, Guardrail, Repository

# Placeholder — la redacción final la coordina T6 (fase 2, paso 0).
AVISO_LEGAL = (
    "👋 Hola, soy tu asistente financiero. Antes de empezar: guardo tus mensajes "
    "y transacciones para poder ayudarte, y un humano puede revisarlos si escalo "
    "tu caso. No doy consejos de inversión. Al seguir escribiendo aceptas estos "
    "términos. ¿En qué te ayudo?"
)

RESPUESTA_SENSIBLE = (
    "Tu consulta necesita atención de una persona de nuestro equipo. "
    "Ya creé un caso y te contactaremos pronto. 🙋"
)


class StubGuardrail:
    """Paso (1) en fase 2: deja pasar todo. Se reemplaza en fase 3 (§7.3)."""

    async def classify(self, texto: str) -> GuardrailResult:
        return GuardrailResult(sensible=False, fuente="stub")


class ProcessMessage:
    """Caso de uso central. Solo conoce puertos; los adaptadores los inyecta main."""

    def __init__(
        self,
        repo: Repository,
        guardrail: Guardrail,
        registry: AgentRegistry,
        channel: ChannelAdapter,
        historial_n: int = 10,
    ) -> None:
        self._repo = repo
        self._guardrail = guardrail
        self._registry = registry
        self._channel = channel
        self._historial_n = historial_n

    async def __call__(self, incoming: IncomingMessage) -> None:
        user = self._repo.get_or_create_user(incoming.telefono, incoming.nombre_perfil)

        # --- (0) consentimiento: primer contacto → aviso legal y nada más (§7.2)
        if not user.tiene_consentimiento:
            self._repo.save_message(
                Message(user_id=user.id, rol=Rol.USUARIO, contenido=incoming.texto)
            )
            user = self._repo.registrar_consentimiento(user.id)
            self._audit_respuesta(user.id, AVISO_LEGAL, Intencion.CONSENTIMIENTO)
            await self._channel.send(user, AVISO_LEGAL)
            return

        # --- (1) guardrail (stub en fase 2; síncrono antes del agente, §7.5)
        veredicto = await self._guardrail.classify(incoming.texto)
        self._repo.save_message(
            Message(user_id=user.id, rol=Rol.USUARIO, contenido=incoming.texto)
        )
        if veredicto.sensible:
            # La ruta completa (Ticket con contexto) se implementa en fase 3.
            self._audit_respuesta(user.id, RESPUESTA_SENSIBLE, Intencion.SENSIBLE)
            await self._channel.send(user, RESPUESTA_SENSIBLE)
            return

        # --- (2) contexto por request (§3.1) + dispatch
        context = AgentContext(
            user=user,
            incoming=incoming,
            historial=self._repo.get_last_n_messages(user.id, self._historial_n),
            transaccion_pendiente=self._repo.get_pending_transaction(user.id),
        )
        handler = self._registry.get("eco")  # fase 4: el router decide el intent
        result: AgentResult = await handler.handle(context)

        # --- (3) audit + (4) responder
        self._audit_respuesta(user.id, result.respuesta, result.intencion, result.tool_llamada)
        await self._channel.send(user, result.respuesta)

    def _audit_respuesta(
        self,
        user_id,
        respuesta: str,
        intencion: Intencion,
        tool_llamada: str | None = None,
    ) -> None:
        """Escribe la respuesta del asistente con intención y tool (audit trail §7.4)."""
        self._repo.save_message(
            Message(
                user_id=user_id,
                rol=Rol.ASISTENTE,
                contenido=respuesta,
                intencion=intencion,
                tool_llamada=tool_llamada,
            )
        )
