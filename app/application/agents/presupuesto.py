"""H2 — lógica de la tool `consultar_presupuesto` (§1.2) — Fase 4.

H2 es *grounded*: EL SISTEMA CALCULA LOS NÚMEROS, Claude solo los explica. El
modelo nunca suma totales por su cuenta — esta función devuelve las cifras ya
agregadas por el `Repository` y el agente las verbaliza.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.domain.ports import Repository


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


def _num(v: Optional[Decimal]):
    return float(v) if v is not None else None
