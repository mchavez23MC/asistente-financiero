"""Repository sobre Supabase Postgres — Fase 2 (§7.1).

INVARIANTE DE SEGURIDAD (§7.3.2): TODA consulta filtra por `user_id`, resuelto
internamente desde el teléfono del webhook. Ningún dato de otro usuario puede
salir de aquí aunque el LLM lo pida.

Sync a propósito (contrato de ports.py): supabase-py v2 es síncrono y el
orquestador corre estas llamadas cortas dentro del background task.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from supabase import Client, create_client

from app.domain.models import Budget, Message, Ticket, Transaction, User


def _dump(model) -> dict:
    """Modelo Pydantic → dict JSON-safe para PostgREST, sin id nulo."""
    data = model.model_dump(mode="json")
    if data.get("id") is None:
        data.pop("id", None)
    return data


class SupabaseRepository:
    def __init__(self, url: str, key: str) -> None:
        self._db: Client = create_client(url, key)

    # --- usuarios -----------------------------------------------------------
    def get_or_create_user(self, telefono: str, nombre: Optional[str] = None) -> User:
        res = self._db.table("users").select("*").eq("telefono", telefono).execute()
        if res.data:
            return User(**res.data[0])
        nuevo = User(telefono=telefono, nombre=nombre)
        res = self._db.table("users").insert(_dump(nuevo)).execute()
        return User(**res.data[0])

    def registrar_consentimiento(self, user_id: UUID) -> User:
        res = (
            self._db.table("users")
            .update({"consentimiento_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", str(user_id))
            .execute()
        )
        return User(**res.data[0])

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
        return Transaction(**res.data[0])

    def get_pending_transaction(self, user_id: UUID) -> Optional[Transaction]:
        res = (
            self._db.table("transactions")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("status", "pendiente_confirmacion")
            .limit(1)
            .execute()
        )
        return Transaction(**res.data[0]) if res.data else None

    # --- presupuesto (H2) — el sistema calcula; Claude explica (§1.2) ---------
    def get_budgets(self, user_id: UUID) -> list[Budget]:
        res = self._db.table("budgets").select("*").eq("user_id", str(user_id)).execute()
        return [Budget(**row) for row in res.data]

    def sum_gastos(
        self,
        user_id: UUID,
        categoria: Optional[str] = None,
        periodo: Optional[str] = None,
    ) -> Decimal:
        q = (
            self._db.table("transactions")
            .select("monto")
            .eq("user_id", str(user_id))
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


def _inicio_periodo(periodo: str):
    """Primer día del periodo corriente ('semanal' | 'mensual' | 'anual')."""
    from datetime import date, timedelta

    hoy = date.today()
    if periodo == "semanal":
        return hoy - timedelta(days=hoy.weekday())
    if periodo == "anual":
        return hoy.replace(month=1, day=1)
    return hoy.replace(day=1)  # mensual (default)
