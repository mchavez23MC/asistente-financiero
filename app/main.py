"""Composition root (§7.1) — Fase 2.

Único lugar que conoce las implementaciones concretas: instancia adaptadores
y los inyecta en el caso de uso vía `app.state`. Regla de dependencia: aquí
se importa de todas partes; `domain/` no importa de nadie.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.adapters.channels.whatsapp_twilio import WhatsAppTwilioAdapter
from app.adapters.persistence.supabase_repo import SupabaseRepository
from app.application.process_message import ProcessMessage, StubGuardrail
from app.application.router import EcoHandler, InMemoryAgentRegistry
from app.infra.config import Settings
from app.interfaces.api import webhook


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="E5 — Asistente financiero", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    if settings is None:
        settings = Settings.from_env()

    channel = WhatsAppTwilioAdapter(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        from_number=settings.twilio_whatsapp_from,
    )
    repo = SupabaseRepository(settings.supabase_url, settings.supabase_key)

    registry = InMemoryAgentRegistry()
    registry.register(EcoHandler())  # fase 4 lo reemplaza por el agente Claude

    app.state.channel = channel
    app.state.repo = repo
    app.state.process_message = ProcessMessage(
        repo=repo,
        guardrail=StubGuardrail(),  # fase 3 lo reemplaza por denylist+Groq
        registry=registry,
        channel=channel,
    )

    app.include_router(webhook.router)
    return app


app = create_app()
