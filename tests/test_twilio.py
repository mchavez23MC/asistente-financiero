"""Canal activo: WhatsApp vía Twilio.

Cubre el adaptador (parse de form-encoded, firma HMAC-SHA1) y el webhook
(200 + TwiML, delega el agente a background, ignora eventos sin texto), con
un `FakeProcess` en memoria. No toca la red ni Supabase.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.channels.whatsapp_twilio import WhatsAppTwilioAdapter
from app.domain.models import IncomingMessage
from app.interfaces.api import webhook_twilio


# --- adaptador -----------------------------------------------------------------
def _form(texto: str = " hola ", telefono: str = "+50370000000", nombre: str = "Ana") -> dict:
    """Simula los campos POST que Twilio manda por WhatsApp."""
    return {
        "From": f"whatsapp:{telefono}",
        "To": "whatsapp:+14155238886",
        "Body": texto,
        "ProfileName": nombre,
        "WaId": telefono.lstrip("+"),
        "MessageSid": "SM123",
    }


def test_twilio_parse_normaliza_el_form():
    adapter = WhatsAppTwilioAdapter("ACsid", "token", "+14155238886")
    incoming = adapter.parse(_form(" hola ", nombre="Ana"))
    assert incoming.canal == "whatsapp"
    assert incoming.telefono == "+50370000000"  # se quita el prefijo 'whatsapp:'
    assert incoming.texto == "hola"  # trim
    assert incoming.nombre_perfil == "Ana"


def test_twilio_parse_evento_sin_texto_queda_vacio():
    adapter = WhatsAppTwilioAdapter("ACsid", "token", "+14155238886")
    # Un callback de status (entregado/leído) no trae Body.
    incoming = adapter.parse({"From": "whatsapp:+50370000000", "MessageStatus": "delivered"})
    assert incoming.telefono == "" and incoming.texto == ""


def test_twilio_from_agrega_prefijo_whatsapp():
    assert WhatsAppTwilioAdapter._to_whatsapp("+503700") == "whatsapp:+503700"
    assert WhatsAppTwilioAdapter._to_whatsapp("whatsapp:+503700") == "whatsapp:+503700"


def test_twilio_firma_valida():
    auth_token = "el-auth-token"
    url = "https://demo.ngrok.dev/webhook/whatsapp"
    params = {"Body": "hola", "From": "whatsapp:+50370000000"}
    cadena = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    firma = base64.b64encode(
        hmac.new(auth_token.encode(), cadena.encode(), hashlib.sha1).digest()
    ).decode()
    assert WhatsAppTwilioAdapter.firma_valida(url, params, firma, auth_token)
    assert not WhatsAppTwilioAdapter.firma_valida(url, params, "firma-mala", auth_token)


# --- webhook -------------------------------------------------------------------
class FakeProcess:
    def __init__(self, contexto=None) -> None:
        self.preprocesados: list[IncomingMessage] = []
        self.agentes: list = []
        self._contexto = contexto

    async def preprocess(self, incoming: IncomingMessage):
        self.preprocesados.append(incoming)
        return self._contexto

    async def run_agent(self, context) -> None:
        self.agentes.append(context)


def _app(process: FakeProcess, validar_firma: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.channel = WhatsAppTwilioAdapter("ACsid", "token", "+14155238886")
    app.state.process_message = process
    app.state.twilio_auth_token = "token"
    app.state.twilio_validate_signature = validar_firma
    app.state.public_base_url = "https://demo.ngrok.dev"
    app.include_router(webhook_twilio.router)
    return app


def test_webhook_twilio_200_y_delega_a_background():
    process = FakeProcess(contexto="ctx")
    resp = TestClient(_app(process)).post("/webhook/whatsapp", data=_form("hola"))
    assert resp.status_code == 200
    assert "<Response>" in resp.text  # TwiML vacío
    assert len(process.preprocesados) == 1  # guardrail síncrono antes del 200
    assert process.agentes == ["ctx"]  # agente en background


def test_webhook_twilio_no_lanza_agente_si_preprocess_atendio():
    process = FakeProcess(contexto=None)  # sensible o aviso legal → None
    resp = TestClient(_app(process)).post("/webhook/whatsapp", data=_form("quiero invertir"))
    assert resp.status_code == 200
    assert process.agentes == []


def test_webhook_twilio_ignora_evento_sin_texto():
    process = FakeProcess()
    resp = TestClient(_app(process)).post(
        "/webhook/whatsapp", data={"From": "whatsapp:+50370000000", "MessageStatus": "read"}
    )
    assert resp.status_code == 200
    assert process.preprocesados == []


def test_webhook_twilio_rechaza_firma_invalida():
    process = FakeProcess(contexto="ctx")
    resp = TestClient(_app(process, validar_firma=True)).post(
        "/webhook/whatsapp",
        data=_form("hola"),
        headers={"X-Twilio-Signature": "firma-mala"},
    )
    assert resp.status_code == 403
    assert process.preprocesados == []
