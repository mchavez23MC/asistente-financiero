"""Fase 7 — scheduler de alertas proactivas (§7.6).

Gate: bajar un presupuesto (o subir el gasto) que cruza el umbral → en el tick
llega la alerta; idempotencia: no se alerta dos veces el mismo cruce/periodo.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.models import Budget, Transaction
from app.infra.scheduler import periodo_clave, revisar_presupuestos

from tests.test_walking_skeleton import FakeChannel, FakeRepo


def _setup(gastado: str, limite: str, umbral: float = 0.8):
    repo, channel = FakeRepo(), FakeChannel()
    user = repo.get_or_create_user("+50370000000", "Ana")
    repo.registrar_consentimiento(user.id)
    repo.budgets.append(
        Budget(user_id=user.id, categoria="comida", monto_limite=Decimal(limite), umbral_alerta=umbral)
    )
    if Decimal(gastado) > 0:
        repo.save_transaction(
            Transaction(user_id=user.id, monto=Decimal(gastado), categoria="comida", status="confirmada")
        )
    return repo, channel, user


def test_periodo_clave_mensual():
    assert periodo_clave("mensual", date(2026, 7, 11)) == "mensual:2026-07"
    assert periodo_clave("anual", date(2026, 7, 11)) == "anual:2026"


async def test_alerta_cuando_cruza_umbral():
    repo, channel, _ = _setup(gastado="85", limite="100", umbral=0.8)
    enviadas = await revisar_presupuestos(repo, channel)
    assert enviadas == 1
    assert "85%" in channel.enviados[0][1] or "85" in channel.enviados[0][1]


async def test_no_alerta_bajo_umbral():
    repo, channel, _ = _setup(gastado="50", limite="100", umbral=0.8)
    enviadas = await revisar_presupuestos(repo, channel)
    assert enviadas == 0 and channel.enviados == []


async def test_idempotencia_no_alerta_dos_veces():
    repo, channel, _ = _setup(gastado="90", limite="100")
    assert await revisar_presupuestos(repo, channel) == 1
    assert await revisar_presupuestos(repo, channel) == 0  # ya notificado este periodo
    assert len(channel.enviados) == 1
