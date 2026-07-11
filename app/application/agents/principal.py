"""Agente principal Claude con tools (opción C, §1) — Fase 4.

UN solo agente maneja H1 (gasto), H2 (presupuesto) y deriva H3 (soporte) por
tool. Reemplaza al handler 'eco'. El bucle de tool use ejecuta las tools sobre
el `Repository` (resolviendo `user_id` desde el contexto, NUNCA desde el modelo,
§7.3.2) y realimenta los resultados hasta que Claude produce texto final.

Mensajes mixtos ("gasté 25 y cómo va mi presupuesto") se resuelven en una sola
pasada porque el agente puede llamar varias tools (por eso la opción C, §1).
"""

from __future__ import annotations

import json
from uuid import UUID

from app.application.agents.gasto import registrar_gasto
from app.application.agents.presupuesto import consultar_presupuesto
from app.application.agents.soporte_rag import SoporteRAG
from app.domain.models import (
    AgentContext,
    AgentResult,
    Intencion,
    Message,
    MotivoEscalacion,
    Ticket,
    TicketPrioridad,
)
from app.domain.ports import LLMProvider, Repository
from app.domain.tools import TOOLS

SYSTEM_PROMPT = """\
Eres un asistente financiero personal que conversa por WhatsApp, en español, \
con un tono cercano y claro. Ayudas con tres cosas:

1. Registrar gastos: cuando el usuario menciona un gasto, llama a `registrar_gasto`.
   Si falta el monto, la transacción queda pendiente: pide amablemente el monto.
2. Consultar presupuesto: cuando pregunta cuánto lleva gastado o cómo va su \
   presupuesto, llama a `consultar_presupuesto`. EL SISTEMA CALCULA LOS NÚMEROS; \
   tú solo los explicas. NUNCA sumes ni estimes totales por tu cuenta.
3. Soporte sobre el servicio: para preguntas de cómo funciona el asistente, \
   llama a `responder_soporte`. Si esa tool indica que no está en el corpus, \
   discúlpate y llama a `crear_ticket`.

Reglas firmes:
- NUNCA des consejos de inversión ni recomendaciones sobre en qué poner el dinero.
- Un mismo mensaje puede requerir varias tools (ej. registrar un gasto y consultar \
  presupuesto): úsalas todas antes de responder.
- Responde siempre en mensajes cortos, aptos para WhatsApp. Usa los datos que \
  devuelven las tools; no inventes montos, fechas ni políticas.
- Cuando registres un gasto confirmado, confírmalo en una frase con el monto."""

# tool → intención para el audit trail (§7.4). La última tool específica gana.
_INTENCION_POR_TOOL = {
    "registrar_gasto": Intencion.GASTO,
    "consultar_presupuesto": Intencion.PRESUPUESTO,
    "responder_soporte": Intencion.SOPORTE,
}

_MOTIVOS_VALIDOS = {m.value for m in MotivoEscalacion}
_PRIORIDADES_VALIDAS = {p.value for p in TicketPrioridad}

_ARGS_TOOL = {
    "registrar_gasto": {"monto", "fecha", "categoria", "comercio"},
    "consultar_presupuesto": {"periodo", "categoria"},
    "responder_soporte": {"pregunta"},
}


class MainAgent:
    intent = "principal"

    def __init__(
        self,
        llm: LLMProvider,
        repo: Repository,
        soporte: SoporteRAG,
        max_turns: int = 5,
    ) -> None:
        self._llm = llm
        self._repo = repo
        self._soporte = soporte
        self._max_turns = max_turns

    async def handle(self, context: AgentContext) -> AgentResult:
        messages = self._build_messages(context.historial, context.incoming.texto)
        intencion = Intencion.OTRO
        ultima_tool: str | None = None

        for _ in range(self._max_turns):
            resp = await self._llm.complete(
                messages=messages, tools=TOOLS, system=SYSTEM_PROMPT
            )
            if not resp.tool_calls:
                return AgentResult(
                    respuesta=resp.texto or "¿En qué más te ayudo?",
                    intencion=intencion,
                    tool_llamada=ultima_tool,
                )

            # Realimentar el turno del asistente (bloques text + tool_use).
            asistente: list[dict] = []
            if resp.texto:
                asistente.append({"type": "text", "text": resp.texto})
            for tc in resp.tool_calls:
                asistente.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.nombre, "input": tc.argumentos}
                )
            messages.append({"role": "assistant", "content": asistente})

            # Ejecutar cada tool y realimentar los resultados.
            resultados: list[dict] = []
            for tc in resp.tool_calls:
                ultima_tool = tc.nombre
                intencion = _INTENCION_POR_TOOL.get(tc.nombre, intencion)
                salida = await self._ejecutar(tc.nombre, tc.argumentos, context)
                resultados.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": json.dumps(salida, ensure_ascii=False, default=str),
                    }
                )
            messages.append({"role": "user", "content": resultados})

        # Se agotaron los turnos sin respuesta final (raro): degradar con gracia.
        return AgentResult(
            respuesta="Dame un momento para terminar de procesar tu solicitud. 🙏",
            intencion=intencion,
            tool_llamada=ultima_tool,
        )

    # ------------------------------------------------------------------ internos
    def _build_messages(self, historial: list[Message], texto_actual: str) -> list[dict]:
        """Historial → formato de mensajes de Anthropic. El último mensaje del
        usuario ya está en `historial` (lo guardó el orquestador); si por alguna
        razón no lo está, se añade `texto_actual` como respaldo."""
        messages: list[dict] = []
        for m in historial:
            rol = m.rol if isinstance(m.rol, str) else m.rol.value
            if rol == "user":
                messages.append({"role": "user", "content": m.contenido})
            elif rol == "assistant":
                messages.append({"role": "assistant", "content": m.contenido})
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": texto_actual})
        return messages

    async def _ejecutar(self, nombre: str, argumentos: dict, context: AgentContext) -> dict:
        uid: UUID = context.user.id
        if nombre in _ARGS_TOOL:
            argumentos = {k: v for k, v in argumentos.items() if k in _ARGS_TOOL[nombre]}
        if nombre == "registrar_gasto":
            return registrar_gasto(self._repo, uid, **argumentos)
        if nombre == "consultar_presupuesto":
            return consultar_presupuesto(self._repo, uid, **argumentos)
        if nombre == "responder_soporte":
            return await self._soporte.responder(argumentos.get("pregunta", ""))
        if nombre == "crear_ticket":
            return self._crear_ticket(uid, argumentos)
        return {"error": f"tool desconocida: {nombre}"}

    def _crear_ticket(self, uid: UUID, argumentos: dict) -> dict:
        motivo = argumentos.get("motivo", "otro")
        prioridad = argumentos.get("prioridad", "media")
        ticket = self._repo.create_ticket(
            Ticket(
                user_id=uid,
                motivo=motivo if motivo in _MOTIVOS_VALIDOS else MotivoEscalacion.OTRO,
                prioridad=prioridad if prioridad in _PRIORIDADES_VALIDAS else TicketPrioridad.MEDIA,
                contexto=argumentos.get("contexto", "Escalación pedida por el agente."),
            )
        )
        return {"ticket_id": str(ticket.id), "estado": "abierto"}
