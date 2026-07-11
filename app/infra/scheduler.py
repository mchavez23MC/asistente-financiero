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

from app.domain.models import Budget
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


def crear_scheduler(
    repo: Repository, channel: ChannelAdapter, intervalo_min: int = 30
) -> AsyncIOScheduler:
    """Agenda la revisión periódica. Arranca con `.start()` en el lifespan de la app."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        revisar_presupuestos,
        trigger="interval",
        minutes=intervalo_min,
        args=[repo, channel],
        id="revisar_presupuestos",
        replace_existing=True,
    )
    return scheduler
