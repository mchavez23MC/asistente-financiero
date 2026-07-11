"""Fase 2 — walking skeleton con fakes en memoria.

Prueba el pipeline completo del orquestador (consentimiento → guardrail stub →
eco → audit → send) y el webhook asíncrono, sin red ni Supabase. El gate real
(WhatsApp → Fly.io) se verifica a mano contra WhatsApp Cloud API de Meta.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.channels.whatsapp_meta import WhatsAppMetaAdapter
from app.application.process_message import AVISO_LEGAL, ProcessMessage
from app.application.router import EcoHandler, InMemoryAgentRegistry
from app.domain.models import (
    Budget,
    GuardrailResult,
    IncomingMessage,
    Message,
    Ticket,
    Transaction,
    User,
)
from app.interfaces.api import webhook


class StubGuardrail:
    """Deja pasar todo — el guardrail real se prueba en test_guardrail.py."""

    async def classify(self, texto: str) -> GuardrailResult:
        return GuardrailResult(sensible=False, fuente="stub")


# --- fakes -------------------------------------------------------------------
class FakeRepo:
    """Repository en memoria, misma semántica que SupabaseRepository."""

    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.messages: list[Message] = []
        self.transactions: list[Transaction] = []
        self.tickets: dict[UUID, Ticket] = {}
        self.budgets: list[Budget] = []
        self.alerts: set[tuple] = set()

    def get_or_create_user(self, telefono: str, nombre: Optional[str] = None) -> User:
        for u in self.users.values():
            if u.telefono == telefono:
                return u
        user = User(id=uuid4(), telefono=telefono, nombre=nombre)
        self.users[user.id] = user
        return user

    def get_user(self, user_id: UUID) -> Optional[User]:
        return self.users.get(user_id)

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

    def recent_messages(self, n: int = 100) -> list[Message]:
        return list(reversed(self.messages))[:n]

    def save_transaction(self, transaction: Transaction) -> Transaction:
        if transaction.id is not None:
            self.transactions = [t for t in self.transactions if t.id != transaction.id]
            saved = transaction
        else:
            saved = transaction.model_copy(update={"id": uuid4()})
        self.transactions.append(saved)
        return saved

    def get_pending_transaction(self, user_id: UUID) -> Optional[Transaction]:
        estado = "pendiente_confirmacion"
        for t in self.transactions:
            status = t.status if isinstance(t.status, str) else t.status.value
            if t.user_id == user_id and status == estado:
                return t
        return None

    def get_budgets(self, user_id: UUID) -> list:
        return [b for b in self.budgets if b.user_id == user_id]

    def get_all_budgets(self) -> list:
        return list(self.budgets)

    def sum_gastos(self, user_id, categoria=None, periodo=None) -> Decimal:
        total = Decimal("0")
        for t in self.transactions:
            status = t.status if isinstance(t.status, str) else t.status.value
            if t.user_id != user_id or status != "confirmada" or t.monto is None:
                continue
            if categoria and t.categoria != categoria:
                continue
            total += t.monto
        return total

    def create_ticket(self, ticket: Ticket) -> Ticket:
        ticket = ticket.model_copy(update={"id": ticket.id or uuid4()})
        self.tickets[ticket.id] = ticket
        return ticket

    def list_tickets(self, estado: Optional[str] = None) -> list[Ticket]:
        vals = list(self.tickets.values())
        if estado:
            vals = [t for t in vals if (t.estado if isinstance(t.estado, str) else t.estado.value) == estado]
        return sorted(vals, key=lambda t: t.created_at, reverse=True)

    def get_ticket(self, ticket_id: UUID) -> Optional[Ticket]:
        return self.tickets.get(ticket_id)

    def update_ticket_estado(self, ticket_id: UUID, estado: str) -> Ticket:
        t = self.tickets[ticket_id].model_copy(update={"estado": estado})
        self.tickets[ticket_id] = t
        return t

    def alerta_ya_enviada(self, budget_id: UUID, periodo_clave: str) -> bool:
        return (budget_id, periodo_clave) in self.alerts

    def marcar_alerta(self, user_id: UUID, budget_id: UUID, periodo_clave: str) -> None:
        self.alerts.add((budget_id, periodo_clave))


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


# --- adaptador Meta (WhatsApp Cloud API) -----------------------------------------
def _meta_payload(texto: str, telefono: str = "50370000000", nombre: str = "Ana") -> dict:
    """Simula el webhook JSON de Meta con un mensaje de texto."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {
            "contacts": [{"profile": {"name": nombre}, "wa_id": telefono}],
            "messages": [{"from": telefono, "id": "wamid.x", "type": "text", "text": {"body": texto}}],
        }}]}],
    }


