"""Webhook de WhatsApp Cloud API (Meta) — asíncrono desde el día uno (§7.5).

Meta usa la MISMA URL para dos cosas:
- GET  /webhook/whatsapp  → handshake de verificación (hub.challenge) al
  registrar el webhook en el panel de Meta Developers.
- POST /webhook/whatsapp  → eventos (mensajes y statuses) en JSON.

Fase 3 se conserva: `preprocess` (consentimiento + guardrail fail-closed) corre
SÍNCRONO antes del 200; solo el trabajo del agente (Claude) va en background y
la respuesta sale por la Graph API, no en el cuerpo del webhook.

Seguridad: si hay `app_secret`, se valida la firma `X-Hub-Signature-256` sobre
el cuerpo crudo (HMAC-SHA256). Sin secret configurado, se omite (dev/sandbox).
"""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.get("/webhook/whatsapp")
async def verificar(request: Request) -> Response:
    """Handshake de verificación de Meta: devuelve hub.challenge si el token
    coincide con el configurado en el panel de Meta Developers."""
    params = request.query_params
    modo = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if modo == "subscribe" and token == request.app.state.whatsapp_verify_token:
        return PlainTextResponse(challenge or "")
    return Response(status_code=403)


@router.post("/webhook/whatsapp")
async def recibir(request: Request, background: BackgroundTasks) -> Response:
    raw = await request.body()
    if not _firma_valida(request, raw):
        return Response(status_code=403)

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return Response(status_code=400)

    channel = request.app.state.channel
    process_message = request.app.state.process_message

    incoming = channel.parse(payload)
    if incoming.telefono and incoming.texto:
        # Síncrono: consentimiento + guardrail (§7.5). None = ya atendido.
        context = await process_message.preprocess(incoming)
        if context is not None:
            background.add_task(process_message.run_agent, context)

    # Meta espera 200 rápido para no reintentar el evento.
    return Response(status_code=200)


def _firma_valida(request: Request, raw: bytes) -> bool:
    """Valida X-Hub-Signature-256 contra el app secret. Sin secret → se omite."""
    secret = request.app.state.whatsapp_app_secret
    if not secret:
        return True
    recibido = request.headers.get("X-Hub-Signature-256", "")
    esperado = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(recibido, esperado)
