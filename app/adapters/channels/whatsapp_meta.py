"""ChannelAdapter sobre WhatsApp Cloud API de Meta (§7.1).

Reemplaza a Twilio: el núcleo no cambia (misma interfaz `ChannelAdapter`), solo
este adaptador y el handshake del webhook. Entrada: webhook de Meta (JSON con
`entry[].changes[].value.messages[]`) → `IncomingMessage` canónico. Salida: Graph
API (`POST /{phone_number_id}/messages`) con Bearer token.

Notas de Meta:
- El `from`/`wa_id` viene SIN el '+'; se normaliza a E.164 (`+` + dígitos).
- El webhook trae también `statuses` (entregado/leído) sin `messages`: esos
  eventos se parsean a un `IncomingMessage` vacío y el webhook los ignora.
- Ventana de 24h: fuera de ella solo se pueden enviar plantillas aprobadas.
"""

from __future__ import annotations

import httpx

from app.domain.models import IncomingMessage, User

GRAPH_API_BASE = "https://graph.facebook.com"


class WhatsAppMetaAdapter:
    canal = "whatsapp"

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        graph_version: str = "v21.0",
    ) -> None:
        self._token = token
        self._url = f"{GRAPH_API_BASE}/{graph_version}/{phone_number_id}/messages"

    def parse(self, payload: dict) -> IncomingMessage:
        """Normaliza el webhook de Meta al formato canónico. Toma el primer
        mensaje de texto; si no hay (p.ej. un status update), devuelve vacío."""
        value = self._primer_value(payload)
        mensajes = value.get("messages") or []
        contactos = value.get("contacts") or []
        nombre = None
        if contactos:
            nombre = (contactos[0].get("profile") or {}).get("name")

        if not mensajes:
            return IncomingMessage(canal=self.canal, telefono="", texto="", raw=payload)

        m = mensajes[0]
        telefono = str(m.get("from", ""))
        if telefono and not telefono.startswith("+"):
            telefono = "+" + telefono
        texto = ""
        if m.get("type") == "text":
            texto = str((m.get("text") or {}).get("body", "")).strip()

        return IncomingMessage(
            canal=self.canal,
            telefono=telefono,
            texto=texto,
            nombre_perfil=nombre,
            raw=payload,
        )

    async def send(self, user: User, text: str) -> None:
        # Meta espera el número sin '+' (formato wa_id).
        to = user.telefono.lstrip("+")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": "text",
                    "text": {"body": text},
                },
            )
            resp.raise_for_status()

    @staticmethod
    def _primer_value(payload: dict) -> dict:
        try:
            return payload["entry"][0]["changes"][0]["value"] or {}
        except (KeyError, IndexError, TypeError):
            return {}
