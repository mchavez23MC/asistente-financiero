"""Webhook de WhatsApp vía Twilio — canal ACTIVO (§7.5).

A diferencia de Meta (ver `webhook.py`, conservado como ejemplo), Twilio:
- NO hace un handshake GET de verificación: se configura la Callback URL en la
  consola de Twilio y listo. Aquí solo hay POST.
- Postea `application/x-www-form-urlencoded` (no JSON).
- Espera una respuesta TwiML (XML). Como la respuesta real del asistente sale
  por la REST API en background, se devuelve un TwiML vacío (`<Response/>`) para
  que Twilio no envíe nada por su cuenta.

Se mantiene la partición de §7.5: `preprocess` (consentimiento + guardrail
fail-closed) corre SÍNCRONO antes del 200; solo el trabajo del agente va en
background.

Seguridad: si `twilio_validate_signature` está activo, se valida
`X-Twilio-Signature` (HMAC-SHA1) contra la URL pública y los params POST. Requiere
`PUBLIC_BASE_URL` bien configurado (debe coincidir con la Callback URL de Twilio).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.adapters.channels.whatsapp_twilio import WhatsAppTwilioAdapter

router = APIRouter()

# TwiML vacío: acusa recibo sin que Twilio mande una respuesta automática.
_TWIML_VACIO = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/webhook/whatsapp")
async def recibir(request: Request, background: BackgroundTasks) -> Response:
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    if not _firma_valida(request, params):
        return Response(status_code=403)

    channel = request.app.state.channel
    process_message = request.app.state.process_message

    incoming = channel.parse(params)
    if incoming.telefono and (incoming.texto or incoming.media):
        # Síncrono: consentimiento + guardrail (§7.5). None = ya atendido.
        context = await process_message.preprocess(incoming)
        if context is not None:
            if incoming.media:
                # La descarga de adjuntos (Basic Auth de Twilio) también va en
                # background: el 200 no espera a la media ni al agente.
                background.add_task(_descargar_media_y_correr, channel, process_message, context)
            else:
                background.add_task(process_message.run_agent, context)

    # Twilio espera 200 rápido (TwiML) para no reintentar el evento.
    return Response(content=_TWIML_VACIO, media_type="application/xml")


async def _descargar_media_y_correr(channel, process_message, context) -> None:
    """Background: baja los adjuntos del mensaje (fetch_media es tolerante a
    fallos por adjunto) y recién ahí decide: la ingesta de documentos (si está
    activa) puede atender el mensaje (menú A–F, duplicado, respaldo); si no, el
    agente arma los bloques image/document para Claude como siempre."""
    await channel.fetch_media(context.incoming)
    await process_message.atender_media_o_agente(context)


def _firma_valida(request: Request, params: dict) -> bool:
    """Valida X-Twilio-Signature si está habilitado. Deshabilitado → se omite
    (útil en dev con URLs de túnel cambiantes)."""
    if not getattr(request.app.state, "twilio_validate_signature", False):
        return True
    auth_token = request.app.state.twilio_auth_token
    base = str(request.app.state.public_base_url or "").rstrip("/")
    url = f"{base}{request.url.path}"
    firma = request.headers.get("X-Twilio-Signature", "")
    return WhatsAppTwilioAdapter.firma_valida(url, params, firma, auth_token)
