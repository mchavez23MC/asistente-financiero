"""H1 (correcciones) — lógica de `consultar_movimientos`, `editar_transaccion`
y `eliminar_transaccion`, más la detección de duplicados — Fase 10.

Corregir es de lo más frecuente en un agente de finanzas: "no, eran 20 no 32",
"cámbialo a Transporte", "borra el último gasto". Eliminar NO borra la fila:
la marca `anulada` (status ya existente en el contrato), así deja de contar en
`sum_gastos`/`sum_ingresos` (que filtran status='confirmada') pero queda rastro
para el audit trail (§7.4).

El aislamiento por usuario (§7.3.2) vive en `repo.get_transaction(user_id, id)`:
un transaction_id ajeno devuelve None y la tool responde "no_encontrada".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from app.domain.models import Transaction, TransactionStatus
from app.domain.ports import Repository


def _a_decimal(valor) -> Optional[Decimal]:
    if valor is None:
        return None
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def _parse_fecha(valor: Optional[str]) -> Optional[date]:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _parse_id(valor) -> Optional[UUID]:
    try:
        return UUID(str(valor))
    except (TypeError, ValueError):
        return None


def _resumen(t: Transaction) -> dict:
    tipo = t.tipo if isinstance(t.tipo, str) else t.tipo.value
    return {
        "transaction_id": str(t.id),
        "tipo": tipo,
        "monto": float(t.monto) if t.monto is not None else None,
        "fecha": t.fecha.isoformat() if t.fecha else None,
        "categoria": t.categoria,
        "comercio_o_fuente": t.comercio,
    }


# --- consultar historial ------------------------------------------------------
def consultar_movimientos(
    repo: Repository,
    user_id: UUID,
    limite: int = 5,
    tipo: Optional[str] = None,
    categoria: Optional[str] = None,
) -> dict:
    """Últimos movimientos confirmados, más recientes primero. El sistema lista;
    Claude solo los verbaliza (mismo principio grounded que H2)."""
    try:
        limite = max(1, min(int(limite), 20))
    except (TypeError, ValueError):
        limite = 5
    if tipo not in ("gasto", "ingreso"):
        tipo = None  # 'todos' u otro valor → sin filtro
    movimientos = repo.list_transactions(user_id, limite=limite, tipo=tipo, categoria=categoria)
    return {"movimientos": [_resumen(t) for t in movimientos], "cuantos": len(movimientos)}


# --- editar -------------------------------------------------------------------
def editar_transaccion(
    repo: Repository,
    user_id: UUID,
    transaction_id=None,
    monto=None,
    fecha: Optional[str] = None,
    categoria: Optional[str] = None,
    comercio: Optional[str] = None,
    tipo: Optional[str] = None,
) -> dict:
    tid = _parse_id(transaction_id)
    existente = repo.get_transaction(user_id, tid) if tid else None
    if existente is None:
        return {"error": "no_encontrada", "transaction_id": str(transaction_id)}
    status = existente.status if isinstance(existente.status, str) else existente.status.value
    if status == TransactionStatus.ANULADA.value:
        return {"error": "anulada", "transaction_id": str(existente.id)}

    cambios: dict = {}
    monto_dec = _a_decimal(monto)
    if monto_dec is not None:
        cambios["monto"] = monto_dec
    fecha_val = _parse_fecha(fecha)
    if fecha_val is not None:
        cambios["fecha"] = fecha_val
    if categoria:
        cambios["categoria"] = categoria
    if comercio:
        cambios["comercio"] = comercio
    if tipo in ("gasto", "ingreso"):
        cambios["tipo"] = tipo
    if not cambios:
        return {"error": "sin_cambios", "transaction_id": str(existente.id)}

    guardada = repo.save_transaction(existente.model_copy(update=cambios))

    # Total groundeado de la categoría tras la corrección (§1.2).
    total = None
    tipo_final = guardada.tipo if isinstance(guardada.tipo, str) else guardada.tipo.value
    if guardada.categoria:
        suma = repo.sum_gastos if tipo_final == "gasto" else repo.sum_ingresos
        total = float(suma(user_id, categoria=guardada.categoria, periodo="mensual"))

    salida = _resumen(guardada)
    salida["status"] = guardada.status if isinstance(guardada.status, str) else guardada.status.value
    salida["total_categoria_periodo"] = total
    return salida


# --- eliminar (anular) ----------------------------------------------------------
def eliminar_transaccion(repo: Repository, user_id: UUID, transaction_id=None) -> dict:
    tid = _parse_id(transaction_id)
    existente = repo.get_transaction(user_id, tid) if tid else None
    if existente is None:
        return {"error": "no_encontrada", "transaction_id": str(transaction_id)}
    status = existente.status if isinstance(existente.status, str) else existente.status.value
    if status == TransactionStatus.ANULADA.value:
        return {"error": "ya_anulada", "transaction_id": str(existente.id)}

    guardada = repo.save_transaction(
        existente.model_copy(update={"status": TransactionStatus.ANULADA})
    )
    return {
        "transaction_id": str(guardada.id),
        "status": "anulada",
        "monto": float(guardada.monto) if guardada.monto is not None else None,
        "categoria": guardada.categoria,
    }


# --- detección de duplicados -----------------------------------------------------
def buscar_duplicado(
    repo: Repository,
    user_id: UUID,
    tipo: str,
    monto: Decimal,
    fecha: date,
) -> Optional[Transaction]:
    """Posible doble registro: mismo tipo, mismo monto y misma fecha entre los
    movimientos recientes. Caso típico con media: el usuario anota 'gasté 32 en
    el súper' por texto y después manda la foto del mismo recibo. La tool NO
    registra en ese caso: devuelve el candidato para que Luca pregunte."""
    recientes = repo.list_transactions(user_id, limite=10, tipo=tipo)
    for t in recientes:
        if t.monto is not None and t.monto == monto and t.fecha == fecha:
            return t
    return None
