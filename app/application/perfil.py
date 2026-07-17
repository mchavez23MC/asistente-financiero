"""Perfil financiero verificable (plan de documentos, E5 — el diferenciador).

Convierte el registro de gastos en un activo del usuario: la prueba de que es
sujeto de crédito. Regla de oro que gobierna TODO el módulo: **solo cuenta lo
que tiene evidencia** (transacción confirmada con document_id). Lo declarado por
texto se muestra, pero no puntúa.

Mismo principio grounded que consultar_presupuesto: el SQL/repositorio calcula,
aquí NO hay LLM ni estimaciones. `Decimal` para dinero, promedios sobre meses
CERRADOS (el mes en curso no cuenta: un perfil que cambia a diario no es serio).

Términos PROHIBIDOS (doc 09): nunca "score crediticio", "aprobado", "historial
crediticio oficial". Se usa "índice de verificación".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.domain.ports import Repository

# Ponderación del índice de verificación (v1 — contrato; cambiar = versionar).
_W_REGULARIDAD = 0.40
_W_RESPALDADO = 0.25
_W_ANTIGUEDAD = 0.20
_W_EVIDENCIA = 0.15

# Peso por calidad de evidencia (promedio ponderado por monto).
_PESO_SRI = Decimal("1.0")
_PESO_OTRO_RESPALDO = Decimal("0.7")  # voucher/foto (v1 no distingue más fino)

MESES_MINIMOS_DEFAULT = 2
_VENTANA_MESES = 6


def _clave_mes(f: date) -> tuple[int, int]:
    return (f.year, f.month)


def _meses_cerrados(hoy: date, n: int = _VENTANA_MESES) -> set[tuple[int, int]]:
    """Los últimos `n` meses calendario ANTERIORES al actual (el mes en curso
    se excluye: los promedios se calculan sobre meses cerrados)."""
    meses = set()
    y, m = hoy.year, hoy.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        meses.add((y, m))
    return meses


def calcular_perfil(
    movimientos: list[dict],
    hoy: Optional[date] = None,
    meses_minimos: int = MESES_MINIMOS_DEFAULT,
) -> dict:
    """`movimientos`: dicts con {tipo, monto: Decimal, fecha: date, respaldada:
    bool, sri: bool, comercio: str|None}. Devuelve el perfil (schema v1)."""
    hoy = hoy or date.today()
    cerrados = _meses_cerrados(hoy)

    universo = [m for m in movimientos if m.get("monto") and m.get("fecha")]
    respaldadas = [m for m in universo if m.get("respaldada")]

    meses_con_tx = {_clave_mes(m["fecha"]) for m in universo}
    meses_activos = len(meses_con_tx)

    # --- ingresos (solo respaldados puntúan) ---
    ing_resp_6m = [m for m in respaldadas if m["tipo"] == "ingreso" and _clave_mes(m["fecha"]) in cerrados]
    meses_con_ingreso = {_clave_mes(m["fecha"]) for m in ing_resp_6m}
    total_ing = sum((m["monto"] for m in ing_resp_6m), Decimal("0"))
    prom_ingreso = (total_ing / len(meses_con_ingreso)) if meses_con_ingreso else Decimal("0")
    regularidad = (len(meses_con_ingreso) / min(_VENTANA_MESES, meses_activos)) if meses_activos else 0.0

    fuentes = _fuentes_ingreso(ing_resp_6m)
    tendencia = _tendencia(ing_resp_6m, cerrados)

    # --- gastos (universo completo se muestra; respaldado_pct mide evidencia) ---
    gas_6m = [m for m in universo if m["tipo"] == "gasto" and _clave_mes(m["fecha"]) in cerrados]
    gas_resp_6m = [m for m in gas_6m if m.get("respaldada")]
    meses_gasto = {_clave_mes(m["fecha"]) for m in gas_6m}
    total_gas = sum((m["monto"] for m in gas_6m), Decimal("0"))
    total_gas_resp = sum((m["monto"] for m in gas_resp_6m), Decimal("0"))
    prom_gasto = (total_gas / len(meses_gasto)) if meses_gasto else Decimal("0")
    respaldado_pct = float(total_gas_resp / total_gas) if total_gas else 0.0
    categorias_top = _categorias_top(gas_6m)

    # --- capacidad ---
    total_ing_resp = total_ing
    n_meses_flujo = max(len(meses_con_ingreso | {_clave_mes(m["fecha"]) for m in gas_resp_6m}), 1)
    flujo_neto = (total_ing_resp - total_gas_resp) / n_meses_flujo
    ratio = float(total_gas_resp / total_ing_resp) if total_ing_resp else None

    # --- confiabilidad + índice ---
    con_sri = sum(1 for m in respaldadas if m.get("sri"))
    respaldado_global = _respaldado_pct_global(universo, cerrados)
    peso_ev = _peso_evidencia(respaldadas)
    indice = (
        _W_REGULARIDAD * regularidad
        + _W_RESPALDADO * respaldado_global
        + _W_ANTIGUEDAD * min(meses_activos / 6, 1.0)
        + _W_EVIDENCIA * float(peso_ev)
    )
    estado = "construyendo" if meses_activos < meses_minimos else "activo"

    return {
        "version": "v1",
        "estado": estado,
        "usuario": {"meses_activo": meses_activos, "generado": hoy.isoformat()},
        "ingresos": {
            "promedio_mensual": round(float(prom_ingreso), 2),
            "regularidad": round(regularidad, 2),
            "fuentes": fuentes,
            "tendencia_6m": tendencia,
        },
        "gastos": {
            "promedio_mensual": round(float(prom_gasto), 2),
            "respaldado_pct": round(respaldado_pct, 2),
            "categorias_top": categorias_top,
        },
        "capacidad": {
            "flujo_neto_mensual": round(float(flujo_neto), 2),
            "ratio_gasto_ingreso": round(ratio, 2) if ratio is not None else None,
        },
        "confiabilidad_del_dato": {
            "transacciones_respaldadas": len(respaldadas),
            "con_validacion_sri": con_sri,
            "antiguedad_del_registro_meses": meses_activos,
            "indice_verificacion": round(indice, 2),
        },
    }


def _fuentes_ingreso(ingresos: list[dict]) -> list[dict]:
    por_fuente: dict[str, list[tuple[int, int]]] = defaultdict(list)
    tipo_ev: dict[str, bool] = {}
    for m in ingresos:
        alias = (m.get("comercio") or "Ingreso").strip() or "Ingreso"
        por_fuente[alias].append(_clave_mes(m["fecha"]))
        tipo_ev[alias] = tipo_ev.get(alias, False) or bool(m.get("sri"))
    fuentes = []
    for alias, meses in sorted(por_fuente.items(), key=lambda kv: -len(set(kv[1]))):
        fuentes.append(
            {
                "alias": alias,
                "meses_consecutivos": len(set(meses)),
                "evidencia": "factura_sri" if tipo_ev[alias] else "voucher_transferencia",
            }
        )
    return fuentes[:5]


def _tendencia(ingresos: list[dict], cerrados: set) -> str:
    por_mes: dict[tuple, Decimal] = defaultdict(lambda: Decimal("0"))
    for m in ingresos:
        por_mes[_clave_mes(m["fecha"])] += m["monto"]
    serie = [float(por_mes[mes]) for mes in sorted(cerrados) if mes in por_mes]
    if len(serie) < 2:
        return "estable"
    # Pendiente simple: promedio de la segunda mitad vs la primera.
    mitad = len(serie) // 2
    prim = sum(serie[:mitad]) / max(mitad, 1)
    seg = sum(serie[mitad:]) / max(len(serie) - mitad, 1)
    if prim == 0:
        return "estable"
    cambio = (seg - prim) / prim
    if cambio > 0.05:
        return "creciente"
    if cambio < -0.05:
        return "decreciente"
    return "estable"


def _categorias_top(gastos: list[dict]) -> list[str]:
    por_cat: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for m in gastos:
        cat = (m.get("categoria") or "otros").strip() or "otros"
        por_cat[cat] += m["monto"]
    return [c for c, _ in sorted(por_cat.items(), key=lambda kv: -kv[1])[:3]]


def _respaldado_pct_global(universo: list[dict], cerrados: set) -> float:
    en_ventana = [m for m in universo if _clave_mes(m["fecha"]) in cerrados]
    total = sum((m["monto"] for m in en_ventana), Decimal("0"))
    resp = sum((m["monto"] for m in en_ventana if m.get("respaldada")), Decimal("0"))
    return float(resp / total) if total else 0.0


def _peso_evidencia(respaldadas: list[dict]) -> Decimal:
    if not respaldadas:
        return Decimal("0")
    total = sum((m["monto"] for m in respaldadas), Decimal("0"))
    if total == 0:
        return Decimal("0")
    acum = sum(
        (m["monto"] * (_PESO_SRI if m.get("sri") else _PESO_OTRO_RESPALDO) for m in respaldadas),
        Decimal("0"),
    )
    return acum / total


def consultar_perfil(repo: Repository, user_id: UUID) -> dict:
    """Tool grounded del agente: devuelve el perfil calculado para que Luca lo
    explique. Resiliente: si algo falla, devuelve estado 'sin_datos'."""
    try:
        movimientos = repo.movimientos_para_perfil(user_id)
    except Exception:
        return {"estado": "sin_datos", "indice_verificacion": 0}
    perfil = calcular_perfil(movimientos)
    conf = perfil["confiabilidad_del_dato"]
    # Resumen plano para el prompt (Luca no recita el JSON entero). El índice se
    # entrega en escala 0–100 (así Luca lo dice como "71/100", no "0.71").
    return {
        "estado": perfil["estado"],
        "indice_verificacion_sobre_100": round(conf["indice_verificacion"] * 100),
        "meses_activo": perfil["usuario"]["meses_activo"],
        "ingreso_promedio_respaldado": perfil["ingresos"]["promedio_mensual"],
        "regularidad_ingresos_pct": round(perfil["ingresos"]["regularidad"] * 100),
        "transacciones_respaldadas": conf["transacciones_respaldadas"],
    }
