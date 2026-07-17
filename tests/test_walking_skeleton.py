"""Fase 2 — walking skeleton con fakes en memoria.

Prueba el pipeline completo del orquestador (consentimiento → guardrail stub →
eco → audit → send) y el webhook asíncrono, sin red ni Supabase. El gate real
(WhatsApp → Fly.io) se verifica a mano contra WhatsApp Cloud API de Meta.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.channels.whatsapp_meta import WhatsAppMetaAdapter
from app.application.process_message import (
    AVISO_ESPERA,
    AVISO_ESPERA_MEDIA,
    AVISO_LEGAL,
    ProcessMessage,
)
from app.application.router import EcoHandler, InMemoryAgentRegistry
from app.domain.models import (
    AgentResult,
    Budget,
    ConversationSummary,
    GuardrailResult,
    IncomingMessage,
    Intencion,
    MediaItem,
    Message,
    Recuerdo,
    Ticket,
    Transaction,
    User,
    UserFact,
)
from app.interfaces.api import webhook


def _coseno(a: list[float], b: list[float]) -> float:
    """Similitud coseno, para que el FakeRepo emule match_* de pgvector."""
    import math

    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


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
        self.categories: set[str] = set()
        self.recurring_incomes: list = []
        self.income_reminders: set[tuple] = set()
        # Memoria semántica (Parte A): vectores por mensaje, hechos y resúmenes.
        self.message_embeddings: dict[UUID, tuple[UUID, list[float]]] = {}
        self.user_facts: list[UserFact] = []
        self.fact_embeddings: dict[UUID, list[float]] = {}
        self.conversation_summaries: list = []

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

    # --- memoria semántica (Parte A) ---
    def save_message_embedding(self, message_id, user_id, embedding) -> None:
        self.message_embeddings[message_id] = (user_id, list(embedding))

    def match_messages(self, user_id, query_embedding, match_count=5, umbral=0.75):
        puntuados = []
        for m in self.messages:
            emb = self.message_embeddings.get(m.id)
            if emb is None or emb[0] != user_id:
                continue
            sim = _coseno(query_embedding, emb[1])
            if sim >= umbral:
                rol = m.rol if isinstance(m.rol, str) else m.rol.value
                puntuados.append(
                    Recuerdo(contenido=m.contenido, origen="mensaje", rol=rol,
                             timestamp=m.timestamp, similitud=sim)
                )
        puntuados.sort(key=lambda r: r.similitud, reverse=True)
        return puntuados[:match_count]

    def match_summaries(self, user_id, query_embedding, match_count=3, umbral=0.72):
        puntuados = []
        for s, vec in self.conversation_summaries:
            if s.user_id != user_id:
                continue
            sim = _coseno(query_embedding, vec)
            if sim >= umbral:
                puntuados.append(
                    Recuerdo(contenido=s.resumen, origen="resumen",
                             timestamp=s.hasta_ts, similitud=sim)
                )
        puntuados.sort(key=lambda r: r.similitud, reverse=True)
        return puntuados[:match_count]

    def usuarios_activos_desde(self, desde):
        return list({m.user_id for m in self.messages if m.timestamp >= desde})

    def get_user_facts(self, user_id, limite=10):
        propios = [f for f in self.user_facts if f.user_id == user_id]
        return list(reversed(propios))[:limite]

    def match_user_facts(self, user_id, query_embedding, match_count=3):
        puntuados = []
        for f in self.user_facts:
            vec = self.fact_embeddings.get(f.id)
            if f.user_id != user_id or vec is None:
                continue
            puntuados.append((f.id, f.contenido, _coseno(query_embedding, vec)))
        puntuados.sort(key=lambda p: p[2], reverse=True)
        return puntuados[:match_count]

    def upsert_user_fact(self, fact, embedding=None):
        if fact.id is not None:
            for i, f in enumerate(self.user_facts):
                if f.id == fact.id:
                    saved = f.model_copy(update={"contenido": fact.contenido, "tipo": fact.tipo})
                    self.user_facts[i] = saved
                    if embedding is not None:
                        self.fact_embeddings[saved.id] = embedding
                    return saved
        saved = fact.model_copy(update={"id": uuid4()})
        self.user_facts.append(saved)
        if embedding is not None:
            self.fact_embeddings[saved.id] = embedding
        return saved

    def save_conversation_summary(self, summary, embedding=None):
        saved = summary.model_copy(update={"id": uuid4()})
        self.conversation_summaries.append((saved, embedding))
        return saved

    def get_ultimo_resumen_ts(self, user_id):
        tss = [s.hasta_ts for s, _ in self.conversation_summaries
               if s.user_id == user_id and s.hasta_ts is not None]
        return max(tss) if tss else None

    def save_transaction(self, transaction: Transaction) -> Transaction:
        if transaction.id is not None:
            self.transactions = [t for t in self.transactions if t.id != transaction.id]
            saved = transaction
        else:
            saved = transaction.model_copy(update={"id": uuid4()})
        self.transactions.append(saved)
        if saved.categoria:
            self.ensure_category(saved.categoria)
        return saved

    def ensure_category(self, nombre: str) -> None:
        nombre = (nombre or "").strip()
        if nombre:
            self.categories.add(nombre)

    def get_categories(self) -> list:
        from app.domain.models import Category

        return [Category(nombre=n) for n in sorted(self.categories)]

    def get_pending_transaction(self, user_id: UUID, tipo: Optional[str] = None) -> Optional[Transaction]:
        estado = "pendiente_confirmacion"
        for t in self.transactions:
            status = t.status if isinstance(t.status, str) else t.status.value
            t_tipo = t.tipo if isinstance(t.tipo, str) else t.tipo.value
            if t.user_id == user_id and status == estado and (tipo is None or t_tipo == tipo):
                return t
        return None

    def list_transactions(
        self, user_id, limite=5, tipo=None, categoria=None, solo_confirmadas=True
    ) -> list[Transaction]:
        propias = []
        # Desempate por orden de inserción: en Windows dos saves consecutivos
        # pueden compartir el mismo created_at (resolución del reloj) y el
        # orden "más reciente primero" quedaría indefinido.
        indexadas = sorted(
            enumerate(self.transactions), key=lambda p: (p[1].created_at, p[0]), reverse=True
        )
        for _, t in indexadas:
            status = t.status if isinstance(t.status, str) else t.status.value
            t_tipo = t.tipo if isinstance(t.tipo, str) else t.tipo.value
            if t.user_id != user_id:
                continue
            if solo_confirmadas and status != "confirmada":
                continue
            if tipo and t_tipo != tipo:
                continue
            if categoria and t.categoria != categoria:
                continue
            propias.append(t)
        return propias[:limite]

    def get_transaction(self, user_id: UUID, transaction_id: UUID) -> Optional[Transaction]:
        for t in self.transactions:
            if t.id == transaction_id and t.user_id == user_id:
                return t
        return None

    def get_budgets(self, user_id: UUID) -> list:
        return [b for b in self.budgets if b.user_id == user_id]

    def save_budget(self, budget: Budget) -> Budget:
        # Upsert por (user, categoria, periodo), como el UNIQUE del schema.
        self.budgets = [
            b for b in self.budgets
            if not (b.user_id == budget.user_id and b.categoria == budget.categoria
                    and b.periodo == budget.periodo)
        ]
        saved = budget.model_copy(update={"id": budget.id or uuid4()})
        self.budgets.append(saved)
        return saved

    def get_all_budgets(self) -> list:
        return list(self.budgets)

    def sum_gastos(self, user_id, categoria=None, periodo=None) -> Decimal:
        return self._sum_por_tipo("gasto", user_id, categoria)

    def sum_ingresos(self, user_id, categoria=None, periodo=None) -> Decimal:
        return self._sum_por_tipo("ingreso", user_id, categoria)

    def _sum_por_tipo(self, tipo, user_id, categoria=None) -> Decimal:
        total = Decimal("0")
        for t in self.transactions:
            status = t.status if isinstance(t.status, str) else t.status.value
            t_tipo = t.tipo if isinstance(t.tipo, str) else t.tipo.value
            if t.user_id != user_id or status != "confirmada" or t.monto is None:
                continue
            if t_tipo != tipo:
                continue
            if categoria and t.categoria != categoria:
                continue
            total += t.monto
        return total

    def create_ticket(self, ticket: Ticket) -> Ticket:
        ticket = ticket.model_copy(update={"id": ticket.id or uuid4()})
        self.tickets[ticket.id] = ticket
        return ticket

    def latest_ticket_at(self, user_id: UUID):
        fechas = [t.created_at for t in self.tickets.values() if t.user_id == user_id]
        return max(fechas) if fechas else None

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

    # --- ingresos recurrentes ---
    def save_recurring_income(self, recurring):
        if recurring.id is not None:
            self.recurring_incomes = [r for r in self.recurring_incomes if r.id != recurring.id]
            saved = recurring
        else:
            saved = recurring.model_copy(update={"id": uuid4()})
        self.recurring_incomes.append(saved)
        return saved

    def get_recurring_incomes(self, user_id: UUID, solo_activos: bool = True) -> list:
        return [
            r for r in self.recurring_incomes
            if r.user_id == user_id and (not solo_activos or r.activo)
        ]

    def get_all_recurring_incomes(self) -> list:
        return [r for r in self.recurring_incomes if r.activo]

    def recordatorio_ya_enviado(self, recurring_id: UUID, periodo_clave: str) -> bool:
        return (recurring_id, periodo_clave) in self.income_reminders

    def marcar_recordatorio(self, recurring_id: UUID, periodo_clave: str) -> None:
        self.income_reminders.add((recurring_id, periodo_clave))
    # --- autenticación de la webapp (OTP + sesiones) ---------------------------
    def save_auth_code(self, code):
        saved = code.model_copy(update={"id": code.id or uuid4()})
        self.auth_codes = getattr(self, "auth_codes", [])
        self.auth_codes.append(saved)
        return saved

    def get_auth_code_activo(self, telefono: str):
        codes = [c for c in getattr(self, "auth_codes", []) if c.telefono == telefono and not c.usado]
        return max(codes, key=lambda c: c.created_at) if codes else None

    def incrementar_intentos_codigo(self, code_id: UUID) -> int:
        for i, c in enumerate(self.auth_codes):
            if c.id == code_id:
                self.auth_codes[i] = c.model_copy(update={"intentos": c.intentos + 1})
                return self.auth_codes[i].intentos
        return 0

    def marcar_codigo_usado(self, code_id: UUID) -> None:
        for i, c in enumerate(self.auth_codes):
            if c.id == code_id:
                self.auth_codes[i] = c.model_copy(update={"usado": True})

    def create_session(self, session):
        self.sessions = getattr(self, "sessions", {})
        self.sessions[session.token_hash] = session
        return session

    def get_session(self, token_hash: str):
        return getattr(self, "sessions", {}).get(token_hash)

    def delete_session(self, token_hash: str) -> None:
        getattr(self, "sessions", {}).pop(token_hash, None)


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


async def test_ia_en_pausa_si_usuario_tiene_ticket_en_proceso():
    """Handoff: con un ticket 'en_proceso', el agente NO corre (lo atiende un
    humano). El mensaje del usuario SÍ se guarda (audit), pero no hay respuesta
    de la IA. Al cerrar el ticket, la IA vuelve a responder."""
    from app.domain.models import Ticket

    process, repo, channel = _pipeline()
    await process(_msg("hola"))  # consentimiento
    user = next(iter(repo.users.values()))
    ticket = repo.create_ticket(Ticket(user_id=user.id, motivo="otro", contexto="x", estado="en_proceso"))

    channel.enviados.clear()
    await process(_msg("¿me ayudas con esto?"))
    # La IA no respondió; el mensaje del usuario quedó guardado (audit intacto).
    assert channel.enviados == []
    assert repo.messages[-1].rol == "user" and repo.messages[-1].contenido == "¿me ayudas con esto?"

    # Cerrado el ticket → la IA retoma.
    repo.update_ticket_estado(ticket.id, "resuelto")
    await process(_msg("gasté 25"))
    assert channel.enviados[-1] == ("+50370000000", "Eco: gasté 25")


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


# --- aviso de espera (agente lento) --------------------------------------------
class HandlerLento:
    """Handler con retardo configurable, para probar el aviso de espera."""

    intent = "principal"

    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    async def handle(self, context) -> AgentResult:
        await asyncio.sleep(self._delay_s)
        return AgentResult(respuesta="listo, registrado ✅", intencion=Intencion.GASTO)


def _pipeline_lento(delay_s: float, umbral_s: float):
    repo, channel = FakeRepo(), FakeChannel()
    registry = InMemoryAgentRegistry()
    registry.register(HandlerLento(delay_s))
    process = ProcessMessage(
        repo, StubGuardrail(), registry, channel, aviso_espera_umbral_s=umbral_s
    )
    return process, repo, channel


async def test_agente_lento_envia_aviso_de_espera_antes_de_responder():
    process, _, channel = _pipeline_lento(delay_s=0.2, umbral_s=0.05)
    await process(_msg("hola"))  # consentimiento; no llega al agente
    channel.enviados.clear()

    await process(_msg("gasté 25 en pupusas"))

    # El aviso sale primero; la respuesta real, después. Nunca al revés.
    textos = [t for _, t in channel.enviados]
    assert textos == [AVISO_ESPERA, "listo, registrado ✅"]


async def test_agente_rapido_no_envia_aviso_de_espera():
    process, _, channel = _pipeline_lento(delay_s=0.0, umbral_s=0.05)
    await process(_msg("hola"))
    channel.enviados.clear()

    await process(_msg("gasté 25 en pupusas"))

    textos = [t for _, t in channel.enviados]
    assert textos == ["listo, registrado ✅"]  # sin aviso: respondió a tiempo


async def test_aviso_de_espera_con_media_menciona_el_adjunto():
    process, _, channel = _pipeline_lento(delay_s=0.2, umbral_s=0.05)
    await process(_msg("hola"))
    channel.enviados.clear()

    incoming = IncomingMessage(
        canal="fake",
        telefono="+50370000000",
        texto="",
        media=[MediaItem(content_type="image/jpeg", data_base64="Zg==")],
    )
    await process(incoming)

    textos = [t for _, t in channel.enviados]
    assert textos[0] == AVISO_ESPERA_MEDIA
    assert textos[-1] == "listo, registrado ✅"


async def test_aviso_de_espera_no_se_audita_ni_entra_al_historial():
    process, repo, _ = _pipeline_lento(delay_s=0.2, umbral_s=0.05)
    await process(_msg("hola"))
    await process(_msg("gasté 25 en pupusas"))

    # El audit trail solo tiene respuestas sustantivas: aviso legal + eco-real.
    # El aviso de espera NO deja fila en `messages` (es UX de transporte).
    asistentes = [m.contenido for m in repo.messages if m.rol == "assistant"]
    assert AVISO_ESPERA not in asistentes
    assert "listo, registrado ✅" in asistentes


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
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": "authtest",
        "TWILIO_WHATSAPP_FROM": "+14155238886",
    }.items():
        monkeypatch.setenv(k, v)

    from app.infra.config import Settings
    from app.main import create_app

    app = create_app(Settings.from_env())
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
