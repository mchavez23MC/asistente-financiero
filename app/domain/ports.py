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

from datetime import datetime
from decimal import Decimal
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from app.domain.models import (
    AgentContext,
    AgentResult,
    AuthCode,
    Budget,
    Category,
    ConversationSummary,
    GuardrailResult,
    IncomingMessage,
    LLMResponse,
    Message,
    Recuerdo,
    RecurringIncome,
    Session,
    Ticket,
    Transaction,
    User,
    UserFact,
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
class EmbeddingProvider(Protocol):
    """Convierte texto en vectores para la memoria semántica (Parte A del plan).

    Implementación hoy: `voyage` (Voyage AI, socio de embeddings de Anthropic).
    Es opcional en el composition root: si no está configurado, la memoria
    semántica queda apagada y el asistente usa solo la ventana reciente.
    """

    #: Dimensión de los vectores que emite (debe coincidir con el schema SQL).
    dimensiones: int

    async def embed(
        self, textos: list[str], tipo: str = "document"
    ) -> list[list[float]]:
        """Vectoriza un lote de textos. `tipo` distingue 'query' (la consulta a
        buscar) de 'document' (lo que se indexa) — algunos modelos optimizan
        distinto cada rol. Devuelve un vector por texto, en el mismo orden."""
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

    # --- memoria semántica (Parte A del plan) --------------------------------
    def save_message_embedding(
        self, message_id: UUID, user_id: UUID, embedding: list[float]
    ) -> None:
        """Guarda (o reemplaza) el vector de un mensaje ya persistido."""
        ...

    def match_messages(
        self,
        user_id: UUID,
        query_embedding: list[float],
        match_count: int = 5,
        umbral: float = 0.75,
    ) -> list[Recuerdo]:
        """Top-k mensajes del usuario por similitud coseno (RPC match_messages).
        SIEMPRE filtrado por user_id (§7.3.2)."""
        ...

    def match_summaries(
        self,
        user_id: UUID,
        query_embedding: list[float],
        match_count: int = 3,
        umbral: float = 0.72,
    ) -> list[Recuerdo]:
        """Top-k resúmenes de conversación del usuario por similitud."""
        ...

    def usuarios_activos_desde(self, desde: datetime) -> list[UUID]:
        """User ids con al menos un mensaje desde `desde` (para los jobs de
        extracción de hechos y de resumen del scheduler)."""
        ...

    # --- hechos del usuario (memoria de largo plazo) -------------------------
    def get_user_facts(self, user_id: UUID, limite: int = 10) -> list[UserFact]:
        """Hechos estables del usuario, más recientes primero."""
        ...

    def match_user_facts(
        self, user_id: UUID, query_embedding: list[float], match_count: int = 3
    ) -> list[tuple[UUID, str, float]]:
        """Hechos más parecidos a un candidato (id, contenido, similitud), para
        deduplicar en la extracción."""
        ...

    def upsert_user_fact(
        self, fact: UserFact, embedding: Optional[list[float]] = None
    ) -> UserFact:
        """Inserta un hecho nuevo, o actualiza `fact.id` si ya venía resuelto
        (dedup: el llamador decide si es actualización)."""
        ...

    # --- resúmenes de conversación (memoria episódica) -----------------------
    def save_conversation_summary(
        self, summary: ConversationSummary, embedding: Optional[list[float]] = None
    ) -> ConversationSummary:
        ...

    def get_ultimo_resumen_ts(self, user_id: UUID) -> Optional[datetime]:
        """`hasta_ts` del resumen más reciente del usuario, o None si no hay.
        Marca hasta dónde ya se resumió (idempotencia del job de resumen)."""
        ...

    # --- transacciones (H1: gastos e ingresos) ---
    def save_transaction(self, transaction: Transaction) -> Transaction:
        ...

    def get_pending_transaction(
        self, user_id: UUID, tipo: Optional[str] = None
    ) -> Optional[Transaction]:
        """Transacción a medias del usuario. Con `tipo` ('gasto'|'ingreso') filtra
        por tipo (el loop de confirmación de gastos no completa un ingreso, ni al
        revés); sin `tipo`, devuelve cualquiera pendiente (contexto del agente)."""
        ...

    def list_transactions(
        self,
        user_id: UUID,
        limite: int = 5,
        tipo: Optional[str] = None,
        categoria: Optional[str] = None,
        solo_confirmadas: bool = True,
    ) -> list[Transaction]:
        """Últimos movimientos del usuario, más recientes primero (por fecha de
        registro). Con `solo_confirmadas=True` (default) excluye anuladas y
        pendientes: alimenta consultar_movimientos, la detección de duplicados y
        las correcciones ('borra el último gasto'). Con `solo_confirmadas=False`
        devuelve todos los estados (vista web, que muestra el status de cada uno).
        `tipo`/`categoria` filtran opcionalmente."""
        ...

    def get_transaction(self, user_id: UUID, transaction_id: UUID) -> Optional[Transaction]:
        """Una transacción por id, SIEMPRE filtrada por user_id (§7.3.2): un id
        de otro usuario devuelve None, nunca la fila ajena."""
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

    def save_budget(self, budget: Budget) -> Budget:
        """Crea o actualiza el presupuesto de (user, categoría, periodo)."""
        ...

    def sum_gastos(
        self,
        user_id: UUID,
        categoria: Optional[str] = None,
        periodo: Optional[str] = None,
    ) -> Decimal:
        """Suma agregada de GASTOS confirmados (tipo='gasto'). El sistema calcula
        el número (§1.2). Debe excluir ingresos, o inflaría el presupuesto."""
        ...

    def sum_ingresos(
        self,
        user_id: UUID,
        categoria: Optional[str] = None,
        periodo: Optional[str] = None,
    ) -> Decimal:
        """Suma agregada de INGRESOS confirmados (tipo='ingreso') del periodo."""
        ...

    # --- tickets (escalación) ---
    def create_ticket(self, ticket: Ticket) -> Ticket:
        ...

    def latest_ticket_at(self, user_id: UUID) -> Optional[datetime]:
        """Fecha de creación del ticket más reciente del usuario, o None si no
        tiene ninguno. Alimenta el límite de 1 ticket cada 5 horas (§escalación)."""
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

    # --- ingresos recurrentes (sueldo base mensual) ---
    def save_recurring_income(self, recurring: RecurringIncome) -> RecurringIncome:
        """Crea o actualiza (si trae id) un ingreso recurrente."""
        ...

    def get_recurring_incomes(
        self, user_id: UUID, solo_activos: bool = True
    ) -> list[RecurringIncome]:
        ...

    def get_all_recurring_incomes(self) -> list[RecurringIncome]:
        """Todas las recurrencias activas de todos los usuarios (cron de recordatorios)."""
        ...

    def recordatorio_ya_enviado(self, recurring_id: UUID, periodo_clave: str) -> bool:
        """Idempotencia: ¿ya se recordó esta recurrencia en este periodo?"""
        ...

    def marcar_recordatorio(self, recurring_id: UUID, periodo_clave: str) -> None:
        ...

    # --- autenticación de la webapp (OTP por WhatsApp + sesiones) -------------
    def save_auth_code(self, code: AuthCode) -> AuthCode:
        ...

    def get_auth_code_activo(self, telefono: str) -> Optional[AuthCode]:
        """Último código no usado del teléfono (para cooldown e intentos)."""
        ...

    def incrementar_intentos_codigo(self, code_id: UUID) -> int:
        """Suma 1 a los intentos fallidos; devuelve el total."""
        ...

    def marcar_codigo_usado(self, code_id: UUID) -> None:
        ...

    def create_session(self, session: Session) -> Session:
        ...

    def get_session(self, token_hash: str) -> Optional[Session]:
        ...

    def delete_session(self, token_hash: str) -> None:
        ...
