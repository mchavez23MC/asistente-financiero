"""Scheduler de alertas proactivas de presupuesto (§7.6) — Fase 7.

APScheduler in-process (cero infra extra). Es OTRO punto de entrada al mismo
núcleo: usa `Repository` y `ChannelAdapter`, los mismos puertos que el webhook.
La lógica está en `revisar_presupuestos` (pura, testeable); el scheduler solo
la agenda.

Ventana de 24h de WhatsApp (§7.6): fuera de la ventana Meta solo permite
plantillas pre-aprobadas. En pruebas se envía texto y se documenta la limitación.
Idempotencia: `alerts` evita notificar dos veces el mismo cruce por periodo.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.domain.models import Budget, Intencion, Message, RecurringIncome, Rol
from app.domain.ports import ChannelAdapter, Repository


def periodo_clave(periodo: str, hoy: date | None = None) -> str:
    """Identidad del periodo corriente, para idempotencia ('mensual:2026-07')."""
    hoy = hoy or date.today()
    if periodo == "semanal":
        iso = hoy.isocalendar()
        return f"semanal:{iso.year}-W{iso.week:02d}"
    if periodo == "anual":
        return f"anual:{hoy.year}"
    return f"mensual:{hoy.year}-{hoy.month:02d}"


def _mensaje_alerta(b: Budget, gastado: Decimal, porcentaje: float) -> str:
    return (
        f"⚠️ Alerta de presupuesto: llevas {porcentaje:.0f}% de tu límite de "
        f"{b.categoria} ({b.periodo}). Gastado ${gastado:.2f} de ${b.monto_limite:.2f}."
    )


async def revisar_presupuestos(
    repo: Repository, channel: ChannelAdapter, hoy: date | None = None
) -> int:
    """Revisa todos los presupuestos; alerta los que cruzaron umbral y no se han
    notificado este periodo. Devuelve cuántas alertas envió."""
    enviadas = 0
    for b in repo.get_all_budgets():
        if b.monto_limite <= 0:
            continue
        clave = periodo_clave(b.periodo, hoy)
        if repo.alerta_ya_enviada(b.id, clave):
            continue

        gastado = repo.sum_gastos(b.user_id, categoria=b.categoria, periodo=b.periodo)
        fraccion = float(gastado / b.monto_limite) if b.monto_limite else 0.0
        if fraccion < b.umbral_alerta:
            continue

        user = repo.get_user(b.user_id)
        if user is None:
            continue
        await channel.send(user, _mensaje_alerta(b, gastado, fraccion * 100))
        repo.marcar_alerta(b.user_id, b.id, clave)
        enviadas += 1
    return enviadas


def _mensaje_recordatorio(r: RecurringIncome) -> str:
    fuente = f" de _{r.fuente}_" if r.fuente else ""
    return (
        f"¡Hola! 🙌 Hoy suele llegarte tu *{r.categoria.lower()}*{fuente} de "
        f"*${r.monto:.2f}*. ¿Ya te cayó? Confírmame y lo anoto de una."
    )


async def recordar_ingresos_recurrentes(
    repo: Repository, channel: ChannelAdapter, hoy: date | None = None
) -> int:
    """Recuerda cada ingreso recurrente activo cuyo día ya llegó y aún no se
    recordó este mes. NO registra el ingreso: el usuario lo confirma por chat
    (D3). El recordatorio queda auditado en `messages`, así el 'sí' posterior del
    usuario llega al agente con el contexto de qué está confirmando. Devuelve
    cuántos recordatorios envió."""
    hoy = hoy or date.today()
    clave = periodo_clave("mensual", hoy)
    enviados = 0
    for r in repo.get_all_recurring_incomes():
        if hoy.day < r.dia_del_mes:
            continue
        if repo.recordatorio_ya_enviado(r.id, clave):
            continue
        user = repo.get_user(r.user_id)
        if user is None:
            continue
        texto = _mensaje_recordatorio(r)
        await channel.send(user, texto)
        repo.save_message(
            Message(
                user_id=r.user_id,
                rol=Rol.ASISTENTE,
                contenido=texto,
                intencion=Intencion.INGRESO,
            )
        )
        repo.marcar_recordatorio(r.id, clave)
        enviados += 1
    return enviados


def crear_scheduler(
    repo: Repository, channel: ChannelAdapter, intervalo_min: int = 30
) -> AsyncIOScheduler:
    """Agenda las revisiones periódicas. Arranca con `.start()` en el lifespan."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        revisar_presupuestos,
        trigger="interval",
        minutes=intervalo_min,
        args=[repo, channel],
        id="revisar_presupuestos",
        replace_existing=True,
    )
    scheduler.add_job(
        recordar_ingresos_recurrentes,
        trigger="interval",
        minutes=intervalo_min,
        args=[repo, channel],
        id="recordar_ingresos_recurrentes",
        replace_existing=True,
    )
    return scheduler