def test_meta_parse_normaliza_el_json():
    adapter = WhatsAppMetaAdapter("token", "1234567890")
    incoming = adapter.parse(_meta_payload(" hola ", nombre="Ana"))
    assert incoming.canal == "whatsapp"
    assert incoming.telefono == "+50370000000"  # Meta manda sin '+', se normaliza a E.164
    assert incoming.texto == "hola"
    assert incoming.nombre_perfil == "Ana"


def test_meta_parse_status_update_sin_mensaje_queda_vacio():
    adapter = WhatsAppMetaAdapter("token", "1234567890")
    status = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "statuses": [{"id": "wamid.x", "status": "delivered"}]}}]}]}
    incoming = adapter.parse(status)
    assert incoming.telefono == "" and incoming.texto == ""


# --- webhook: verificación GET + 200 inmediato + background ------------------------
class FakeProcess:
    """Registra qué corrió síncrono (preprocess) y qué en background (run_agent)."""

    def __init__(self, contexto=None) -> None:
        self.preprocesados: list[IncomingMessage] = []
        self.agentes: list = []
        self._contexto = contexto

    async def preprocess(self, incoming: IncomingMessage):
        self.preprocesados.append(incoming)
        return self._contexto

    async def run_agent(self, context) -> None:
        self.agentes.append(context)


def _app_webhook(process: FakeProcess) -> FastAPI:
    app = FastAPI()
    app.state.channel = WhatsAppMetaAdapter("token", "1234567890")
    app.state.process_message = process
    app.state.whatsapp_verify_token = "vt"
    app.state.whatsapp_app_secret = ""  # sin firma en tests
    app.include_router(webhook.router)
    return app


def test_webhook_verificacion_meta_devuelve_challenge():
    resp = TestClient(_app_webhook(FakeProcess())).get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"},
    )
    assert resp.status_code == 200 and resp.text == "42"


def test_webhook_verificacion_rechaza_token_malo():
    resp = TestClient(_app_webhook(FakeProcess())).get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "malo", "hub.challenge": "42"},
    )
    assert resp.status_code == 403


def test_webhook_responde_200_y_delega_el_agente_a_background():
    process = FakeProcess(contexto="ctx")
    resp = TestClient(_app_webhook(process)).post("/webhook/whatsapp", json=_meta_payload("hola"))
    assert resp.status_code == 200
    assert len(process.preprocesados) == 1  # guardrail síncrono antes del 200
    assert process.agentes == ["ctx"]  # el agente corrió en background


def test_webhook_no_lanza_agente_si_preprocess_atendio_el_mensaje():
    process = FakeProcess(contexto=None)  # sensible o aviso legal → None
    resp = TestClient(_app_webhook(process)).post(
        "/webhook/whatsapp", json=_meta_payload("quiero invertir")
    )
    assert resp.status_code == 200
    assert len(process.preprocesados) == 1
    assert process.agentes == []


def test_webhook_ignora_evento_sin_mensaje():
    process = FakeProcess()
    resp = TestClient(_app_webhook(process)).post("/webhook/whatsapp", json={"object": "x", "entry": []})
    assert resp.status_code == 200
    assert process.preprocesados == []


# --- composition root ---------------------------------------------------------------
def test_health_del_composition_root(monkeypatch):
    for k, v in {
        "ANTHROPIC_API_KEY": "test",
        "GROQ_API_KEY": "test",
        "SUPABASE_URL": "http://supabase.invalid",
        "SUPABASE_KEY": "test",
        "WHATSAPP_TOKEN": "EAAtest",
        "WHATSAPP_PHONE_NUMBER_ID": "1234567890",
        "WHATSAPP_VERIFY_TOKEN": "vt",
    }.items():
        monkeypatch.setenv(k, v)

    from app.infra.config import Settings
    from app.main import create_app

    app = create_app(Settings.from_env())
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
