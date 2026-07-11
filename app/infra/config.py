"""Configuración desde entorno (§8.1). Las claves viven en .env (local) o en el
vault de Fly.io (`fly secrets set`) — nunca en la imagen ni en el repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:  # carga .env en local; en Fly.io las vars ya vienen inyectadas
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _req(nombre: str) -> str:
    v = os.environ.get(nombre)
    if not v:
        raise RuntimeError(f"Falta la variable de entorno requerida: {nombre}")
    return v


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    claude_model: str
    groq_api_key: str
    groq_model: str
    supabase_url: str
    supabase_key: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_from: str
    guardrail_umbral_confianza: float
    guardrail_timeout_ms: int
    claude_max_tokens: int
    panel_user: str
    panel_password: str
    scheduler_habilitado: bool
    scheduler_intervalo_min: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            anthropic_api_key=_req("ANTHROPIC_API_KEY"),
            claude_model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5"),
            groq_api_key=_req("GROQ_API_KEY"),
            groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
            supabase_url=_req("SUPABASE_URL"),
            supabase_key=_req("SUPABASE_KEY"),
            twilio_account_sid=_req("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_req("TWILIO_AUTH_TOKEN"),
            twilio_whatsapp_from=os.environ.get("TWILIO_WHATSAPP_FROM", ""),
            guardrail_umbral_confianza=float(
                os.environ.get("GUARDRAIL_UMBRAL_CONFIANZA", "0.7")
            ),
            guardrail_timeout_ms=int(os.environ.get("GUARDRAIL_TIMEOUT_MS", "800")),
            claude_max_tokens=int(os.environ.get("CLAUDE_MAX_TOKENS", "1024")),
            panel_user=os.environ.get("PANEL_USER", "admin"),
            panel_password=os.environ.get("PANEL_PASSWORD", "cambiar-esto"),
            scheduler_habilitado=os.environ.get("SCHEDULER_HABILITADO", "true").lower()
            in ("1", "true", "yes"),
            scheduler_intervalo_min=int(os.environ.get("SCHEDULER_INTERVALO_MIN", "30")),
        )
