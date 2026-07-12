"""H1 (ingresos) — lógica de `registrar_ingreso` y `configurar_ingreso_recurrente`.

Espejo de `gasto.py` para la plata que ENTRA. Mismo loop de confirmación (§3.1):
si falta el monto, el ingreso queda `pendiente_confirmacion` y se devuelven los
`faltantes`. El `tipo='ingreso'` mantiene los ingresos separados de los gastos en
`transactions`, para que `sum_gastos` (que filtra tipo='gasto') no los cuente.

El ingreso recurrente (sueldo base) NO se registra aquí: `configurar_ingreso_
recurrente` solo guarda la configuración; el scheduler recuerda y el usuario
confirma por chat (decisión D3 del plan). Ver `app/infra/scheduler.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from app.domain.models import RecurringIncome, Transaction, TransactionStatus, TransactionTipo
from app.domain.ports import Repository

# Solo el monto es obligatorio para confirmar un ingreso (igual que un gasto).
OBLIGATORIOS = ("monto",)


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


def registrar_ingreso(
    repo: Repository,
    user_id: UUID,
    monto=None,
    fecha: Optional[str] = None,
    categoria: Optional[str] = None,
    fuente: Optional[str] = None,
) -> dict:
    """Registra un ingreso. `fuente` se guarda en la columna `comercio` (misma
    columna, semántica según tipo). Merge sobre la pendiente de ingreso si existe."""
    pendiente = repo.get_pending_transaction(user_id, tipo="ingreso")

    monto_dec = _a_decimal(monto)
    if pendiente is not None:
        monto_dec = monto_dec if monto_dec is not None else pendiente.monto
        fecha = fecha or (pendiente.fecha.isoformat() if pendiente.fecha else None)
        categoria = categoria or pendiente.categoria
        fuente = fuente or pendiente.comercio

    faltantes = [c for c in OBLIGATORIOS if c == "monto" and monto_dec is None]
    confirmada = not faltantes
    fecha_val = _parse_fecha(fecha) or (date.today() if confirmada else None)

    tx = Transaction(
        id=pendiente.id if pendiente else None,
        user_id=user_id,
        tipo=TransactionTipo.INGRESO,
        monto=monto_dec,
        fecha=fecha_val,
        categoria=categoria,
        comercio=fuente,
        status=(
            TransactionStatus.CONFIRMADA
            if confirmada
            else TransactionStatus.PENDIENTE_CONFIRMACION
        ),
    )
    guardada = repo.save_transaction(tx)
    total = None
    if confirmada and guardada.categoria:
        total = float(repo.sum_ingresos(user_id, categoria=guardada.categoria, periodo="mensual"))
    return {
        "transaction_id": str(guardada.id),
        "status": guardada.status,
        "faltantes": faltantes,
        "total_categoria_periodo": total,
    }


def configurar_ingreso_recurrente(
    repo: Repository,
    user_id: UUID,
    accion: str = "crear",
    monto=None,
    categoria: Optional[str] = None,
    fuente: Optional[str] = None,
    dia_del_mes: Optional[int] = None,
) -> dict:
    """Crea / actualiza / desactiva un ingreso fijo mensual. NO registra ninguna
    transacción: el registro real lo confirma el usuario cuando el scheduler le
    recuerda (D3). Devuelve el estado resultante de la recurrencia."""
    existentes = repo.get_recurring_incomes(user_id, solo_activos=False)

    if accion == "desactivar":
        if not existentes:
            return {"error": "no_hay_recurrencia", "activo": False}
        # Sin un id explícito, se desactiva la más reciente activa (caso típico).
        objetivo = next((r for r in reversed(existentes) if r.activo), existentes[-1])
        guardada = repo.save_recurring_income(objetivo.model_copy(update={"activo": False}))
        return _resumen(guardada)

    dia = _dia_valido(dia_del_mes)
    monto_dec = _a_decimal(monto)

    if accion == "actualizar" and existentes:
        objetivo = next((r for r in reversed(existentes) if r.activo), existentes[-1])
        cambios = {"activo": True}
        if monto_dec is not None:
            cambios["monto"] = monto_dec
        if categoria:
            cambios["categoria"] = categoria
        if fuente is not None:
            cambios["fuente"] = fuente
        if dia is not None:
            cambios["dia_del_mes"] = dia
        guardada = repo.save_recurring_income(objetivo.model_copy(update=cambios))
        return _resumen(guardada)

    # crear (o actualizar sin recurrencia previa): requiere monto y día.
    if monto_dec is None or dia is None:
        faltan = [n for n, v in (("monto", monto_dec), ("dia_del_mes", dia)) if v is None]
        return {"error": "faltan_datos", "faltantes": faltan}
    guardada = repo.save_recurring_income(
        RecurringIncome(
            user_id=user_id,
            monto=monto_dec,
            categoria=categoria or "Salario",
            fuente=fuente,
            dia_del_mes=dia,
            activo=True,
        )
    )
    return _resumen(guardada)


def _dia_valido(dia: Optional[int]) -> Optional[int]:
    """Acota el día a 1..28 (evita la casuística de febrero). Un día > 28 se
    ajusta a 28; el prompt le pide a Luca avisarle al usuario del ajuste."""
    if dia is None:
        return None
    try:
        d = int(dia)
    except (TypeError, ValueError):
        return None
    if d < 1:
        return 1
    return min(d, 28)


def _resumen(r: RecurringIncome) -> dict:
    return {
        "recurring_id": str(r.id),
        "activo": r.activo,
        "monto": float(r.monto),
        "categoria": r.categoria,
        "dia_del_mes": r.dia_del_mes,
    }
