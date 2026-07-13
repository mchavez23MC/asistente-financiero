"""Fase 11 — `configurar_presupuesto`: presupuestos por chat (paridad con la webapp).

Cubre la lógica de la tool (crear, actualizar con monto anterior, faltantes,
umbral) y el dispatch en el agente principal con LLM scripteado.
"""

from __future__ import annotations

from decimal import Decimal

from app.application.agents.presupuesto import configurar_presupuesto, consultar_presupuesto

from tests.test_agente import ScriptedLLM, _agente, _contexto, _texto, _tool
from tests.test_walking_skeleton import FakeRepo


def _user(repo: FakeRepo):
    return repo.get_or_create_user("+50370000000", "Ana")


# --- lógica de la tool -------------------------------------------------------------
def test_crear_presupuesto_nuevo():
    repo = FakeRepo()
    u = _user(repo)
    r = configurar_presupuesto(repo, u.id, categoria="Comida", monto_limite=100)

    assert r["categoria"] == "comida"  # normalizada como en la webapp
    assert r["monto_limite"] == 100.0
    assert r["periodo"] == "mensual"  # default
    assert r["umbral_alerta"] == 0.8  # default del modelo
    assert r["anterior_monto_limite"] is None
    assert len(repo.budgets) == 1


def test_actualizar_presupuesto_devuelve_monto_anterior():
    repo = FakeRepo()
    u = _user(repo)
    configurar_presupuesto(repo, u.id, categoria="comida", monto_limite=80, umbral_alerta=0.9)
    r = configurar_presupuesto(repo, u.id, categoria="comida", monto_limite=100)

    assert r["anterior_monto_limite"] == 80.0
    assert r["monto_limite"] == 100.0
    assert r["umbral_alerta"] == 0.9  # se conserva el umbral configurado
    assert len(repo.budgets) == 1  # upsert, no duplica


def test_presupuestos_distintos_periodos_conviven():
    repo = FakeRepo()
    u = _user(repo)
    configurar_presupuesto(repo, u.id, categoria="comida", monto_limite=100, periodo="mensual")
    r = configurar_presupuesto(repo, u.id, categoria="comida", monto_limite=30, periodo="semanal")

    assert r["anterior_monto_limite"] is None  # el semanal es otro presupuesto
    assert len(repo.budgets) == 2


def test_sin_monto_o_categoria_devuelve_faltantes_sin_guardar():
    repo = FakeRepo()
    u = _user(repo)
    r1 = configurar_presupuesto(repo, u.id, categoria="comida")
    r2 = configurar_presupuesto(repo, u.id, monto_limite=100)
    r3 = configurar_presupuesto(repo, u.id, categoria="comida", monto_limite=-5)

    assert r1 == {"error": "faltan_datos", "faltantes": ["monto_limite"]}
    assert r2 == {"error": "faltan_datos", "faltantes": ["categoria"]}
    assert r3["error"] == "faltan_datos"  # monto negativo no es válido
    assert repo.budgets == []


def test_umbral_en_porcentaje_se_normaliza():
    repo = FakeRepo()
    u = _user(repo)
    r = configurar_presupuesto(repo, u.id, categoria="comida", monto_limite=100, umbral_alerta=90)
    assert r["umbral_alerta"] == 0.9


def test_presupuesto_creado_alimenta_consultar_presupuesto():
    """El límite puesto por chat se refleja en la consulta H2 (grounded)."""
    repo = FakeRepo()
    u = _user(repo)
    configurar_presupuesto(repo, u.id, categoria="comida", monto_limite=100)
    from app.domain.models import Transaction

    repo.save_transaction(
        Transaction(user_id=u.id, monto=Decimal("40"), categoria="comida", status="confirmada")
    )
    r = consultar_presupuesto(repo, u.id, periodo="mensual", categoria="comida")
    assert r["limite"] == 100.0 and r["gastado"] == 40.0 and r["porcentaje"] == 40.0


# --- dispatch en el agente ----------------------------------------------------------
async def test_agente_configura_presupuesto_via_tool():
    repo = FakeRepo()
    llm = ScriptedLLM(
        [
            _tool("configurar_presupuesto", {"categoria": "comida", "monto_limite": 100}),
            _texto("Listo, te dejé $100 mensuales en comida; te aviso cuando te acerques."),
        ]
    )
    result = await _agente(llm, repo).handle(_contexto(repo, "ponme un límite de 100 en comida"))

    assert result.intencion == "presupuesto"
    assert result.tool_llamada == "configurar_presupuesto"
    assert len(repo.budgets) == 1
    assert repo.budgets[0].monto_limite == Decimal("100")
    # El tool_result que vio el modelo incluye el presupuesto guardado.
    tool_result = llm.llamadas[1]["messages"][-1]["content"][0]["content"]
    assert "budget_id" in tool_result and '"monto_limite": 100' in tool_result
