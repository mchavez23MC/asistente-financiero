"""Fases 6 y 9 — panel humano (auth, cola, detalle, audit) y chat web plan B.

Panel: se prueba con FakeRepo montado en app.state; el chat web ejercita el
pipeline completo con un guardrail y un agente falsos (sin red).
"""

from __future__ import annotations

from base64 import b64encode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.models import AgentResult, GuardrailResult, Intencion, Ticket
from app.application.router import InMemoryAgentRegistry
from app.interfaces.api import panel, web_chat

from tests.test_walking_skeleton import FakeChannel, FakeRepo


def _auth_header(user="admin", pw="secreta"):
    return {"Authorization": "Basic " + b64encode(f"{user}:{pw}".encode()).decode()}


def _app_panel():
    app = FastAPI()
    repo = FakeRepo()
    u = repo.get_or_create_user("+50370000000", "Ana")
    repo.registrar_consentimiento(u.id)
    repo.create_ticket(Ticket(user_id=u.id, motivo="reclamo", contexto="pide reembolso"))
    app.state.repo = repo
    app.state.channel = FakeChannel()
    app.state.panel_auth = ("admin", "secreta")
    app.include_router(panel.router)
    return app, repo, u


# --- panel: auth -------------------------------------------------------------------
def test_panel_exige_auth():
    app, _, _ = _app_panel()
    assert TestClient(app).get("/panel").status_code == 401


def test_panel_cola_lista_tickets():
    app, _, _ = _app_panel()
    r = TestClient(app).get("/panel", headers=_auth_header())
    assert r.status_code == 200
    assert "reclamo" in r.text and "Cola de tickets" in r.text


def test_panel_detalle_muestra_conversacion_y_contexto():
    app, repo, u = _app_panel()
    ticket = repo.list_tickets()[0]
    r = TestClient(app).get(f"/panel/ticket/{ticket.id}", headers=_auth_header())
    assert r.status_code == 200
    assert "pide reembolso" in r.text  # contexto de escalación


def test_panel_cambiar_estado():
    app, repo, _ = _app_panel()
    ticket = repo.list_tickets()[0]
    client = TestClient(app)
    r = client.post(f"/panel/ticket/{ticket.id}/estado", data={"estado": "resuelto"},
                    headers=_auth_header(), follow_redirects=False)
    assert r.status_code == 303
    assert repo.get_ticket(ticket.id).estado == "resuelto"


def test_panel_responder_envia_por_el_canal():
    app, repo, _ = _app_panel()
    ticket = repo.list_tickets()[0]
    r = TestClient(app).post(f"/panel/ticket/{ticket.id}/responder", data={"texto": "hola"},
                             headers=_auth_header(), follow_redirects=False)
    assert r.status_code == 303
    assert app.state.channel.enviados[-1][1] == "hola"


def test_panel_audit_trail():
    app, repo, u = _app_panel()
    from app.domain.models import Message, Rol
    repo.save_message(Message(user_id=u.id, rol=Rol.ASISTENTE, contenido="hola", intencion=Intencion.GASTO, tool_llamada="registrar_gasto"))
    r = TestClient(app).get("/panel/audit", headers=_auth_header())
    assert r.status_code == 200 and "registrar_gasto" in r.text


# --- chat web (plan B) --------------------------------------------------------------
class SiempreOk:
    async def classify(self, texto):
        return GuardrailResult(sensible=False, fuente="stub")


class AgenteFijo:
    intent = "principal"

    async def handle(self, context) -> AgentResult:
        return AgentResult(respuesta=f"eco:{context.incoming.texto}", intencion=Intencion.OTRO)


def _app_webchat():
    app = FastAPI()
    repo = FakeRepo()
    registry = InMemoryAgentRegistry()
    registry.register(AgenteFijo())
    app.state.repo = repo
    app.state.guardrail = SiempreOk()
    app.state.registry = registry
    app.include_router(web_chat.router)
    return app, repo


def test_webchat_sirve_la_pagina():
    app, _ = _app_webchat()
    r = TestClient(app).get("/chat")
    assert r.status_code == 200 and "Asistente financiero" in r.text


def test_webchat_primer_mensaje_pide_consentimiento():
    app, repo = _app_webchat()
    r = TestClient(app).post("/chat/send", json={"texto": "hola"})
    assert r.status_code == 200
    respuestas = r.json()["respuestas"]
    assert any("acept" in x.lower() or "términos" in x.lower() for x in respuestas)


def test_webchat_segundo_mensaje_pasa_por_el_agente():
    app, repo = _app_webchat()
    client = TestClient(app)
    client.post("/chat/send", json={"texto": "hola"})  # consentimiento
    r = client.post("/chat/send", json={"texto": "prueba"})
    assert r.json()["respuestas"] == ["eco:prueba"]


# --- chat web: streaming (plan-latencia C3) -----------------------------------------
class AgenteStream:
    intent = "principal"

    async def handle(self, context) -> AgentResult:  # fallback no usado aquí
        return AgentResult(respuesta="fallback", intencion=Intencion.OTRO)

    async def handle_stream(self, context, capture):
        for frag in ["Hola", " ", "ñaño"]:
            yield frag
        capture.update(respuesta="Hola ñaño", intencion=Intencion.OTRO, tool_llamada=None)


def _app_webchat_stream():
    app = FastAPI()
    repo = FakeRepo()
    registry = InMemoryAgentRegistry()
    registry.register(AgenteStream())
    app.state.repo = repo
    app.state.guardrail = SiempreOk()
    app.state.registry = registry
    app.include_router(web_chat.router)
    return app, repo


def test_webchat_stream_emite_fragmentos_sse_y_audita():
    app, repo = _app_webchat_stream()
    client = TestClient(app)
    client.post("/chat/stream", json={"texto": "hola"})  # consentimiento
    r = client.post("/chat/stream", json={"texto": "prueba"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    # Los fragmentos llegan como eventos SSE JSON-encodeados.
    assert 'data: "Hola"' in r.text and "ñaño" in r.text
    # El audit trail guardó la respuesta completa reconstruida.
    assert repo.messages[-1].contenido == "Hola ñaño"


def test_webchat_stream_consentimiento_emite_aviso_legal():
    app, repo = _app_webchat_stream()
    r = TestClient(app).post("/chat/stream", json={"texto": "hola"})
    assert r.status_code == 200
    assert "acept" in r.text.lower() or "términos" in r.text.lower()
