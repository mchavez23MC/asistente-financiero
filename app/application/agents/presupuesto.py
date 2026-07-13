"""H2 — lógica de `consultar_presupuesto` y `configurar_presupuesto` (§1.2).

H2 es *grounded*: EL SISTEMA CALCULA LOS NÚMEROS, Claude solo los explica. El
modelo nunca suma totales por su cuenta — esta función devuelve las cifras ya
agregadas por el `Repository` y el agente las verbaliza.

`configurar_presupuesto` (fase 11) da paridad chat ↔ webapp: reutiliza el mismo
`repo.save_budget` (upsert por user/categoría/periodo) que el endpoint
POST /presupuestos de la webapp, con la misma normalización de categoría.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from app.domain.models import Budget, Periodo
from app.domain.ports import Repository

_PERIODOS_VALIDOS = {p.value for p in Periodo}


def consultar_presupuesto(
    repo: Repository,
    user_id: UUID,
    periodo: str = "mensual",
    categoria: Optional[str] = None,
) -> dict:
    gastado = repo.sum_gastos(user_id, categoria=categoria, periodo=periodo)
    ingresos = repo.sum_ingresos(user_id, categoria=categoria, periodo=periodo)

    # Límite: el del presupuesto de esa categoría/periodo, o la suma de límites
    # del periodo si no se filtró por categoría.
    budgets = repo.get_budgets(user_id)
    relevantes = [b for b in budgets if b.periodo == periodo]
    if categoria is not None:
        relevantes = [b for b in relevantes if b.categoria == categoria]

    limite = sum((b.monto_limite for b in relevantes), Decimal("0")) if relevantes else None
    restante = (limite - gastado) if limite is not None else None
    porcentaje = (
        float(round(gastado / limite * 100, 1)) if limite and limite > 0 else None
    )

    return {
        "periodo": periodo,
        "categoria": categoria,
        "limite": _num(limite),
        "gastado": _num(gastado),
        "ingresos": _num(ingresos),
        # Balance del periodo = lo que entró menos lo que salió (§1.2, groundeado).
        "balance": _num(ingresos - gastado),
        "restante": _num(restante),
        "porcentaje": porcentaje,
    }


def configurar_presupuesto(
    repo: Repository,
    user_id: UUID,
    categoria: Optional[str] = None,
    monto_limite=None,
    periodo: str = "mensual",
    umbral_alerta=None,
) -> dict:
    """Crea o actualiza el presupuesto de (usuario, categoría, periodo).

    Upsert: si ya existía un límite para esa categoría/periodo se reemplaza, y
    el retorno incluye `anterior_monto_limite` para que Luca mencione el cambio
    ("subí tu límite de comida de $80 a $100"). Sin categoría o sin monto no se
    guarda nada: se devuelven los `faltantes` para que Luca los pida."""
    categoria = (categoria or "").strip().lower()  # misma normalización que la webapp
    monto_dec = _a_decimal(monto_limite)
    faltantes = [n for n, v in (("categoria", categoria), ("monto_limite", monto_dec)) if not v]
    if faltantes:
        return {"error": "faltan_datos", "faltantes": faltantes}
    if periodo not in _PERIODOS_VALIDOS:
        periodo = "mensual"

    # Presupuesto previo de la misma (categoría, periodo), si lo hay.
    anterior = next(
        (
            b
            for b in repo.get_budgets(user_id)
            if b.categoria == categoria and b.periodo == periodo
        ),
        None,
    )

    umbral = _umbral_valido(umbral_alerta)
    if umbral is None:
        # Sin umbral nuevo: se conserva el configurado, o el default del modelo.
        umbral = anterior.umbral_alerta if anterior is not None else 0.8

    guardado = repo.save_budget(
        Budget(
            id=anterior.id if anterior is not None else None,
            user_id=user_id,
            categoria=categoria,
            monto_limite=monto_dec,
            periodo=periodo,
            umbral_alerta=umbral,
        )
    )
    return {
        "budget_id": str(guardado.id),
        "categoria": guardado.categoria,
        "monto_limite": float(guardado.monto_limite),
        "periodo": guardado.periodo,
        "umbral_alerta": guardado.umbral_alerta,
        "anterior_monto_limite": float(anterior.monto_limite) if anterior is not None else None,
    }


def _a_decimal(valor) -> Optional[Decimal]:
    if valor is None:
        return None
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def _umbral_valido(valor) -> Optional[float]:
    """Umbral de alerta como fracción [0..1]. Acepta '80' como 80% (0.8)."""
    if valor is None:
        return None
    try:
        u = float(valor)
    except (TypeError, ValueError):
        return None
    if 1 < u <= 100:
        u = u / 100.0
    return u if 0 <= u <= 1 else None


def _num(v: Optional[Decimal]):
    return float(v) if v is not None else None
