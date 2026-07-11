"""Webhook de WhatsApp — asíncrono desde el día uno (§7.5, no negociable).

Twilio recibe un 200 con TwiML vacío de inmediato; el pipeline completo corre
en `BackgroundTasks` y la respuesta al usuario sale por la API REST de Twilio.
Nota fase 3: cuando el guardrail sea real, su clasificación se moverá síncrona
antes del 200 — solo el trabajo de Claude queda en background.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request, Response

router = APIRouter()

# TwiML vacío: "recibido, no respondo por aquí" (la respuesta va por REST).
TWIML_VACIO = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request, background: BackgroundTasks) -> Response:
    form = await request.form()
    channel = request.app.state.channel
    process_message = request.app.state.process_message

    incoming = channel.parse(dict(form))
    if incoming.telefono and incoming.texto:
        background.add_task(process_message, incoming)

    return Response(content=TWIML_VACIO, media_type="text/xml")
