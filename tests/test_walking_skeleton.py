"""Fase 2 — walking skeleton con fakes en memoria.

Prueba el pipeline completo del orquestador (consentimiento → guardrail stub →
eco → audit → send) y el webhook asíncrono, sin red ni Supabase. El gate real
(WhatsApp → Fly.io) se verifica a mano contra el sandbox de Twilio.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.channels.whatsapp_twilio import WhatsAppTwilioAdapter
from app.application.process_message import AVISO_LEGAL, ProcessMessage, StubGuardrail
from app.application.router import EcoHandler, InMemoryAgentRegistry
from app.domain.models import IncomingMessage, Message, Ticket, Transaction, User
from app.interfaces.api import webhook


# --- fakes -------------------------------------------------------------------
class FakeRepo:
    """Repository en memoria, misma semántica que SupabaseRepository."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.messages: list[Message] = []
        self.transactions: list[Transaction] = []
        self.tickets: list[Ticket] = []

    def get_or_create_user(self, telefono: str, nombre: Optional[str] = None) -> User:
        for u in self.users.values():
            if u.telefono == telefono:
                return u
        user = User(id=uuid4(), telefono=telefono, nombre=nombre)
        self.users[user.id] = user
        return user

    def registrar_consentimiento(self, user_id: UUID) -> User:
        user = self.users[user_id].model_copy(
            update={"consentimiento_at": datetime.now(timezone.utc)}
        )
        self.users[user_id] = user
        return user

    def save_message(self, message: Message) -> Message:
        message = message.model_copy(update={"id": uuid4()})
        self.messages.append(message)
        return message

    def get_last_n_messages(self, user_id: UUID, n: int = 10) -> list[Message]:
        propios = [m for m in self.messages if m.user_id == user_id]
        return propios[-n:]

    def save_transaction(self, transaction: Transaction) -> Transaction:
        self.transactions.append(transaction)
        return transaction

    def get_pending_transaction(self, user_id: UUID) -> Optional[Transaction]:
        return None

    def get_budgets(self, user_id: UUID) -> list:
        return []

    def sum_gastos(self, user_id, categoria=None, periodo=None) -> Decimal:
        return Decimal("0")

    def create_ticket(self, ticket: Ticket) -> Ticket:
        self.tickets.append(ticket)
        return ticket


class FakeChannel:
    canal = "fake"

    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    def parse(self, payload: dict) -> IncomingMessage:
        return IncomingMessage(canal=self.canal, telefono=payload["telefono"], texto=payload["texto"])

    async def send(self, user: User, text: str) -> None:
        self.enviados.append((user.telefono, text))


def _pipeline() -> tuple[ProcessMessage, FakeRepo, FakeChannel]:
    repo, channel = FakeRepo(), FakeChannel()
    registry = InMemoryAgentRegistry()
    registry.register(EcoHandler())
    return ProcessMessage(repo, StubGuardrail(), registry, channel), repo, channel


def _msg(texto: str) -> IncomingMessage:
    return IncomingMessage(canal="fake", telefono="+50370000000", texto=texto)


# --- orquestador ---------------------------------------------------------------
async def test_usuario_nuevo_recibe_aviso_legal_y_queda_con_consentimiento():
    process, repo, channel = _pipeline()
    await process(_msg("hola"))

    user = next(iter(repo.users.values()))
    assert user.tiene_consentimiento
    assert channel.enviados == [("+50370000000", AVISO_LEGAL)]
    # Audit: entrada del usuario + aviso con intención 'consentimiento'.
    assert [m.rol for m in repo.messages] == ["user", "assistant"]
    assert repo.messages[-1].intencion == "consentimiento"


async def test_segundo_mensaje_pasa_por_eco_y_queda_auditado():
    process, repo, channel = _pipeline()
    await process(_msg("hola"))
    await process(_msg("gasté 25 en pupusas"))

    assert channel.enviados[-1] == ("+50370000000", "Eco: gasté 25 en pupusas")
    # 4 filas: (user, aviso) + (user, eco). El eco lleva intención 'otro'.
    assert len(repo.messages) == 4
    assert repo.messages[-1].rol == "assistant"
    assert repo.messages[-1].intencion == "otro"
    # Un solo usuario, sin duplicados por teléfono.
    assert len(repo.users) == 1


async def test_historial_llega_al_handler():
    process, repo, _ = _pipeline()
    await process(_msg("hola"))
    await process(_msg("uno"))
    await process(_msg("dos"))

    historial = repo.get_last_n_messages(next(iter(repo.users)), 10)
    assert len(historial) == 6  # 3 turnos × (user + assistant)


# --- adaptador Twilio ------------------------------------------------------------
def test_twilio_parse_normaliza_el_form():
    adapter = WhatsAppTwilioAdapter("AC123", "token", "whatsapp:+14155238886")
    incoming = adapter.parse(
        {"From": "whatsapp:+50370000000", "Body": " hola ", "ProfileName": "Ana"}
    )
    assert incoming.canal == "whatsapp"
    assert incoming.telefono == "+50370000000"
    assert incoming.texto == "hola"
    assert incoming.nombre_perfil == "Ana"


# --- webhook: 200 inmediato + background ------------------------------------------
def test_webhook_responde_200_y_procesa_en_background():
    app = FastAPI()
    procesados: list[IncomingMessage] = []

    async def fake_process(incoming: IncomingMessage) -> None:
        procesados.append(incoming)

    app.state.channel = WhatsAppTwilioAdapter("AC123", "token", "whatsapp:+1415")
    app.state.process_message = fake_process
    app.include_router(webhook.router)

    client = TestClient(app)
    resp = client.post(
        "/webhook/whatsapp",
        data={"From": "whatsapp:+50370000000", "Body": "hola"},
    )
    assert resp.status_code == 200
    assert "<Response>" in resp.text  # TwiML vacío: la respuesta va por REST
    assert len(procesados) == 1 and procesados[0].texto == "hola"


def test_webhook_ignora_payload_vacio():
    app = FastAPI()
    procesados = []

    async def fake_process(incoming) -> None:
        procesados.append(incoming)

    app.state.channel = WhatsAppTwilioAdapter("AC123", "token", "whatsapp:+1415")
    app.state.process_message = fake_process
    app.include_router(webhook.router)

    resp = TestClient(app).post("/webhook/whatsapp", data={"From": "", "Body": ""})
    assert resp.status_code == 200
    assert procesados == []


# --- composition root ---------------------------------------------------------------
def test_health_del_composition_root(monkeypatch):
    for k, v in {
        "ANTHROPIC_API_KEY": "test",
        "GROQ_API_KEY": "test",
        "SUPABASE_URL": "http://supabase.invalid",
        "SUPABASE_KEY": "test",
        "TWILIO_ACCOUNT_SID": "AC123",
        "TWILIO_AUTH_TOKEN": "test",
    }.items():
        monkeypatch.setenv(k, v)

    from app.infra.config import Settings
    from app.main import create_app

    app = create_app(Settings.from_env())
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
