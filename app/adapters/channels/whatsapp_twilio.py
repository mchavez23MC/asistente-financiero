"""ChannelAdapter sobre el sandbox de WhatsApp de Twilio — Fase 2 (§7.1).

Entrada: webhook de Twilio (form-urlencoded) → `IncomingMessage` canónico.
Salida: API REST de Twilio. La respuesta NO va en el TwiML del webhook porque
el procesamiento es asíncrono (§7.5): el webhook devuelve 200 vacío y la
respuesta sale después por esta API.
"""

from __future__ import annotations

import httpx

from app.domain.models import IncomingMessage, User

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


class WhatsAppTwilioAdapter:
    canal = "whatsapp"

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._account_sid = account_sid
        self._auth = (account_sid, auth_token)
        # Formato 'whatsapp:+14155238886' (número del sandbox).
        self._from = from_number

    def parse(self, payload: dict) -> IncomingMessage:
        """Normaliza el form del webhook de Twilio al formato canónico.

        Campos de Twilio: From='whatsapp:+503...', Body, ProfileName.
        """
        telefono = str(payload.get("From", "")).removeprefix("whatsapp:")
        return IncomingMessage(
            canal=self.canal,
            telefono=telefono,
            texto=str(payload.get("Body", "")).strip(),
            nombre_perfil=payload.get("ProfileName") or None,
            raw=dict(payload),
        )

    async def send(self, user: User, text: str) -> None:
        url = f"{TWILIO_API_BASE}/Accounts/{self._account_sid}/Messages.json"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                auth=self._auth,
                data={
                    "From": self._from,
                    "To": f"whatsapp:{user.telefono}",
                    "Body": text,
                },
            )
            resp.raise_for_status()
