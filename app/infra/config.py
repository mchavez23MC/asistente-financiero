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
    # WhatsApp vía Twilio — canal ACTIVO.
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_from: str
    twilio_validate_signature: bool
    public_base_url: str
    # WhatsApp Cloud API (Meta) — conservado como EJEMPLO (no se cablea). Opcional.
    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str
    whatsapp_app_secret: str
    graph_api_version: str
    guardrail_umbral_confianza: float
    guardrail_timeout_ms: int
    guardrail_reintentos: int
    guardrail_backoff_ms: int
    claude_max_tokens: int
    panel_user: str
    panel_password: str
    scheduler_habilitado: bool
    scheduler_intervalo_min: int
    aviso_espera_umbral_s: float
    # Código maestro de OTP para demos (vacío = apagado). NUNCA en producción.
    auth_demo_otp: str
    # Memoria semántica (Parte A). VOYAGE_API_KEY vacío = memoria apagada: el
    # asistente usa solo la ventana reciente (comportamiento previo).
    voyage_api_key: str
    voyage_model: str
    memoria_top_k: int
    memoria_umbral: float
    memoria_jobs_intervalo_min: int
    memoria_inactividad_horas: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            anthropic_api_key=_req("ANTHROPIC_API_KEY"),
            claude_model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5"),
            groq_api_key=_req("GROQ_API_KEY"),
            groq_model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
            supabase_url=_req("SUPABASE_URL"),
            supabase_key=_req("SUPABASE_KEY"),
            # Twilio (canal activo): requerido.
            twilio_account_sid=_req("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_req("TWILIO_AUTH_TOKEN"),
            twilio_whatsapp_from=_req("TWILIO_WHATSAPP_FROM"),
            twilio_validate_signature=os.environ.get(
                "TWILIO_VALIDATE_SIGNATURE", "false"
            ).lower()
            in ("1", "true", "yes"),
            public_base_url=os.environ.get("PUBLIC_BASE_URL", ""),
            # Meta (ejemplo, no se cablea): opcional, defaults vacíos.
            whatsapp_token=os.environ.get("WHATSAPP_TOKEN", ""),
            whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
            whatsapp_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
            whatsapp_app_secret=os.environ.get("WHATSAPP_APP_SECRET", ""),
            graph_api_version=os.environ.get("GRAPH_API_VERSION", "v21.0"),
            guardrail_umbral_confianza=float(
                os.environ.get("GUARDRAIL_UMBRAL_CONFIANZA", "0.7")
            ),
            guardrail_timeout_ms=int(os.environ.get("GUARDRAIL_TIMEOUT_MS", "1200")),
            guardrail_reintentos=int(os.environ.get("GUARDRAIL_REINTENTOS", "1")),
            guardrail_backoff_ms=int(os.environ.get("GUARDRAIL_BACKOFF_MS", "300")),
            claude_max_tokens=int(os.environ.get("CLAUDE_MAX_TOKENS", "1024")),
            panel_user=os.environ.get("PANEL_USER", "admin"),
            panel_password=os.environ.get("PANEL_PASSWORD", "cambiar-esto"),
            scheduler_habilitado=os.environ.get("SCHEDULER_HABILITADO", "true").lower()
            in ("1", "true", "yes"),
            scheduler_intervalo_min=int(os.environ.get("SCHEDULER_INTERVALO_MIN", "30")),
            aviso_espera_umbral_s=float(os.environ.get("AVISO_ESPERA_UMBRAL_S", "2.0")),
            auth_demo_otp=os.environ.get("AUTH_DEMO_OTP", ""),
            voyage_api_key=os.environ.get("VOYAGE_API_KEY", ""),
            voyage_model=os.environ.get("VOYAGE_MODEL", "voyage-3.5-lite"),
            memoria_top_k=int(os.environ.get("MEMORIA_TOP_K", "5")),
            memoria_umbral=float(os.environ.get("MEMORIA_UMBRAL", "0.75")),
            memoria_jobs_intervalo_min=int(os.environ.get("MEMORIA_JOBS_INTERVALO_MIN", "360")),
            memoria_inactividad_horas=int(os.environ.get("MEMORIA_INACTIVIDAD_HORAS", "6")),
        )
