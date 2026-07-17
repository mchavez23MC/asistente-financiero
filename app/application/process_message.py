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

import asyncio
import contextlib
import logging
import time
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
from app.application.memoria import MemoriaSemantica
from app.application.escalacion import en_cooldown, pide_humano
from app.domain.ports import AgentRegistry, ChannelAdapter, Guardrail, Repository

log = logging.getLogger("e5.orquestador")

# Onboarding en la voz de Luca (paso 0): breve presentación de quién es y qué
# hace + el aviso legal obligatorio (consentimiento). Un usuario nuevo recibe
# SOLO esto; el agente no corre hasta que hay consentimiento (§7.2).
AVISO_LEGAL = (
    "👋 ¡Hola! Soy Luca, tu asistente financiero personal por WhatsApp. "
    "Te doy una mano con tu plata del día a día:\n"
    "• Anoto tus gastos e ingresos — escríbeme o mándame la foto del recibo\n"
    "• Llevo tu presupuesto y te aviso cómo vas\n"
    "• Te muestro y corrijo lo que registraste\n"
    "• Resuelvo dudas sencillas y, si hace falta, te paso con una persona\n\n"
    "Antes de arrancar: guardo tus mensajes y movimientos para poder ayudarte, "
    "y un humano de mi equipo puede revisarlos si escalo tu caso. No doy "
    "consejos de inversión. Al seguir escribiendo aceptas estos términos. "
    "¿En qué te ayudo?"
)

# Ruta sensible (pre-Luca): Luca DECLINA con calidez, SIN crear ticket. Estos
# temas (inversión, fraude, reclamos regulatorios) no los maneja; pero deja
# abierta la puerta a una persona por si el usuario de verdad la necesita.
RESPUESTA_SENSIBLE = (
    "Uy, sobre eso no te puedo ayudar 🙏 No manejo temas de inversiones, "
    "fraudes ni reclamos formales. Aquí sigo para todo lo de tus gastos, "
    "ingresos y presupuesto. Si necesitas, dime 'quiero hablar con una "
    "persona' y te conecto con alguien de mi equipo."
)

# Pedido explícito de humano atendido: se creó el ticket.
RESPUESTA_TICKET_CREADO = (
    "Listo 🙌 ya le pasé tu caso a una persona de mi equipo; te contactan en "
    "breve. Mientras tanto, aquí sigo para lo de tus gastos."
)

# Pedido de humano pero ya hay un ticket reciente (límite de 1 cada 5 horas):
# no se crea otro, se le dice que su caso ya está en cola.
RESPUESTA_TICKET_EN_COLA = (
    "Tu caso ya está en cola con una persona de mi equipo 🙌 te van a "
    "contactar. Mientras tanto, aquí sigo para lo de tus gastos."
)

# Groq caído/timeout (fail-closed §7.3.4): se pide reintento sin prometer humano
# y SIN crear ticket (nadie lo pidió).
RESPUESTA_FAIL_CLOSED = (
    "Dame un momentito 🙏 — no pude procesar tu mensaje con seguridad. "
    "Prueba de nuevo en unos minutos, ñaño."
)

# Aviso de espera cuando el agente tarda más del umbral (§7.5): que el usuario
# sepa que su mensaje se está procesando y no quedó en el vacío. Es UX de
# transporte, NO una respuesta sustantiva → no se audita ni entra al historial.
AVISO_ESPERA = "Dame un segundito 🙌 ya te respondo…"
AVISO_ESPERA_MEDIA = "Dame un momentito 👀 estoy revisando lo que me enviaste…"

# categoria del guardrail → motivo del ticket (valores de MotivoEscalacion).
_MOTIVOS_VALIDOS = {m.value for m in MotivoEscalacion}

# El clasificador (documento de blindaje) fusiona reclamo+regulatorio en una sola
# categoría; se traduce a un motivo válido del ticket.
_CATEGORIA_CLASIFICADOR_A_MOTIVO = {
    "reclamo_regulatorio": MotivoEscalacion.REGULATORIO,
}


