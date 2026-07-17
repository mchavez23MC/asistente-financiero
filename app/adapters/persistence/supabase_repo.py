"""Repository sobre Supabase Postgres — Fase 2 (§7.1).

INVARIANTE DE SEGURIDAD (§7.3.2): TODA consulta filtra por `user_id`, resuelto
internamente desde el teléfono del webhook. Ningún dato de otro usuario puede
salir de aquí aunque el LLM lo pida.

Sync a propósito (contrato de ports.py): supabase-py v2 es síncrono y el
orquestador corre estas llamadas cortas dentro del background task.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from supabase import Client, create_client

from app.domain.models import (
    AuthCode,
    Budget,
    Category,
    ConversationSummary,
    Document,
    Message,
    Recuerdo,
    RecurringIncome,
    Session,
    Ticket,
    Transaction,
    User,
    UserFact,
)


def _dump(model) -> dict:
    """Modelo Pydantic → dict JSON-safe para PostgREST, sin id nulo."""
    data = model.model_dump(mode="json")
    if data.get("id") is None:
        data.pop("id", None)
    return data


class SupabaseRepository:
    #: Cache de usuario por teléfono (§plan-latencia B1). El mismo usuario envía
    #: varios mensajes seguidos; get_or_create_user cuesta ~0.5s por round-trip.
    #: El único campo que muta en sesión (consentimiento_at) se actualiza abajo,
    #: así que un TTL corto no arriesga re-mostrar el aviso legal.
    _USER_CACHE_TTL_S = 300.0

    def __init__(self, url: str, key: str) -> None:
        self._db: Client = create_client(url, key)
        self._user_cache: dict[str, tuple[float, User]] = {}

    # --- usuarios -----------------------------------------------------------
    def get_or_create_user(self, telefono: str, nombre: Optional[str] = None) -> User:
        cacheado = self._user_cache.get(telefono)
        if cacheado and time.monotonic() - cacheado[0] < self._USER_CACHE_TTL_S:
            return cacheado[1]

        res = self._db.table("users").select("*").eq("telefono", telefono).execute()
        if res.data:
            user = User(**res.data[0])
        else:
            nuevo = User(telefono=telefono, nombre=nombre)
            res = self._db.table("users").insert(_dump(nuevo)).execute()
            user = User(**res.data[0])
        self._user_cache[telefono] = (time.monotonic(), user)
        return user

    def registrar_consentimiento(self, user_id: UUID) -> User:
        res = (
            self._db.table("users")
            .update({"consentimiento_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", str(user_id))
            .execute()
        )
        user = User(**res.data[0])
        # Refresca el cache: el próximo mensaje ya NO debe ver tiene_consentimiento=False.
        self._user_cache[user.telefono] = (time.monotonic(), user)
        return user

    # --- mensajes / audit trail ----------------------------------------------
    def save_message(self, message: Message) -> Message:
        res = self._db.table("messages").insert(_dump(message)).execute()
        return Message(**res.data[0])

    def get_last_n_messages(self, user_id: UUID, n: int = 10) -> list[Message]:
        res = (
            self._db.table("messages")
            .select("*")
            .eq("user_id", str(user_id))
            .order("timestamp", desc=True)
            .limit(n)
            .execute()
        )
        # Se devuelven en orden cronológico, listos para el prompt (§3.1).
        return [Message(**row) for row in reversed(res.data)]

    def recent_messages(self, n: int = 100) -> list[Message]:
        res = (
            self._db.table("messages")
            .select("*")
            .order("timestamp", desc=True)
            .limit(n)
            .execute()
        )
        return [Message(**row) for row in res.data]

    # --- memoria semántica (Parte A del plan) ---------------------------------
    def save_message_embedding(
        self, message_id: UUID, user_id: UUID, embedding: list[float]
    ) -> None:
        # upsert por PK (message_id): re-embeber un mensaje reemplaza su vector.
        self._db.table("message_embeddings").upsert(
            {
                "message_id": str(message_id),
                "user_id": str(user_id),
                "embedding": embedding,
            },
            on_conflict="message_id",
        ).execute()

    def match_messages(
        self,
        user_id: UUID,
        query_embedding: list[float],
        match_count: int = 5,
        umbral: float = 0.75,
    ) -> list[Recuerdo]:
        res = self._db.rpc(
            "match_messages",
            {
                "p_user_id": str(user_id),
                "query_embedding": query_embedding,
                "match_count": match_count,
                "umbral": umbral,
            },
        ).execute()
        return [
            Recuerdo(
                contenido=row["contenido"],
                origen="mensaje",
                rol=row.get("rol"),
                timestamp=row.get("ts"),
                similitud=row.get("similitud", 0.0),
            )
            for row in (res.data or [])
        ]

    def match_summaries(
        self,
        user_id: UUID,
        query_embedding: list[float],
        match_count: int = 3,
        umbral: float = 0.72,
    ) -> list[Recuerdo]:
        res = self._db.rpc(
            "match_summaries",
            {
                "p_user_id": str(user_id),
                "query_embedding": query_embedding,
                "match_count": match_count,
                "umbral": umbral,
            },
        ).execute()
        return [
            Recuerdo(
                contenido=row["resumen"],
                origen="resumen",
                timestamp=row.get("hasta_ts"),
                similitud=row.get("similitud", 0.0),
            )
            for row in (res.data or [])
        ]

    def usuarios_activos_desde(self, desde: datetime) -> list[UUID]:
        res = (
            self._db.table("messages")
            .select("user_id")
            .gte("timestamp", desde.isoformat())
            .execute()
        )
        # Distinct en la app: PostgREST no expone DISTINCT sin una vista dedicada,
        # y el volumen de una ventana corta es pequeño.
        return list({UUID(row["user_id"]) for row in (res.data or [])})

    # --- hechos del usuario (memoria de largo plazo) --------------------------
    def get_user_facts(self, user_id: UUID, limite: int = 10) -> list[UserFact]:
        res = (
            self._db.table("user_facts")
            .select("id,user_id,tipo,contenido,fuente_message_id,updated_at,created_at")
            .eq("user_id", str(user_id))
            .order("updated_at", desc=True)
            .limit(limite)
            .execute()
        )
        return [UserFact(**row) for row in (res.data or [])]

    def match_user_facts(
        self, user_id: UUID, query_embedding: list[float], match_count: int = 3
    ) -> list[tuple[UUID, str, float]]:
        res = self._db.rpc(
            "match_user_facts",
            {
                "p_user_id": str(user_id),
                "query_embedding": query_embedding,
                "match_count": match_count,
            },
        ).execute()
        return [
            (UUID(row["fact_id"]), row["contenido"], row.get("similitud", 0.0))
            for row in (res.data or [])
        ]

    def upsert_user_fact(
        self, fact: UserFact, embedding: Optional[list[float]] = None
    ) -> UserFact:
        data = _dump(fact)
        if embedding is not None:
            data["embedding"] = embedding
        if fact.id is not None:
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            res = (
                self._db.table("user_facts").update(data).eq("id", str(fact.id)).execute()
            )
        else:
            res = self._db.table("user_facts").insert(data).execute()
        # El select del retorno omite `embedding` (no lo mapea el modelo).
        fila = {k: v for k, v in res.data[0].items() if k != "embedding"}
        return UserFact(**fila)

    # --- resúmenes de conversación (memoria episódica) ------------------------
    def save_conversation_summary(
        self, summary: ConversationSummary, embedding: Optional[list[float]] = None
    ) -> ConversationSummary:
        data = _dump(summary)
        if embedding is not None:
            data["embedding"] = embedding
        res = self._db.table("conversation_summaries").insert(data).execute()
        fila = {k: v for k, v in res.data[0].items() if k != "embedding"}
        return ConversationSummary(**fila)

    def get_ultimo_resumen_ts(self, user_id: UUID) -> Optional[datetime]:
        res = (
            self._db.table("conversation_summaries")
            .select("hasta_ts")
            .eq("user_id", str(user_id))
            .order("hasta_ts", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data or res.data[0].get("hasta_ts") is None:
            return None
        return datetime.fromisoformat(res.data[0]["hasta_ts"])

    # --- transacciones (H1) ---------------------------------------------------
    def save_transaction(self, transaction: Transaction) -> Transaction:
        data = _dump(transaction)
        if transaction.id is not None:
            res = (
                self._db.table("transactions")
                .update(data)
                .eq("id", str(transaction.id))
                .execute()
            )
        else:
            res = self._db.table("transactions").insert(data).execute()
        guardada = Transaction(**res.data[0])
        # Cataloga la categoría usada (§3.1: tabla categories de T2).
        if guardada.categoria:
            self.ensure_category(guardada.categoria)
        return guardada

    # --- categorías (catálogo de T2) ------------------------------------------
    def ensure_category(self, nombre: str) -> None:
        nombre = (nombre or "").strip()
        if not nombre:
            return
        # Idempotente: `nombre` es UNIQUE; ignora si ya existe.
        self._db.table("categories").upsert(
            {"nombre": nombre}, on_conflict="nombre", ignore_duplicates=True
        ).execute()

    def get_categories(self) -> list[Category]:
        res = self._db.table("categories").select("*").order("nombre").execute()
        return [Category(**row) for row in res.data]

    def get_pending_transaction(
        self, user_id: UUID, tipo: Optional[str] = None
    ) -> Optional[Transaction]:
        q = (
            self._db.table("transactions")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("status", "pendiente_confirmacion")
        )
        if tipo:
            q = q.eq("tipo", tipo)
        res = q.limit(1).execute()
        return Transaction(**res.data[0]) if res.data else None

    def list_transactions(
        self,
        user_id: UUID,
        limite: int = 5,
        tipo: Optional[str] = None,
        categoria: Optional[str] = None,
        solo_confirmadas: bool = True,
    ) -> list[Transaction]:
        q = self._db.table("transactions").select("*").eq("user_id", str(user_id))
        if solo_confirmadas:
            q = q.eq("status", "confirmada")
        if tipo:
            q = q.eq("tipo", tipo)
        if categoria:
            q = q.eq("categoria", categoria)
        res = q.order("created_at", desc=True).limit(limite).execute()
        return [Transaction(**row) for row in res.data]

    def get_transaction(self, user_id: UUID, transaction_id: UUID) -> Optional[Transaction]:
        # El .eq(user_id) es el aislamiento (§7.3.2): un id ajeno devuelve None.
        res = (
            self._db.table("transactions")
            .select("*")
            .eq("id", str(transaction_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        return Transaction(**res.data[0]) if res.data else None

    # --- presupuesto (H2) — el sistema calcula; Claude explica (§1.2) ---------
    def get_budgets(self, user_id: UUID) -> list[Budget]:
        res = self._db.table("budgets").select("*").eq("user_id", str(user_id)).execute()
        return [Budget(**row) for row in res.data]

    def save_budget(self, budget: Budget) -> Budget:
        # Upsert sobre la unicidad (user_id, categoria, periodo) del schema.
        res = (
            self._db.table("budgets")
            .upsert(_dump(budget), on_conflict="user_id,categoria,periodo")
            .execute()
        )
        return Budget(**res.data[0])

    def sum_gastos(
        self,
        user_id: UUID,
        categoria: Optional[str] = None,
        periodo: Optional[str] = None,
    ) -> Decimal:
        # tipo='gasto' es CRÍTICO: sin él, un ingreso confirmado (sueldo) contaría
        # como gasto e inflaría el presupuesto / dispararía alertas falsas.
        return self._sum_por_tipo("gasto", user_id, categoria, periodo)

    def sum_ingresos(
        self,
        user_id: UUID,
        categoria: Optional[str] = None,
        periodo: Optional[str] = None,
    ) -> Decimal:
        return self._sum_por_tipo("ingreso", user_id, categoria, periodo)

    def _sum_por_tipo(
        self,
        tipo: str,
        user_id: UUID,
        categoria: Optional[str],
        periodo: Optional[str],
    ) -> Decimal:
        q = (
            self._db.table("transactions")
            .select("monto")
            .eq("user_id", str(user_id))
            .eq("tipo", tipo)
            .eq("status", "confirmada")
        )
        if categoria:
            q = q.eq("categoria", categoria)
        if periodo:
            q = q.gte("fecha", _inicio_periodo(periodo).isoformat())
        res = q.execute()
        return sum((Decimal(str(r["monto"])) for r in res.data if r["monto"]), Decimal("0"))

    # --- tickets ---------------------------------------------------------------
    def create_ticket(self, ticket: Ticket) -> Ticket:
        res = self._db.table("tickets").insert(_dump(ticket)).execute()
        return Ticket(**res.data[0])

    # --- panel humano (fase 6) --------------------------------------------------
    def get_user(self, user_id: UUID) -> Optional[User]:
        res = self._db.table("users").select("*").eq("id", str(user_id)).limit(1).execute()
        return User(**res.data[0]) if res.data else None

    def list_tickets(self, estado: Optional[str] = None) -> list[Ticket]:
        q = self._db.table("tickets").select("*")
        if estado:
            q = q.eq("estado", estado)
        res = q.order("created_at", desc=True).execute()
        return [Ticket(**row) for row in res.data]

    def get_ticket(self, ticket_id: UUID) -> Optional[Ticket]:
        res = self._db.table("tickets").select("*").eq("id", str(ticket_id)).limit(1).execute()
        return Ticket(**res.data[0]) if res.data else None

    def update_ticket_estado(self, ticket_id: UUID, estado: str) -> Ticket:
        cambios: dict = {"estado": estado, "updated_at": datetime.now(timezone.utc).isoformat()}
        if estado in ("resuelto", "cerrado"):
            cambios["resuelto_at"] = datetime.now(timezone.utc).isoformat()
        res = (
            self._db.table("tickets").update(cambios).eq("id", str(ticket_id)).execute()
        )
        return Ticket(**res.data[0])

    # --- scheduler proactivo (fase 7) -------------------------------------------
    def get_all_budgets(self) -> list[Budget]:
        res = self._db.table("budgets").select("*").execute()
        return [Budget(**row) for row in res.data]

    def alerta_ya_enviada(self, budget_id: UUID, periodo_clave: str) -> bool:
        res = (
            self._db.table("alerts")
            .select("id")
            .eq("budget_id", str(budget_id))
            .eq("periodo_clave", periodo_clave)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def marcar_alerta(self, user_id: UUID, budget_id: UUID, periodo_clave: str) -> None:
        self._db.table("alerts").insert(
            {
                "user_id": str(user_id),
                "budget_id": str(budget_id),
                "periodo_clave": periodo_clave,
            }
        ).execute()

    # --- ingresos recurrentes (sueldo base mensual) -----------------------------
    def save_recurring_income(self, recurring: RecurringIncome) -> RecurringIncome:
        data = _dump(recurring)
        if recurring.id is not None:
            res = (
                self._db.table("recurring_incomes")
                .update(data)
                .eq("id", str(recurring.id))
                .execute()
            )
        else:
            res = self._db.table("recurring_incomes").insert(data).execute()
        return RecurringIncome(**res.data[0])

    def get_recurring_incomes(
        self, user_id: UUID, solo_activos: bool = True
    ) -> list[RecurringIncome]:
        q = self._db.table("recurring_incomes").select("*").eq("user_id", str(user_id))
        if solo_activos:
            q = q.eq("activo", True)
        res = q.order("created_at").execute()
        return [RecurringIncome(**row) for row in res.data]

    def get_all_recurring_incomes(self) -> list[RecurringIncome]:
        res = self._db.table("recurring_incomes").select("*").eq("activo", True).execute()
        return [RecurringIncome(**row) for row in res.data]

    def recordatorio_ya_enviado(self, recurring_id: UUID, periodo_clave: str) -> bool:
        res = (
            self._db.table("income_reminders")
            .select("id")
            .eq("recurring_id", str(recurring_id))
            .eq("periodo_clave", periodo_clave)
            .limit(1)
            .execute()
        )
        return bool(res.data)

    def marcar_recordatorio(self, recurring_id: UUID, periodo_clave: str) -> None:
        self._db.table("income_reminders").insert(
            {"recurring_id": str(recurring_id), "periodo_clave": periodo_clave}
        ).execute()
    # --- autenticación de la webapp (OTP + sesiones) ---------------------------
    def save_auth_code(self, code: AuthCode) -> AuthCode:
        res = self._db.table("auth_codes").insert(_dump(code)).execute()
        return AuthCode(**res.data[0])

    def get_auth_code_activo(self, telefono: str) -> Optional[AuthCode]:
        res = (
            self._db.table("auth_codes")
            .select("*")
            .eq("telefono", telefono)
            .eq("usado", False)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return AuthCode(**res.data[0]) if res.data else None

    def incrementar_intentos_codigo(self, code_id: UUID) -> int:
        actual = (
            self._db.table("auth_codes").select("intentos").eq("id", str(code_id)).execute()
        )
        intentos = (actual.data[0]["intentos"] if actual.data else 0) + 1
        self._db.table("auth_codes").update({"intentos": intentos}).eq(
            "id", str(code_id)
        ).execute()
        return intentos

    def marcar_codigo_usado(self, code_id: UUID) -> None:
        self._db.table("auth_codes").update({"usado": True}).eq("id", str(code_id)).execute()

    def create_session(self, session: Session) -> Session:
        res = self._db.table("sessions").insert(session.model_dump(mode="json")).execute()
        return Session(**res.data[0])

    def get_session(self, token_hash: str) -> Optional[Session]:
        res = (
            self._db.table("sessions").select("*").eq("token_hash", token_hash).limit(1).execute()
        )
        return Session(**res.data[0]) if res.data else None

    def delete_session(self, token_hash: str) -> None:
        self._db.table("sessions").delete().eq("token_hash", token_hash).execute()

    # --- documentos financieros (plan de documentos, E1) ----------------------
    def save_document(self, document: Document) -> Document:
        res = self._db.table("documents").insert(_dump(document)).execute()
        return Document(**res.data[0])

    def get_document(self, user_id: UUID, document_id: UUID) -> Optional[Document]:
        res = (
            self._db.table("documents")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("id", str(document_id))
            .limit(1)
            .execute()
        )
        return Document(**res.data[0]) if res.data else None

    def find_document_by_sha(self, user_id: UUID, sha256: str) -> Optional[Document]:
        res = (
            self._db.table("documents")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("sha256", sha256)
            .limit(1)
            .execute()
        )
        return Document(**res.data[0]) if res.data else None

    def find_document_esperando(self, user_id: UUID) -> Optional[Document]:
        res = (
            self._db.table("documents")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("status", "esperando_clasificacion")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return Document(**res.data[0]) if res.data else None

    def update_document(
        self, user_id: UUID, document_id: UUID, cambios: dict
    ) -> Optional[Document]:
        res = (
            self._db.table("documents")
            .update(cambios)
            .eq("user_id", str(user_id))
            .eq("id", str(document_id))
            .execute()
        )
        return Document(**res.data[0]) if res.data else None

    def count_documents_desde(self, user_id: UUID, desde: datetime) -> int:
        res = (
            self._db.table("documents")
            .select("id", count="exact")
            .eq("user_id", str(user_id))
            .gte("created_at", desde.isoformat())
            .execute()
        )
        return res.count or 0


def _inicio_periodo(periodo: str):
    """Primer día del periodo corriente ('semanal' | 'mensual' | 'anual')."""
    from datetime import date, timedelta

    hoy = date.today()
    if periodo == "semanal":
        return hoy - timedelta(days=hoy.weekday())
    if periodo == "anual":
        return hoy.replace(month=1, day=1)
    return hoy.replace(day=1)  # mensual (default)
