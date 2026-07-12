"""API JSON de la webapp (rama webapp) — /api/chat, /api/estado, /api/presupuestos.

Mismo patrón que test_panel_y_webchat: FakeRepo + guardrail y agente falsos,
sin red. Verifica el contrato que consume webapp/assets/js/api.js y el
aislamiento por usuario (un teléfono nunca ve datos de otro).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.router import InMemoryAgentRegistry
from app.domain.models import AgentResult, GuardrailResult, Intencion, Ticket, Transaction
from app.interfaces.api import webapp_api

from tests.test_walking_skeleton import FakeRepo


class SiempreOk:
    async def classify(self, texto):
        return GuardrailResult(sensible=False, fuente="stub")


class AgenteFijo:
    intent = "principal"

    async def handle(self, context) -> AgentResult:
        return AgentResult(respuesta=f"eco:{context.incoming.texto}", intencion=Intencion.OTRO)


def _app():
    app = FastAPI()
    repo = FakeRepo()
    registry = InMemoryAgentRegistry()
    registry.register(AgenteFijo())
    app.state.repo = repo
    app.state.guardrail = SiempreOk()
    app.state.registry = registry
    app.include_router(webapp_api.router)
    return app, repo


TEL = "+593987651234"


def _con_consentimiento(repo, telefono=TEL):
    u = repo.get_or_create_user(telefono, "Ana")
    return repo.registrar_consentimiento(u.id)


# --- /api/chat ----------------------------------------------------------------
def test_chat_exige_telefono_valido():
    app, _ = _app()
    r = TestClient(app).post("/api/chat", json={"telefono": "abc", "texto": "hola"})
    assert r.status_code == 422


def test_chat_corre_el_pipeline_completo():
    app, repo = _app()
    client = TestClient(app)
    client.post("/api/chat", json={"telefono": TEL, "texto": "hola"})  # consentimiento
    r = client.post("/api/chat", json={"telefono": TEL, "texto": "prueba"})
    assert r.status_code == 200
    assert r.json()["respuestas"] == ["eco:prueba"]


# --- /api/estado ---------------------------------------------------------------
def test_estado_devuelve_snapshot_del_usuario():
    app, repo = _app()
    u = _con_consentimiento(repo)
    repo.save_transaction(Transaction(
        user_id=u.id, monto=Decimal("12.50"), fecha=date.today(),
        categoria="comida", status="confirmada",
    ))
    repo.create_ticket(Ticket(user_id=u.id, motivo="reclamo", contexto="caso x"))

    r = TestClient(app).get("/api/estado", params={"telefono": TEL})
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["telefono"] == TEL
    assert data["resumen"]["gastado_mes"] == 12.5
    assert data["transactions"][0]["categoria"] == "comida"
    assert data["tickets"][0]["motivo"] == "reclamo"


def test_estado_aisla_por_usuario():
    """El invariante §7.3.2 aplicado a la webapp: otro teléfono no ve nada."""
    app, repo = _app()
    u = _con_consentimiento(repo)
    repo.save_transaction(Transaction(
        user_id=u.id, monto=Decimal("99"), fecha=date.today(),
        categoria="comida", status="confirmada",
    ))
    r = TestClient(app).get("/api/estado", params={"telefono": "+593999999999"})
    assert r.status_code == 200
    data = r.json()
    assert data["transactions"] == [] and data["tickets"] == []
    assert data["resumen"]["gastado_mes"] == 0


# --- /api/presupuestos ----------------------------------------------------------
def test_crear_presupuesto_y_verlo_en_estado():
    app, repo = _app()
    _con_consentimiento(repo)
    client = TestClient(app)
    r = client.post("/api/presupuestos", json={
        "telefono": TEL, "categoria": "Comida", "monto_limite": 200, "umbral_alerta": 0.8,
    })
    assert r.status_code == 201
    estado = client.get("/api/estado", params={"telefono": TEL}).json()
    assert estado["budgets"][0]["categoria"] == "comida"  # se normaliza a minúsculas
    assert estado["budgets"][0]["monto_limite"] == 200.0


def test_crear_presupuesto_upsert_no_duplica():
    app, repo = _app()
    _con_consentimiento(repo)
    client = TestClient(app)
    for limite in (200, 250):
        client.post("/api/presupuestos", json={
            "telefono": TEL, "categoria": "comida", "monto_limite": limite,
        })
    estado = client.get("/api/estado", params={"telefono": TEL}).json()
    assert len(estado["budgets"]) == 1
    assert estado["budgets"][0]["monto_limite"] == 250.0


def test_crear_presupuesto_invalido_da_422():
    app, repo = _app()
    _con_consentimiento(repo)
    r = TestClient(app).post("/api/presupuestos", json={
        "telefono": TEL, "categoria": "comida", "monto_limite": -5,
    })
    assert r.status_code == 422
