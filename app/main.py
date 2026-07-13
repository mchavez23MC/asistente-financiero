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
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.adapters.channels.whatsapp_twilio import WhatsAppTwilioAdapter
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
from app.application.auth import AuthService
from app.interfaces.api import legal, pages, panel, web_chat, webapp_api, webhook_twilio

log = logging.getLogger("e5")


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings.from_env()

    # --- adaptadores concretos -------------------------------------------------
    # Canal activo: Twilio. (El adaptador de Meta se conserva en
    # app/adapters/channels/whatsapp_meta.py + interfaces/api/webhook.py como
    # ejemplo para empresas con acceso directo a la WhatsApp Cloud API.)
    channel = WhatsAppTwilioAdapter(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token,
        whatsapp_from=settings.twilio_whatsapp_from,
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
    # Auth de la webapp: OTP por WhatsApp (mismo canal Twilio) + sesiones.
    app.state.auth = AuthService(repo, channel, demo_otp=settings.auth_demo_otp)
    app.state.panel_auth = (settings.panel_user, settings.panel_password)
    # Validación de firma del webhook de Twilio (X-Twilio-Signature).
    app.state.twilio_auth_token = settings.twilio_auth_token
    app.state.twilio_validate_signature = settings.twilio_validate_signature
    app.state.public_base_url = settings.public_base_url

    app.include_router(webhook_twilio.router)
    app.include_router(panel.router)
    app.include_router(web_chat.router)
    app.include_router(legal.router)
    app.include_router(webapp_api.router)
    # Vistas de la webapp como endpoints con URL limpia (sin .html) — ver
    # app/interfaces/api/pages.py. Solo los assets css/js van como estáticos.
    app.include_router(pages.router)
    assets_dir = Path(__file__).resolve().parents[1] / "webapp" / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    return app


app = create_app()
