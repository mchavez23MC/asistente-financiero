"""Orquestador central (§7.2) — Fases 2 y 3.

Pipeline por mensaje entrante, partido en dos etapas (§7.5):

  `preprocess` — corre SÍNCRONO antes del 200 del webhook:
    (0) consentimiento → usuario nuevo recibe el aviso legal y nada más.
    (1) guardrail (denylist → Groq → umbral, fail-closed) → sensible =
        Ticket con contexto + respuesta de escalación; NUNCA llega al agente.
  `run_agent` — solo el trabajo del LLM va en background:
    (2) contexto por request + dispatch al handler registrado.
    (3) audit trail: input → intención → tool → respuesta en `messages` (§7.4).
    (4) responder por el ChannelAdapter.
"""

from __future__ import annotations

from typing import Optional

from app.domain.models import (
    AgentContext,
    AgentResult,
    GuardrailResult,
    IncomingMessage,
    Intencion,
    Message,
    MotivoEscalacion,
    Rol,
    Ticket,
    TicketPrioridad,
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

# Groq caído/timeout (fail-closed §7.3.4): se pide reintento sin prometer humano.
RESPUESTA_FAIL_CLOSED = (
    "Dame un momento 🙏 — no pude procesar tu mensaje con seguridad. "
    "Ya avisé al equipo; puedes intentar de nuevo en unos minutos."
)

# categoria del guardrail → motivo del ticket (valores de MotivoEscalacion).
_MOTIVOS_VALIDOS = {m.value for m in MotivoEscalacion}


def _motivo_de(veredicto: GuardrailResult) -> MotivoEscalacion:
    if veredicto.fuente.startswith("fail_closed"):
        return MotivoEscalacion.GUARDRAIL_FAIL_CLOSED
    if veredicto.categoria in _MOTIVOS_VALIDOS:
        return MotivoEscalacion(veredicto.categoria)
    return MotivoEscalacion.OTRO


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
        """Pipeline completo en una llamada (tests, canales sin webhook)."""
        context = await self.preprocess(incoming)
        if context is not None:
            await self.run_agent(context)

    # ------------------------------------------------------------------ etapa 1
    async def preprocess(self, incoming: IncomingMessage) -> Optional[AgentContext]:
        """Consentimiento + guardrail. Devuelve el contexto para el agente,
        o None si el mensaje ya fue atendido (aviso legal / ruta sensible)."""
        user = self._repo.get_or_create_user(incoming.telefono, incoming.nombre_perfil)

        # --- (0) consentimiento: primer contacto → aviso legal y nada más (§7.2)
        if not user.tiene_consentimiento:
            self._repo.save_message(
                Message(user_id=user.id, rol=Rol.USUARIO, contenido=incoming.texto)
            )
            user = self._repo.registrar_consentimiento(user.id)
            self._audit_respuesta(user.id, AVISO_LEGAL, Intencion.CONSENTIMIENTO)
            await self._channel.send(user, AVISO_LEGAL)
            return None

        # --- (1) guardrail síncrono, antes del agente (§7.3 / §7.5)
        veredicto = await self._guardrail.classify(incoming.texto)
        mensaje = self._repo.save_message(
            Message(user_id=user.id, rol=Rol.USUARIO, contenido=incoming.texto)
        )
        if veredicto.sensible:
            await self._escalar(user, incoming, veredicto, mensaje)
            return None

        return AgentContext(
            user=user,
            incoming=incoming,
            historial=self._repo.get_last_n_messages(user.id, self._historial_n),
            transaccion_pendiente=self._repo.get_pending_transaction(user.id),
        )

    # ------------------------------------------------------------------ etapa 2
    async def run_agent(self, context: AgentContext) -> None:
        """Solo el trabajo del LLM; corre en background tras el 200 (§7.5)."""
        handler = self._registry.get("principal")  # agente Claude (o 'eco' en tests)
        result: AgentResult = await handler.handle(context)
        self._audit_respuesta(
            context.user.id, result.respuesta, result.intencion, result.tool_llamada
        )
        await self._channel.send(context.user, result.respuesta)

    # ------------------------------------------------------------------ internos
    async def _escalar(
        self,
        user,
        incoming: IncomingMessage,
        veredicto: GuardrailResult,
        mensaje: Message,
    ) -> None:
        """Ruta sensible (§7.3): ticket con contexto + respuesta de escalación.
        El mensaje NUNCA llega al agente principal."""
        motivo = _motivo_de(veredicto)
        fail_closed = motivo == MotivoEscalacion.GUARDRAIL_FAIL_CLOSED
        self._repo.create_ticket(
            Ticket(
                user_id=user.id,
                motivo=motivo,
                prioridad=(
                    TicketPrioridad.ALTA
                    if motivo == MotivoEscalacion.FRAUDE
                    else TicketPrioridad.MEDIA
                ),
                contexto=(
                    f"Mensaje: {incoming.texto!r} | guardrail: fuente={veredicto.fuente}, "
                    f"categoria={veredicto.categoria}, confianza={veredicto.confianza:.2f}"
                ),
                mensaje_origen_id=mensaje.id,
            )
        )
        respuesta = RESPUESTA_FAIL_CLOSED if fail_closed else RESPUESTA_SENSIBLE
        self._audit_respuesta(user.id, respuesta, Intencion.SENSIBLE)
        await self._channel.send(user, respuesta)

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