def _motivo_de(veredicto: GuardrailResult) -> MotivoEscalacion:
    if veredicto.fuente.startswith("fail_closed"):
        return MotivoEscalacion.GUARDRAIL_FAIL_CLOSED
    if veredicto.categoria in _MOTIVOS_VALIDOS:
        return MotivoEscalacion(veredicto.categoria)
    if veredicto.categoria in _CATEGORIA_CLASIFICADOR_A_MOTIVO:
        return _CATEGORIA_CLASIFICADOR_A_MOTIVO[veredicto.categoria]
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
        aviso_espera_umbral_s: float = 2.0,
        memoria: Optional[MemoriaSemantica] = None,
    ) -> None:
        self._repo = repo
        self._guardrail = guardrail
        self._registry = registry
        self._channel = channel
        self._historial_n = historial_n
        # Si el agente tarda más que esto, se manda un aviso de espera al usuario.
        self._aviso_espera_umbral_s = aviso_espera_umbral_s
        # Memoria semántica (Parte A). None = apagada: el asistente usa solo la
        # ventana reciente (comportamiento previo a esta feature).
        self._memoria = memoria

    async def __call__(self, incoming: IncomingMessage) -> None:
        """Pipeline completo en una llamada (tests, canales sin webhook)."""
        context = await self.preprocess(incoming)
        if context is not None:
            await self.run_agent(context)

    # ------------------------------------------------------------------ etapa 1
    async def preprocess(self, incoming: IncomingMessage) -> Optional[AgentContext]:
        """Consentimiento + guardrail. Devuelve el contexto para el agente,
        o None si el mensaje ya fue atendido (aviso legal / ruta sensible).

        El guardrail (Groq, ~0.5s) NO depende del usuario, así que corre en
        paralelo con la lectura del usuario en Supabase (~0.5s). Las llamadas
        síncronas al repo van en hilos (`to_thread`) para no congelar el event
        loop (afectaría a otros usuarios, al scheduler y al panel)."""
        t0 = time.perf_counter()

        # Un adjunto sin caption no trae texto: el guardrail y el audit trail
        # trabajan sobre una descripción textual ('[imagen]', etc.).
        contenido = incoming.contenido_para_audit

        # --- lectura de usuario y guardrail EN PARALELO (§7.3 / §7.5) ----------
        # El guardrail sigue resolviéndose antes de que nada llegue al agente;
        # solo se solapa con I/O que no depende de su veredicto.
        user, veredicto = await asyncio.gather(
            asyncio.to_thread(
                self._repo.get_or_create_user, incoming.telefono, incoming.nombre_perfil
            ),
            self._guardrail.classify(contenido),
        )

        # --- (0) consentimiento: primer contacto → aviso legal y nada más (§7.2)
        if not user.tiene_consentimiento:
            await asyncio.to_thread(
                self._repo.save_message,
                Message(user_id=user.id, rol=Rol.USUARIO, contenido=contenido),
            )
            user = await asyncio.to_thread(self._repo.registrar_consentimiento, user.id)
            await self._enviar_seguro(user, AVISO_LEGAL)
            await self._audit_respuesta(user.id, AVISO_LEGAL, Intencion.CONSENTIMIENTO)
            return None

        mensaje = await asyncio.to_thread(
            self._repo.save_message,
            Message(user_id=user.id, rol=Rol.USUARIO, contenido=contenido),
        )
        # Escalación SOLO si el usuario pide explícitamente una persona (§escalación):
        # ese pedido tiene prioridad sobre la ruta sensible — si pide humano sobre
        # un tema sensible, se lo conectamos igual (respetando el límite de 5h).
        if pide_humano(contenido):
            await self._escalar_por_pedido(user, incoming, veredicto, mensaje)
            return None
        # Tema sensible sin pedido de humano: se DECLINA, sin crear ticket.
        if veredicto.sensible:
            await self._responder_sensible(user, veredicto)
            return None

        # --- contexto: historial + transacción pendiente en paralelo ----------
        historial, pendiente = await asyncio.gather(
            asyncio.to_thread(self._repo.get_last_n_messages, user.id, self._historial_n),
            asyncio.to_thread(self._repo.get_pending_transaction, user.id),
        )
        log.info(
            "preprocess %.0fms guardrail=%s", (time.perf_counter() - t0) * 1000, veredicto.fuente
        )
        return AgentContext(
            user=user,
            incoming=incoming,
            historial=historial,
            mensaje_id=mensaje.id,
            transaccion_pendiente=pendiente,
        )

    # ------------------------------------------------------------------ etapa 2
    async def run_agent(self, context: AgentContext) -> None:
        """Solo el trabajo del LLM; corre en background tras el 200 (§7.5).
        Se envía primero y se audita después: el usuario ve la respuesta sin
        esperar la escritura del audit trail (que igual queda garantizado).

        Si el agente tarda más del umbral, una tarea vigía manda un aviso de
        espera ('ya te respondo…') para que el usuario sepa que se está
        procesando. Al terminar se cancela la vigía y se espera su cierre limpio
        ANTES de enviar la respuesta real: así el aviso, si salió, siempre llega
        antes que la respuesta (nunca después)."""
        t0 = time.perf_counter()
        # Memoria semántica (Parte A): se recupera ANTES de arrancar el agente y
        # la vigía, para que su latencia no cuente hacia el aviso de espera. Va
        # aquí (background, tras el 200) y no en preprocess, para no demorar la
        # respuesta del webhook.
        if self._memoria is not None:
            await self._recuperar_memoria(context)
        handler = self._registry.get("principal")  # agente Claude (o 'eco' en tests)
        vigia = asyncio.create_task(self._aviso_si_tarda(context))
        try:
            result: AgentResult = await handler.handle(context)
        finally:
            vigia.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await vigia
        await self._enviar_seguro(context.user, result.respuesta)
        mensaje_asistente = await self._audit_respuesta(
            context.user.id, result.respuesta, result.intencion, result.tool_llamada
        )
        # Indexar (mensaje del usuario + respuesta) para recuperación futura. Va
        # después de responder: el usuario ya vio la respuesta; esto solo alimenta
        # la memoria y nunca la puede tumbar (indexar traga sus propios fallos).
        if self._memoria is not None:
            await self._indexar_memoria(context, mensaje_asistente)
        log.info(
            "agente %.0fms tool=%s intención=%s",
            (time.perf_counter() - t0) * 1000,
            result.tool_llamada,
            result.intencion,
        )

    async def _recuperar_memoria(self, context: AgentContext) -> None:
        """Rellena `context.memoria_relevante` y `context.hechos_usuario` con la
        recuperación híbrida (§ Parte A). Blindado en MemoriaSemantica: ante
        fallo deja las listas vacías y el agente responde igual."""
        recuerdos, hechos = await self._memoria.recordar(
            context.user.id, context.incoming.contenido_para_audit, context.historial
        )
        context.memoria_relevante = recuerdos
        context.hechos_usuario = hechos

    async def _indexar_memoria(self, context: AgentContext, mensaje_asistente) -> None:
        # Mensaje del usuario + respuesta del asistente se indexan en UN solo
        # lote (una llamada de embeddings) para reducir peticiones a Voyage.
        items: list[tuple] = []
        if context.mensaje_id is not None:
            items.append((context.mensaje_id, context.incoming.contenido_para_audit))
        if mensaje_asistente is not None and mensaje_asistente.id is not None:
            items.append((mensaje_asistente.id, mensaje_asistente.contenido))
        if items:
            await self._memoria.indexar_lote(items, context.user.id)

    async def _aviso_si_tarda(self, context: AgentContext) -> None:
        """Vigía: espera el umbral y, si no la cancelaron antes (el agente aún no
        terminó), manda el aviso de espera. Cancelada = el agente respondió a
        tiempo → no se manda nada. El aviso no se audita: es UX, no una respuesta."""
        await asyncio.sleep(self._aviso_espera_umbral_s)
        aviso = AVISO_ESPERA_MEDIA if context.incoming.media else AVISO_ESPERA
        await self._enviar_seguro(context.user, aviso)
        log.info("aviso de espera enviado a %s (agente > %ss)", context.user.id, self._aviso_espera_umbral_s)

    # ------------------------------------------------------------------ internos
    async def _escalar_por_pedido(
        self,
        user,
        incoming: IncomingMessage,
        veredicto: GuardrailResult,
        mensaje: Message,
    ) -> None:
        """El usuario pidió EXPLÍCITAMENTE una persona (§escalación): se crea el
        ticket, salvo que ya tenga uno reciente (límite de 1 cada 5h) — en cuyo
        caso se le avisa que su caso ya está en cola. El mensaje NUNCA llega al
        agente principal. Si el pedido venía sobre un tema sensible, el motivo/
        prioridad se toman del guardrail; si no, es un pedido de ayuda ('otro')."""
        ultimo = await asyncio.to_thread(self._repo.latest_ticket_at, user.id)
        if en_cooldown(ultimo):
            await self._enviar_seguro(user, RESPUESTA_TICKET_EN_COLA)
            await self._audit_respuesta(user.id, RESPUESTA_TICKET_EN_COLA, Intencion.SENSIBLE)
            return

        motivo = _motivo_de(veredicto) if veredicto.sensible else MotivoEscalacion.OTRO
        await asyncio.to_thread(
            self._repo.create_ticket,
            Ticket(
                user_id=user.id,
                motivo=motivo,
                prioridad=(
                    TicketPrioridad.ALTA
                    if motivo == MotivoEscalacion.FRAUDE
                    else TicketPrioridad.MEDIA
                ),
                contexto=(
                    f"Pedido explícito de humano. Mensaje: {incoming.texto!r} | "
                    f"guardrail: fuente={veredicto.fuente}, categoria={veredicto.categoria}, "
                    f"confianza={veredicto.confianza:.2f}"
                ),
                mensaje_origen_id=mensaje.id,
            ),
        )
        await self._enviar_seguro(user, RESPUESTA_TICKET_CREADO)
        await self._audit_respuesta(user.id, RESPUESTA_TICKET_CREADO, Intencion.SENSIBLE)

    async def _responder_sensible(self, user, veredicto: GuardrailResult) -> None:
        """Tema sensible (inversión, fraude, reclamo regulatorio) sin pedido de
        humano: se DECLINA con calidez, SIN crear ticket. El mensaje NUNCA llega
        al agente principal (§7.3). El fail-closed (Groq caído) pide reintento."""
        fail_closed = _motivo_de(veredicto) == MotivoEscalacion.GUARDRAIL_FAIL_CLOSED
        respuesta = RESPUESTA_FAIL_CLOSED if fail_closed else RESPUESTA_SENSIBLE
        await self._enviar_seguro(user, respuesta)
        await self._audit_respuesta(user.id, respuesta, Intencion.SENSIBLE)

    async def _enviar_seguro(self, user, texto: str) -> None:
        """Envía por el canal sin propagar fallos de entrega. Un 4xx/5xx del
        canal (ej. Meta) no debe tirar el webhook (evita reintentos → duplicados)
        ni ensuciar el ciclo con un traceback. El mensaje ya quedó auditado."""
        try:
            await self._channel.send(user, texto)
        except Exception:
            log.exception("No se pudo entregar el mensaje al usuario %s", user.id)

    async def _audit_respuesta(
        self,
        user_id,
        respuesta: str,
        intencion: Intencion,
        tool_llamada: str | None = None,
    ) -> Message:
        """Escribe la respuesta del asistente con intención y tool (audit trail §7.4).
        El insert va en un hilo para no bloquear el event loop. Devuelve el
        mensaje guardado (con id) para que la memoria semántica pueda indexarlo."""
        return await asyncio.to_thread(
            self._repo.save_message,
            Message(
                user_id=user_id,
                rol=Rol.ASISTENTE,
                contenido=respuesta,
                intencion=intencion,
                tool_llamada=tool_llamada,
            ),
        )
