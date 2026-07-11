"""Webhook de WhatsApp — asíncrono desde el día uno (§7.5, no negociable).

Fase 3: `preprocess` (consentimiento + guardrail fail-closed) corre SÍNCRONO
antes de devolver el 200 — un mensaje sensible queda escalado aunque el proceso
muera después. Solo el trabajo del agente (Claude) va en `BackgroundTasks`; la
respuesta al usuario sale por la API REST de Twilio, no por el TwiML.
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
        # Síncrono: consentimiento + guardrail (§7.5). None = ya atendido.
        context = await process_message.preprocess(incoming)
        if context is not None:
            background.add_task(process_message.run_agent, context)

    return Response(content=TWIML_VACIO, media_type="text/xml")
