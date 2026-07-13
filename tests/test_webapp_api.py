"""API de la webapp con autenticación OTP — /api/auth/*, /api/chat, /api/estado,
/api/presupuestos.

Mismo patrón que test_panel_y_webchat: FakeRepo + guardrail y agente falsos,
sin red. El canal falso captura el mensaje de WhatsApp con el OTP, del que el
test extrae el código — exactamente lo que haría el dueño del teléfono.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.auth import AuthService
from app.application.router import InMemoryAgentRegistry
from app.domain.models import AgentResult, GuardrailResult, Intencion, Ticket, Transaction
from app.interfaces.api import webapp_api

from tests.test_walking_skeleton import FakeChannel, FakeRepo


class SiempreOk:
    async def classify(self, texto):
        return GuardrailResult(sensible=False, fuente="stub")


class AgenteFijo:
    intent = "principal"

    async def handle(self, context) -> AgentResult:
        return AgentResult(respuesta=f"eco:{context.incoming.texto}", intencion=Intencion.OTRO)


def _app(demo_otp: str = ""):
    app = FastAPI()
    repo = FakeRepo()
    canal_otp = FakeChannel()
    registry = InMemoryAgentRegistry()
    registry.register(AgenteFijo())
    app.state.repo = repo
    app.state.guardrail = SiempreOk()
    app.state.registry = registry
    app.state.auth = AuthService(repo, canal_otp, demo_otp=demo_otp)
    app.include_router(webapp_api.router)
    return app, repo, canal_otp


TEL = "+593987651234"


def _codigo_enviado(canal: FakeChannel) -> str:
    """Extrae el OTP del último WhatsApp capturado (lo que ve el dueño)."""
    texto = canal.enviados[-1][1]
    return re.search(r"\b(\d{6})\b", texto).group(1)


def _login(client: TestClient, canal: FakeChannel, telefono: str = TEL) -> dict:
    assert client.post("/api/auth/solicitar", json={"telefono": telefono}).status_code == 202
    codigo = _codigo_enviado(canal)
    r = client.post("/api/auth/verificar", json={"telefono": telefono, "codigo": codigo})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- auth: flujo feliz -----------------------------------------------------------
def test_flujo_otp_completo():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)
    r = client.get("/api/estado", headers=headers)
    assert r.status_code == 200
    assert r.json()["user"]["telefono"] == TEL


def test_otp_se_envia_por_whatsapp_y_no_se_guarda_en_claro():
    app, repo, canal = _app()
    TestClient(app).post("/api/auth/solicitar", json={"telefono": TEL})
    codigo = _codigo_enviado(canal)
    guardado = repo.get_auth_code_activo(TEL)
    assert codigo not in guardado.codigo_hash  # en reposo solo el hash


# --- auth: políticas OWASP -------------------------------------------------------
def test_codigo_incorrecto_da_401_y_consume_intento():
    app, repo, canal = _app()
    client = TestClient(app)
    client.post("/api/auth/solicitar", json={"telefono": TEL})
    r = client.post("/api/auth/verificar", json={"telefono": TEL, "codigo": "000000"})
    assert r.status_code == 401
    assert repo.get_auth_code_activo(TEL).intentos == 1


def test_codigo_es_de_un_solo_uso():
    app, repo, canal = _app()
    client = TestClient(app)
    client.post("/api/auth/solicitar", json={"telefono": TEL})
    codigo = _codigo_enviado(canal)
    assert client.post("/api/auth/verificar", json={"telefono": TEL, "codigo": codigo}).status_code == 200
    # El mismo código otra vez → rechazado.
    assert client.post("/api/auth/verificar", json={"telefono": TEL, "codigo": codigo}).status_code == 401


def test_maximo_de_intentos_bloquea_el_codigo():
    app, repo, canal = _app()
    client = TestClient(app)
    client.post("/api/auth/solicitar", json={"telefono": TEL})
    codigo = _codigo_enviado(canal)
    for _ in range(5):
        client.post("/api/auth/verificar", json={"telefono": TEL, "codigo": "999999"})
    # Aun con el código correcto: bloqueado por intentos.
    assert client.post("/api/auth/verificar", json={"telefono": TEL, "codigo": codigo}).status_code == 401


def test_cooldown_de_reenvio():
    app, repo, canal = _app()
    client = TestClient(app)
    assert client.post("/api/auth/solicitar", json={"telefono": TEL}).status_code == 202
    r = client.post("/api/auth/solicitar", json={"telefono": TEL})
    assert r.status_code == 429
    assert r.json()["reintentar_en"] > 0


def test_salir_revoca_la_sesion():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)
    assert client.post("/api/auth/salir", headers=headers).status_code == 200
    assert client.get("/api/estado", headers=headers).status_code == 401


# --- endpoints protegidos --------------------------------------------------------
def test_endpoints_exigen_sesion():
    app, repo, canal = _app()
    client = TestClient(app)
    assert client.get("/api/estado").status_code == 401
    assert client.post("/api/chat", json={"texto": "hola"}).status_code == 401
    assert client.post("/api/presupuestos", json={"categoria": "comida", "monto_limite": 10}).status_code == 401


def test_chat_corre_el_pipeline_con_identidad_de_la_sesion():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)
    client.post("/api/chat", json={"texto": "hola"}, headers=headers)  # consentimiento
    r = client.post("/api/chat", json={"texto": "prueba"}, headers=headers)
    assert r.json()["respuestas"] == ["eco:prueba"]


def test_estado_aisla_por_sesion():
    """El aislamiento ahora lo garantiza la sesión: sin token del otro número,
    no hay forma de expresar la consulta."""
    app, repo, canal = _app()
    client = TestClient(app)
    otro = repo.get_or_create_user("+593999999999")
    repo.save_transaction(Transaction(
        user_id=otro.id, monto=Decimal("99"), fecha=date.today(),
        categoria="comida", status="confirmada",
    ))
    headers = _login(client, canal)  # sesión de TEL, no del otro
    data = client.get("/api/estado", headers=headers).json()
    assert data["transactions"] == [] and data["resumen"]["gastado_mes"] == 0


def test_crear_presupuesto_y_upsert():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)
    for limite in (200, 250):
        r = client.post("/api/presupuestos", json={"categoria": "Comida", "monto_limite": limite}, headers=headers)
        assert r.status_code == 201
    estado = client.get("/api/estado", headers=headers).json()
    assert len(estado["budgets"]) == 1
    assert estado["budgets"][0]["monto_limite"] == 250.0
    assert estado["budgets"][0]["categoria"] == "comida"


def test_presupuesto_invalido_da_422():
    app, repo, canal = _app()
    client = TestClient(app)
    headers = _login(client, canal)
    r = client.post("/api/presupuestos", json={"categoria": "comida", "monto_limite": -5}, headers=headers)
    assert r.status_code == 422


# --- código maestro de demo ------------------------------------------------------
def test_demo_otp_apagado_por_defecto():
    app, repo, canal = _app(demo_otp="")
    client = TestClient(app)
    client.post("/api/auth/solicitar", json={"telefono": TEL})
    assert client.post("/api/auth/verificar", json={"telefono": TEL, "codigo": ""}).status_code == 401


def test_demo_otp_permite_entrar_si_esta_configurado():
    app, repo, canal = _app(demo_otp="424242")
    client = TestClient(app)
    r = client.post("/api/auth/verificar", json={"telefono": TEL, "codigo": "424242"})
    assert r.status_code == 200 and r.json()["token"]
