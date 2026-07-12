"""Puertos del núcleo (interfaces) — Fase 1.

Los 5 puertos de la tabla de §7.1. Son `Protocol` ligeros (structural typing),
no ABCs con herencia obligatoria ni capas de DTOs (evitamos la ceremonia de
Clean "de libro", §7.1). Un adaptador cumple un puerto por tener los métodos,
sin importar este módulo — así la regla de dependencia se respeta sola.

Regla de dependencia (§7.1): `adapters/` e `interfaces/` dependen de `domain/`;
`domain/` no importa nada de ellos.

Convención async/sync (decisión de contrato):
  - I/O de red por request → `async` (LLMProvider, Guardrail, ChannelAdapter).
  - Repository → `sync`: supabase-py v2 es síncrono; el orquestador lo corre en
    threadpool. Mantenerlo sync evita envolver todo en `run_in_executor` ahora.
Si en fase 2 el repo se vuelve async, es una renegociación explícita del contrato.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from app.domain.models import (
    AgentContext,
    AgentResult,
    Budget,
    Category,
    GuardrailResult,
    IncomingMessage,
    LLMResponse,
    Message,
    Ticket,
    Transaction,
    User,
)


@runtime_checkable
class ChannelAdapter(Protocol):
    """Normaliza el mensaje entrante → `IncomingMessage`; envía la respuesta saliente.

    Extensión futura (§7.1): Telegram / Instagram / SMS = nuevo adaptador, cero
    cambios al core. `web_chat` es el plan B real (§9).
    """

    #: Identificador del canal ('whatsapp', 'web', ...). Va en IncomingMessage.canal.
    canal: str

    def parse(self, payload: dict) -> IncomingMessage:
        """Convierte el payload crudo del canal al formato canónico."""
        ...

    async def send(self, user: User, text: str) -> None:
        """Envía `text` al usuario por este canal (Graph API de Meta, etc.)."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """`complete(messages, tools)` sobre un proveedor de modelo (§1.1).

    Implementaciones hoy: `claude` (agentes H1/H2/H3), `groq` (guardrail).
    Extensión: `gemini` para H2 como palanca de costo (§1.2).
    """

    async def complete(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        system: Optional[str] = None,
    ) -> LLMResponse:
        """Una pasada de completado, con tool use opcional."""
        ...


@runtime_checkable
class AgentHandler(Protocol):
    """Atiende una intención. El router despacha al handler registrado (§7.1).

    Extensión: cluster remoto = `RemoteAgentHandler` con la misma firma que
    publica a una cola; el orquestador no nota la diferencia (§7.5).
    """

    #: Nombre de la intención que atiende ('gasto', 'presupuesto', 'soporte'...).
    intent: str

    async def handle(self, context: AgentContext) -> AgentResult:
        ...


@runtime_checkable
class AgentRegistry(Protocol):
    """Registro de handlers por intención (patrón Registry + Strategy, §7.1)."""

    def register(self, handler: AgentHandler) -> None:
        ...

    def get(self, intent: str) -> AgentHandler:
        """Devuelve el handler para la intención, o el default ('eco' en fase 2)."""
        ...


@runtime_checkable
class Guardrail(Protocol):
    """Clasifica sensibilidad ANTES del agente (§1.4 / §7.3).

    El `Repository` NO aparece aquí: el aislamiento de datos por usuario vive en
    el repo, no en el guardrail. Este puerto solo clasifica texto.
    """

    async def classify(self, texto: str) -> GuardrailResult:
        """Denylist → clasificador Groq → umbral. Fail-closed ante error/timeout."""
        ...


@runtime_checkable
class Repository(Protocol):
    """Persistencia de users/messages/transactions/budgets/tickets.

    Aislamiento por usuario (§7.3.2): el `user_id` se resuelve internamente desde
    el teléfono del webhook y filtra TODA consulta. El LLM nunca pasa un user_id
    como parámetro de tool — por eso ningún método de tool lo recibe del modelo.
    """

    # --- usuarios ---
    def get_or_create_user(self, telefono: str, nombre: Optional[str] = None) -> User:
        ...

    def registrar_consentimiento(self, user_id: UUID) -> User:
        ...

    # --- mensajes / audit trail ---
    def save_message(self, message: Message) -> Message:
        ...

    def get_last_n_messages(self, user_id: UUID, n: int = 10) -> list[Message]:
        ...

    def recent_messages(self, n: int = 100) -> list[Message]:
        """Últimos mensajes de todos los usuarios (audit trail del panel, §7.4)."""
        ...

    # --- transacciones (H1) ---
    def save_transaction(self, transaction: Transaction) -> Transaction:
        ...

    def get_pending_transaction(self, user_id: UUID) -> Optional[Transaction]:
        ...

    # --- categorías (catálogo de T2) ---
    def ensure_category(self, nombre: str) -> None:
        """Registra la categoría en el catálogo si no existe (idempotente)."""
        ...

    def get_categories(self) -> list[Category]:
        ...

    # --- presupuesto (H2) — el sistema calcula; Claude explica (§1.2) ---
    def get_budgets(self, user_id: UUID) -> list[Budget]:
        ...

    def sum_gastos(
        self,
        user_id: UUID,
        categoria: Optional[str] = None,
        periodo: Optional[str] = None,
    ) -> Decimal:
        """Suma agregada de gastos confirmados. El sistema calcula el número (§1.2)."""
        ...

    # --- tickets (escalación) ---
    def create_ticket(self, ticket: Ticket) -> Ticket:
        ...

    # --- panel humano (fase 6) ---
    def get_user(self, user_id: UUID) -> Optional[User]:
        ...

    def list_tickets(self, estado: Optional[str] = None) -> list[Ticket]:
        """Cola de tickets, más nuevos primero; opcionalmente filtrada por estado."""
        ...

    def get_ticket(self, ticket_id: UUID) -> Optional[Ticket]:
        ...

    def update_ticket_estado(self, ticket_id: UUID, estado: str) -> Ticket:
        ...

    # --- scheduler proactivo (fase 7) ---
    def get_all_budgets(self) -> list[Budget]:
        """Todos los presupuestos de todos los usuarios (para el cron de alertas)."""
        ...

    def alerta_ya_enviada(self, budget_id: UUID, periodo_clave: str) -> bool:
        """Idempotencia: ¿ya se notificó este cruce de umbral en este periodo?"""
        ...

    def marcar_alerta(self, user_id: UUID, budget_id: UUID, periodo_clave: str) -> None:
        ...
