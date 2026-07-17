"""Pipeline de estado de cuenta (plan de documentos, E3).

Filas crudas de un CSV/XLSX (tabular.py) → movimientos normalizados y
deduplicados, listos para el staging (`document_items`) que el usuario revisa
en la webapp. NO toca `transactions`: eso ocurre solo tras la confirmación
humana (riesgo R1).

v1 detecta las columnas por el nombre del encabezado (fecha, descripción,
monto o débito/crédito) — cubre el caso común de la banca ecuatoriana sin
necesitar el wizard de mapeo (que llega para formatos raros). El signo decide
el tipo: monto negativo o columna débito = gasto; positivo o crédito = ingreso.

La categorización por contraparte/IA NO va aquí todavía: los movimientos salen
sin categoría (confianza media) y el usuario los clasifica en la revisión.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional
from uuid import UUID

from app.domain.models import DocumentItem, DocumentItemEstado

# Palabras clave por rol de columna (sin tildes, minúsculas).
_ENCABEZADOS = {
    "fecha": ("fecha", "date", "dia"),
    "descripcion": ("descripcion", "detalle", "concepto", "referencia", "transaccion", "movimiento"),
    "monto": ("monto", "valor", "importe", "amount"),
    "debito": ("debito", "debe", "cargo", "egreso", "retiro"),
    "credito": ("credito", "haber", "abono", "ingreso", "deposito"),
}

_UMBRAL_REVISION_DEFAULT = 5


@dataclass
class ResultadoEstadoCuenta:
    items: list[DocumentItem]
    resumen: str
    total_gastos: Decimal = Decimal("0")
    total_ingresos: Decimal = Decimal("0")
    duplicados: int = 0
    columnas_detectadas: bool = True
    filas_ignoradas: int = 0
    advertencias: list[str] = field(default_factory=list)


def procesar_estado_cuenta(
    filas: list[list[str]],
    document_id: UUID,
    user_id: UUID,
    es_duplicado: Optional[Callable[[date, Decimal], bool]] = None,
) -> ResultadoEstadoCuenta:
    """Filas crudas → items de staging. `es_duplicado(fecha, monto)` marca las
    filas ya presentes en `transactions` del periodo (cruce fecha+monto)."""
    es_duplicado = es_duplicado or (lambda f, m: False)
    mapeo, fila_encabezado = _detectar_columnas(filas)
    if mapeo is None:
        return ResultadoEstadoCuenta(
            items=[],
            resumen="No pude reconocer las columnas del archivo.",
            columnas_detectadas=False,
        )

    items: list[DocumentItem] = []
    total_g = total_i = Decimal("0")
    duplicados = ignoradas = 0
    n = 0
    for fila in filas[fila_encabezado + 1 :]:
        fecha = _fecha(_celda(fila, mapeo.get("fecha")))
        monto, tipo = _monto_y_tipo(fila, mapeo)
        descripcion = _celda(fila, mapeo.get("descripcion")) or "(sin descripción)"
        if monto is None or monto == 0:
            ignoradas += 1
            continue
        n += 1
        estado = DocumentItemEstado.PENDIENTE
        if fecha is not None and es_duplicado(fecha, abs(monto)):
            estado = DocumentItemEstado.DUPLICADO
            duplicados += 1
        elif tipo == "gasto":
            total_g += abs(monto)
        else:
            total_i += abs(monto)
        # Confianza: alta si fecha y monto legibles; media si falta la fecha.
        confianza = 0.9 if fecha is not None else 0.6
        items.append(
            DocumentItem(
                document_id=document_id,
                user_id=user_id,
                n_linea=n,
                fecha=fecha,
                descripcion_raw=descripcion[:500],
                monto=abs(monto),
                tipo=tipo,
                confianza=confianza,
                estado=estado,
            )
        )

    resumen = (
        f"{len(items)} movimientos: ${total_g:.2f} en gastos, ${total_i:.2f} en "
        f"ingresos" + (f", {duplicados} posibles duplicados" if duplicados else "")
    )
    return ResultadoEstadoCuenta(
        items=items,
        resumen=resumen,
        total_gastos=total_g,
        total_ingresos=total_i,
        duplicados=duplicados,
        filas_ignoradas=ignoradas,
    )


def es_carga_masiva(n_items: int, umbral: int = _UMBRAL_REVISION_DEFAULT) -> bool:
    """≥ umbral movimientos → tarea de webapp; menos podría ir por chat (E3 fase
    posterior). Por ahora cualquier estado de cuenta con items va a revisión."""
    return n_items >= 1


# --- internos ----------------------------------------------------------------
def _normalizar(texto: str) -> str:
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return sin_tildes.lower().strip()


def _detectar_columnas(filas: list[list[str]]) -> tuple[Optional[dict], int]:
    """Busca la fila de encabezado (una que mencione fecha + monto/débito) y
    devuelve el mapeo rol→índice y el índice de esa fila."""
    for i, fila in enumerate(filas[:15]):
        mapeo: dict[str, int] = {}
        for j, celda in enumerate(fila):
            norma = _normalizar(celda)
            for rol, claves in _ENCABEZADOS.items():
                if rol not in mapeo and any(k in norma for k in claves):
                    mapeo[rol] = j
        tiene_monto = "monto" in mapeo or "debito" in mapeo or "credito" in mapeo
        if "fecha" in mapeo and tiene_monto:
            return mapeo, i
    return None, -1


def _celda(fila: list[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(fila):
        return ""
    return fila[idx].strip()


def _fecha(crudo: str) -> Optional[date]:
    crudo = crudo.split()[0] if crudo else ""
    m = re.match(r"^(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})$", crudo)
    if not m:
        return None
    a, b, c = m.groups()
    try:
        if len(a) == 4:  # yyyy-mm-dd
            return date(int(a), int(b), int(c))
        anio = int(c) + 2000 if len(c) == 2 else int(c)  # dd/mm/yyyy
        return date(anio, int(b), int(a))
    except ValueError:
        return None


def _a_decimal(crudo: str) -> Optional[Decimal]:
    crudo = (crudo or "").strip().replace("$", "").replace(" ", "")
    if not crudo:
        return None
    negativo = crudo.startswith("(") and crudo.endswith(")")  # contable: (123)
    crudo = crudo.strip("()")
    # Miles con '.' y decimales con ',' (formato es-EC) o al revés: se normaliza
    # asumiendo que el ÚLTIMO separador es el decimal.
    if "," in crudo and "." in crudo:
        if crudo.rfind(",") > crudo.rfind("."):
            crudo = crudo.replace(".", "").replace(",", ".")
        else:
            crudo = crudo.replace(",", "")
    else:
        crudo = crudo.replace(",", ".")
    try:
        valor = Decimal(crudo)
    except InvalidOperation:
        return None
    return -valor if negativo else valor


def _monto_y_tipo(fila: list[str], mapeo: dict) -> tuple[Optional[Decimal], str]:
    """Devuelve (monto_con_signo, tipo). Columna única: signo decide. Columnas
    débito/crédito separadas: débito=gasto, crédito=ingreso."""
    if "monto" in mapeo:
        valor = _a_decimal(_celda(fila, mapeo["monto"]))
        if valor is None:
            return None, "gasto"
        return valor, ("gasto" if valor < 0 else "ingreso")
    debito = _a_decimal(_celda(fila, mapeo.get("debito"))) if "debito" in mapeo else None
    credito = _a_decimal(_celda(fila, mapeo.get("credito"))) if "credito" in mapeo else None
    if debito and debito != 0:
        return -abs(debito), "gasto"
    if credito and credito != 0:
        return abs(credito), "ingreso"
    return None, "gasto"
