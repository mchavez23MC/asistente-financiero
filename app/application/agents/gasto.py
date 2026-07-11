"""H1 — lógica de la tool `registrar_gasto` (§3.1) — Fase 4.

El sistema (no el modelo) decide `status`: si falta el monto, la transacción
queda `pendiente_confirmacion` y se devuelven los `faltantes` para que Claude
pida el dato. Si ya había una transacción pendiente, esta llamada la completa
(merge de campos no nulos) — el modelo ya decidió que este mensaje la completa
en vez de abrir otra intención (caso frágil de §3.1).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from app.domain.models import Transaction, TransactionStatus
from app.domain.ports import Repository

# Campos obligatorios para confirmar un gasto (§3.1: sin monto → pendiente).
OBLIGATORIOS = ("monto",)


def _a_decimal(valor) -> Optional[Decimal]:
    if valor is None:
        return None
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def registrar_gasto(
    repo: Repository,
    user_id: UUID,
    monto=None,
    fecha: Optional[str] = None,
    categoria: Optional[str] = None,
    comercio: Optional[str] = None,
) -> dict:
    pendiente = repo.get_pending_transaction(user_id)

    # Merge sobre la pendiente: los campos nuevos (no nulos) pisan a los viejos.
    monto_dec = _a_decimal(monto)
    if pendiente is not None:
        monto_dec = monto_dec if monto_dec is not None else pendiente.monto
        fecha = fecha or (pendiente.fecha.isoformat() if pendiente.fecha else None)
        categoria = categoria or pendiente.categoria
        comercio = comercio or pendiente.comercio

    faltantes = [c for c in OBLIGATORIOS if c == "monto" and monto_dec is None]
    confirmada = not faltantes
    fecha_val = _parse_fecha(fecha) or (date.today() if confirmada else None)

    tx = Transaction(
        id=pendiente.id if pendiente else None,
        user_id=user_id,
        monto=monto_dec,
        fecha=fecha_val,
        categoria=categoria,
        comercio=comercio,
        status=(
            TransactionStatus.CONFIRMADA
            if confirmada
            else TransactionStatus.PENDIENTE_CONFIRMACION
        ),
    )
    guardada = repo.save_transaction(tx)
    return {
        "transaction_id": str(guardada.id),
        "status": guardada.status,
        "faltantes": faltantes,
    }


def _parse_fecha(valor: Optional[str]) -> Optional[date]:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None
