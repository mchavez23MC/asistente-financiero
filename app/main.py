"""Composition root (§7.1) — Fases 2–9.

Único lugar que conoce las implementaciones concretas: instancia adaptadores
y los inyecta en los casos de uso vía `app.state`. Regla de dependencia: aquí
se importa de todas partes; `domain/` no importa de nadie.

Cablea: webhook de WhatsApp (fase 2), guardrail (fase 3), agente Claude + tools
(fase 4), soporte RAG (fase 5), panel humano (fase 6), scheduler proactivo
(fase 7) y chat web plan B (fase 9).
"""

from __future__ import annotations

import contextlib
import logging

from fastapi import FastAPI

from app.adapters.channels.whatsapp_meta import WhatsAppMetaAdapter
from app.adapters.guardrail.groq_classifier import GroqClassifier
from app.adapters.guardrail.layered import LayeredGuardrail
from app.adapters.llm.claude import ClaudeProvider
from app.adapters.persistence.supabase_repo import SupabaseRepository
from app.application.agents.principal import MainAgent
from app.application.agents.soporte_rag import SoporteRAG
from app.application.process_message import ProcessMessage
from app.application.router import InMemoryAgentRegistry
from app.infra.config import Settings
from app.infra.scheduler import crear_scheduler
from app.interfaces.api import legal, panel, web_chat, webhook

log = logging.getLogger("e5")


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings.from_env()

    # --- adaptadores concretos -------------------------------------------------
    channel = WhatsAppMetaAdapter(
        token=settings.whatsapp_token,
        phone_number_id=settings.whatsapp_phone_number_id,
        graph_version=settings.graph_api_version,
    )
    repo = SupabaseRepository(settings.supabase_url, settings.supabase_key)
    guardrail = LayeredGuardrail(
        classifier=GroqClassifier(settings.groq_api_key, settings.groq_model),
        umbral_confianza=settings.guardrail_umbral_confianza,
        timeout_ms=settings.guardrail_timeout_ms,
        reintentos=settings.guardrail_reintentos,
        backoff_ms=settings.guardrail_backoff_ms,
    )
    claude = ClaudeProvider(
        settings.anthropic_api_key, settings.claude_model, settings.claude_max_tokens
    )
    soporte = SoporteRAG(claude)

    # Agente principal Claude (fase 4) reemplaza al 'eco'.
    registry = InMemoryAgentRegistry()
    registry.register(MainAgent(llm=claude, repo=repo, soporte=soporte))

    process_message = ProcessMessage(
        repo=repo, guardrail=guardrail, registry=registry, channel=channel
    )

    scheduler = crear_scheduler(repo, channel, settings.scheduler_intervalo_min)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.scheduler_habilitado:
            scheduler.start()
            log.info("Scheduler de alertas iniciado (cada %s min).", settings.scheduler_intervalo_min)
        # Precalentar el prompt caching de H3 (§9) sin romper el arranque si falla.
        with contextlib.suppress(Exception):
            await soporte.responder("ping de precalentamiento de caché")
        yield
        if settings.scheduler_habilitado and scheduler.running:
            scheduler.shutdown(wait=False)

    app = FastAPI(
        title="E5 — Asistente financiero",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    # Las páginas legales (/privacidad, /terminos) las sirve legal.router.

    # Puertos compartidos por las interfaces (panel, web chat).
    app.state.channel = channel
    app.state.repo = repo
    app.state.guardrail = guardrail
    app.state.registry = registry
    app.state.process_message = process_message
    app.state.panel_auth = (settings.panel_user, settings.panel_password)
    # Tokens del webhook de Meta (verificación GET + firma del POST).
    app.state.whatsapp_verify_token = settings.whatsapp_verify_token
    app.state.whatsapp_app_secret = settings.whatsapp_app_secret

    app.include_router(webhook.router)
    app.include_router(panel.router)
    app.include_router(web_chat.router)
    app.include_router(legal.router)
    return app


app = create_app()
