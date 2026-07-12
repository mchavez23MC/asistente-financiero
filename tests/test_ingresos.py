"""Ingresos (H1 extendido) — registro puntual, recurrente y aislamiento del total.

Gates del plan-implementacion-ingresos:
  - I1: sum_gastos EXCLUYE ingresos (el bug silencioso); ingreso incompleto →
        pendiente → siguiente mensaje completa; pendientes de tipos distintos no
        se cruzan; balance = ingresos − gastos.
  - I2: el agente scripted usa registrar_ingreso y resuelve gasto+ingreso mixto.
  - I3: el scheduler recuerda el recurrente (idempotente) y lo audita.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.application.agents.ingreso import (
    configurar_ingreso_recurrente,
    registrar_ingreso,
)
from app.application.agents.gasto import registrar_gasto
from app.application.agents.presupuesto import consultar_presupuesto
from app.application.agents.principal import MainAgent
from app.infra.scheduler import recordar_ingresos_recurrentes
from app.domain.models import (
    AgentContext,
    IncomingMessage,
    LLMResponse,
    RecurringIncome,
    ToolCall,
    User,
)

from tests.test_agente import FakeSoporte, ScriptedLLM, _tool, _texto
from tests.test_walking_skeleton import FakeChannel, FakeRepo


def _uid(repo: FakeRepo) -> User:
    return repo.get_or_create_user("+50370000000", "Ana")


# --- I1: registro puntual + el aislamiento crítico del total ------------------
def test_registrar_ingreso_confirmado():
    repo = FakeRepo()
    u = _uid(repo)
    r = registrar_ingreso(repo, u.id, monto=450, categoria="Salario", fuente="Empresa X")
    assert r["status"] == "confirmada" and not r["faltantes"]
    tx = repo.transactions[0]
    assert tx.tipo == "ingreso" and tx.monto == Decimal("450")
    assert tx.comercio == "Empresa X"  # la fuente vive en la columna comercio


def test_sum_gastos_excluye_ingresos():
    """EL bug a evitar: un ingreso NO debe contar como gasto."""
    repo = FakeRepo()
    u = _uid(repo)
    registrar_gasto(repo, u.id, monto=40, categoria="comida")
    registrar_ingreso(repo, u.id, monto=450, categoria="Salario")
    assert repo.sum_gastos(u.id) == Decimal("40")     # solo el gasto
    assert repo.sum_ingresos(u.id) == Decimal("450")  # solo el ingreso


def test_balance_en_consultar_presupuesto():
    repo = FakeRepo()
    u = _uid(repo)
    registrar_gasto(repo, u.id, monto=120, categoria="comida")
    registrar_ingreso(repo, u.id, monto=450, categoria="Salario")
    out = consultar_presupuesto(repo, u.id)
    assert out["gastado"] == 120.0 and out["ingresos"] == 450.0
    assert out["balance"] == 330.0  # 450 − 120


def test_ingreso_incompleto_queda_pendiente_y_luego_se_completa():
    repo = FakeRepo()
    u = _uid(repo)
    r1 = registrar_ingreso(repo, u.id, categoria="Freelance/Independiente")
    assert r1["status"] == "pendiente_confirmacion" and "monto" in r1["faltantes"]
    # Siguiente mensaje da el monto → completa la MISMA transacción.
    r2 = registrar_ingreso(repo, u.id, monto=80)
    assert r2["status"] == "confirmada"
    assert len([t for t in repo.transactions if t.tipo == "ingreso"]) == 1


def test_pendientes_de_gasto_e_ingreso_no_se_cruzan():
    repo = FakeRepo()
    u = _uid(repo)
    registrar_gasto(repo, u.id, categoria="comida")       # gasto pendiente (sin monto)
    registrar_ingreso(repo, u.id, categoria="Salario")    # ingreso pendiente (sin monto)
    # Completar el ingreso NO debe tocar el gasto pendiente.
    registrar_ingreso(repo, u.id, monto=450)
    gasto_pend = repo.get_pending_transaction(u.id, tipo="gasto")
    ing = [t for t in repo.transactions if t.tipo == "ingreso"][0]
    assert gasto_pend is not None and gasto_pend.monto is None  # el gasto sigue pendiente
    assert ing.status == "confirmada" and ing.monto == Decimal("450")


# --- I1: ingreso recurrente (config, no registro) -----------------------------
def test_configurar_recurrente_crear_y_ajuste_de_dia():
    repo = FakeRepo()
    u = _uid(repo)
    r = configurar_ingreso_recurrente(repo, u.id, accion="crear", monto=450, dia_del_mes=31)
    assert r["dia_del_mes"] == 28 and r["activo"] and r["monto"] == 450.0
    # No registra ninguna transacción — solo configura.
    assert repo.transactions == []
    assert len(repo.get_recurring_incomes(u.id)) == 1


def test_configurar_recurrente_actualizar_y_desactivar():
    repo = FakeRepo()
    u = _uid(repo)
    configurar_ingreso_recurrente(repo, u.id, accion="crear", monto=450, dia_del_mes=30)
    up = configurar_ingreso_recurrente(repo, u.id, accion="actualizar", monto=500)
    assert up["monto"] == 500.0 and up["dia_del_mes"] == 28
    off = configurar_ingreso_recurrente(repo, u.id, accion="desactivar")
    assert off["activo"] is False
    assert repo.get_all_recurring_incomes() == []  # ya no está activo


# --- I2: agente ---------------------------------------------------------------
def _agente(llm, repo):
    return MainAgent(llm=llm, repo=repo, soporte=FakeSoporte({}))


def _ctx(repo, texto):
    u = _uid(repo)
    return AgentContext(user=u, incoming=IncomingMessage(canal="web", telefono=u.telefono, texto=texto), historial=[])


async def test_agente_registra_ingreso():
    repo = FakeRepo()
    llm = ScriptedLLM(
        [_tool("registrar_ingreso", {"monto": 450, "categoria": "Salario"}), _texto("¡De una! Anoté $450.")]
    )
    result = await _agente(llm, repo).handle(_ctx(repo, "me pagaron el sueldo, 450"))
    assert result.intencion == "ingreso" and result.tool_llamada == "registrar_ingreso"
    assert repo.transactions[0].tipo == "ingreso"


async def test_agente_gasto_e_ingreso_mixto_una_pasada():
    repo = FakeRepo()
    doble = LLMResponse(
        tool_calls=[
            ToolCall(id="a", nombre="registrar_gasto", argumentos={"monto": 20, "categoria": "comida"}),
            ToolCall(id="b", nombre="registrar_ingreso", argumentos={"monto": 100, "categoria": "Venta"}),
        ],
        stop_reason="tool_use",
    )
    llm = ScriptedLLM([doble, _texto("Anoté el gasto y el ingreso.")])
    await _agente(llm, repo).handle(_ctx(repo, "gasté 20 en comida y vendí algo en 100"))
    tipos = sorted(t.tipo for t in repo.transactions)
    assert tipos == ["gasto", "ingreso"]


# --- I3: scheduler recuerda el recurrente -------------------------------------
async def test_recordatorio_recurrente_idempotente_y_auditado():
    repo, channel = FakeRepo(), FakeChannel()
    u = _uid(repo)
    repo.save_recurring_income(
        RecurringIncome(user_id=u.id, monto=Decimal("450"), categoria="Salario", dia_del_mes=1)
    )
    hoy = date(2026, 7, 15)  # día 15 ≥ día 1 configurado
    n1 = await recordar_ingresos_recurrentes(repo, channel, hoy)
    n2 = await recordar_ingresos_recurrentes(repo, channel, hoy)  # mismo mes → no repite
    assert n1 == 1 and n2 == 0
    assert len(channel.enviados) == 1
    # El recordatorio quedó en el historial (para dar contexto al 'sí' del usuario).
    assert repo.messages[-1].intencion == "ingreso"


async def test_recordatorio_no_se_envia_antes_del_dia():
    repo, channel = FakeRepo(), FakeChannel()
    u = _uid(repo)
    repo.save_recurring_income(
        RecurringIncome(user_id=u.id, monto=Decimal("450"), dia_del_mes=28)
    )
    n = await recordar_ingresos_recurrentes(repo, channel, date(2026, 7, 10))  # día 10 < 28
    assert n == 0 and channel.enviados == []
